import logging
from datetime import date

import pandas as pd

from src.classification.auto_tagger import AutoTagger
from src.database.connector import DatabaseConnector
from src.financial_analysis.cost_basis import CostBasisCalculator
from src.identity.authority_resolver import AuthorityResolver
from src.services.transaction_source_selector import select_transaction_sources
from src.sync.phases._common import (
    STALE_READER_SHADOW_DAYS,
    READER_HOLDING_SOURCES,
    HISTORICAL_HOLDING_SOURCES,
    LEGACY_HOLDING_SOURCES,
    NON_TRADABLE_HOLDING_SOURCES,
)

logger = logging.getLogger(__name__)

#: price_source stamped on rows written by _tombstone_empty_verified_sources.
EMPTY_SOURCE_TOMBSTONE_PRICE_SOURCE = "empty_source_tombstone"


def _shadow_stale_reader_holdings(
    connector: DatabaseConnector,
    empty_verified_sources: "set | None" = None,
) -> int:
    """Shadow stale reader holdings only when they are old and fully liquidated.

    Rules:
    - Candidate rows must be older than source latest snapshot by > STALE_READER_SHADOW_DAYS.
    - Candidate asset must have post-snapshot liquidation signal:
      a post-snapshot sell exists, no later post-snapshot buy exists, and net post-snapshot
      quantity change is <= 0.
    This protects lagging assets (e.g. QDII T+1/T+2) from accidental shadowing.

    Args:
        empty_verified_sources: sources that ran, were verified, and yielded zero
            rows this sync (see SyncResult.empty_verified_sources). Their history
            is FROZEN: `_tombstone_empty_verified_sources` has already written the
            zero that makes the source's position go away, and shadowing the last
            qty-bearing snapshot on top of that would strip the source's newest
            qty-bearing row of its active status — which integrity check #6
            (`shadow_mutual_exclusion`, BLOCKING) reads as "reader data is
            invisible". See the module note on `_tombstone_empty_verified_sources`.
    """
    frozen = set(empty_verified_sources or ())
    shadowed_total = 0
    for source in READER_HOLDING_SOURCES:
        if source in frozen:
            logger.info(
                "_shadow_stale_reader_holdings: source=%s reported an empty (verified) file — "
                "history frozen, the zero is carried by its tombstone", source,
            )
            continue
        latest_date_row = connector.execute(
            "SELECT MAX(snapshot_date) FROM holdings WHERE source_system = ? AND is_shadow = FALSE",
            (source,)
        ).fetchone()

        if latest_date_row and latest_date_row[0]:
            latest_date = latest_date_row[0]
            res = connector.execute(
                f"""
                WITH candidate_rows AS (
                    SELECT
                        h.asset_id,
                        h.snapshot_date,
                        MAX(
                            CASE
                                WHEN LOWER(t.transaction_type) IN ('sell', 'adjustment_sell')
                                 AND COALESCE(t.quantity, 0) != 0
                                 AND t.transaction_date > h.snapshot_date
                                THEN t.transaction_date
                            END
                        ) AS last_post_sell,
                        MAX(
                            CASE
                                WHEN LOWER(t.transaction_type) IN (
                                    'buy', 'adjustment_buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment'
                                )
                                 AND COALESCE(t.quantity, 0) != 0
                                 AND t.transaction_date > h.snapshot_date
                                THEN t.transaction_date
                            END
                        ) AS last_post_buy,
                        SUM(
                            CASE
                                WHEN t.transaction_date <= h.snapshot_date THEN 0
                                WHEN LOWER(t.transaction_type) IN (
                                    'buy', 'adjustment_buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment'
                                ) THEN ABS(COALESCE(t.quantity, 0))
                                WHEN LOWER(t.transaction_type) IN ('sell', 'adjustment_sell')
                                  THEN -ABS(COALESCE(t.quantity, 0))
                                ELSE 0
                            END
                        ) AS net_post_snapshot_qty
                    FROM holdings h
                    LEFT JOIN transactions t ON t.asset_id = h.asset_id
                    WHERE h.source_system = ?
                      AND h.is_shadow = FALSE
                      AND h.snapshot_date < (? - INTERVAL '{STALE_READER_SHADOW_DAYS} day')
                    GROUP BY h.asset_id, h.snapshot_date
                ),
                to_shadow AS (
                    SELECT asset_id, snapshot_date
                    FROM candidate_rows
                    WHERE last_post_sell IS NOT NULL
                      AND (last_post_buy IS NULL OR last_post_buy <= last_post_sell)
                      AND COALESCE(net_post_snapshot_qty, 0) <= 0
                )
                UPDATE holdings
                SET is_shadow = TRUE
                WHERE source_system = ?
                  AND is_shadow = FALSE
                  AND (asset_id, snapshot_date) IN (
                      SELECT asset_id, snapshot_date FROM to_shadow
                  )
                RETURNING asset_id
                """,
                (source, latest_date, source)
            )
            shadowed_total += len(res.fetchall())
    return shadowed_total


def _tombstone_empty_verified_sources(
    connector: DatabaseConnector,
    empty_verified_sources: "set | None" = None,
    as_of_date=None,
) -> int:
    """Write a zero tombstone for every asset of a source that verifiably reported nothing.

    **The defect this closes (task #16).** `_shadow_stale_non_tradable_holdings`
    shadows rows whose `snapshot_date < MAX(snapshot_date)` for their source. When a
    source emits **no rows at all**, `MAX` never advances: the previous rows sit
    exactly *at* that date, `<` is false, and the whole last snapshot stays active
    forever. Reproduced on an in-memory DuckDB: two `Gold_Excel` rows at a fixed
    snapshot date with no new rows → 0 rows shadowed, the stale market value still
    counted. Absence was
    indistinguishable from "no update" — the same "invisible states" failure class as
    the V7.8.1 Financial-Summary blank-column phantom.

    **The signal.** A source is only ever passed in here when it satisfies BOTH
    conditions: its reader returned `READ_STATUS_OK` (artifact located, format
    validator passed, parse did not raise) AND it produced zero holdings rows. A
    missing workbook, a disabled reader, a failed validator or a raised exception all
    keep the source out of the set, so those cases keep the existing rows untouched
    and merely warn. Zeroing a live portfolio because a file was still uploading is
    the far more damaging error, so the ambiguous case is loud, never destructive.

    **Why a zero row and not `is_shadow`.** Following the V7.8.1 precedent, and
    because shadowing does not actually work here: integrity check #6
    (`shadow_mutual_exclusion`, BLOCKING) inspects every reader row at that source's
    newest **qty-bearing** snapshot_date and fails if such a row is shadowed without
    a `Consolidated` supersession. For an empty source that newest qty-bearing row IS
    the last real snapshot, so shadowing it trips the gate. An active
    `quantity = 0, market_value = 0` row dated later wins every per-asset
    `MAX(snapshot_date)` query instead, drops the asset out of net worth (which
    filters `market_value > 0`), and leaves check #6 satisfied. Checks #5
    (`active_holdings_have_positive_value`, `< 0` only) and #10
    (`no_extreme_single_asset_change`, `market_value > 0` only) already tolerate
    zero-valued rows.

    Idempotent: an asset whose latest active row is already a zero is skipped, so a
    source that stays empty for weeks does not accrue one row per day. Re-ingest is
    self-healing — when the workbook comes back, `_upsert_holdings` writes a fresh
    dated row and the normal sweeps resume (the source is no longer in the set).

    Args:
        connector: DatabaseConnector instance.
        empty_verified_sources: source_system names proven to have run and be empty.
        as_of_date: tombstone snapshot_date (defaults to date.today()). Pass an
            explicit value in tests for deterministic output.

    Returns:
        Number of tombstone rows written.
    """
    sources = set(empty_verified_sources or ())
    if not sources:
        return 0

    if as_of_date is None:
        as_of_date = date.today()

    written = 0
    for source in sorted(sources):
        # Rule 3: per-(asset, source) latest — never a global MAX(snapshot_date).
        rows = connector.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings
                WHERE source_system = ? AND is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT h.asset_id, h.asset_name, h.asset_type, h.unit, h.currency,
                   h.account, h.snapshot_date, h.market_value
            FROM holdings h
            JOIN latest_per_asset l
              ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
            WHERE h.source_system = ?
              AND h.is_shadow = FALSE
              AND (COALESCE(h.quantity, 0) != 0 OR COALESCE(h.market_value, 0) != 0)
            """,
            (source, source),
        ).fetchall()

        if not rows:
            logger.info(
                "_tombstone_empty_verified_sources: source=%s reported empty and has no "
                "non-zero active holdings — nothing to tombstone", source,
            )
            continue

        for asset_id, asset_name, asset_type, unit, currency, account, prior_date, prior_mv in rows:
            connector.execute(
                """
                INSERT INTO holdings (
                    snapshot_date, asset_id, asset_name, asset_type,
                    quantity, unit, cost_price_unit, market_price_unit, market_value,
                    currency, account, source_system, price_source, is_shadow
                ) VALUES (?, ?, ?, ?, 0, ?, 0, 0, 0, ?, ?, ?, ?, FALSE)
                ON CONFLICT (snapshot_date, asset_id, source_system) DO UPDATE SET
                    quantity = 0,
                    cost_price_unit = 0,
                    market_price_unit = 0,
                    market_value = 0,
                    price_source = EXCLUDED.price_source,
                    is_shadow = FALSE
                """,
                (
                    as_of_date, asset_id, asset_name, asset_type, unit,
                    currency, account, source, EMPTY_SOURCE_TOMBSTONE_PRICE_SOURCE,
                ),
            )
            written += 1
            logger.warning(
                "_tombstone_empty_verified_sources: source=%s reported ZERO holdings — "
                "zeroing asset=%s (was %s at %s) with a tombstone at %s",
                source, asset_id, prior_mv, prior_date, as_of_date,
            )

    return written


def _shadow_stale_non_tradable_holdings(
    connector: DatabaseConnector,
    empty_verified_sources: "set | None" = None,
) -> int:
    """Shadow stale holdings for non-tradable sources (Insurance, RSU, Gold, etc.).

    Since these sources do not have regular buy/sell transactions, they cannot use the
    transaction-based liquidation signal in _shadow_stale_reader_holdings.
    However, they represent complete snapshots of the current portfolio. Therefore,
    any holding with a snapshot date older than the latest snapshot date of that source
    should be marked as shadow (is_shadow = TRUE) to prevent double counting.

    Args:
        empty_verified_sources: sources that ran, were verified, and yielded zero rows
            this sync. They are SKIPPED here — `_tombstone_empty_verified_sources` has
            already written the zero, and letting this sweep run would shadow the
            source's newest qty-bearing row against integrity check #6. See that
            function's docstring for the full argument.
    """
    frozen = set(empty_verified_sources or ())
    shadowed_total = 0
    for source in NON_TRADABLE_HOLDING_SOURCES:
        if source in frozen:
            logger.info(
                "_shadow_stale_non_tradable_holdings: source=%s reported an empty (verified) "
                "file — history frozen, the zero is carried by its tombstone", source,
            )
            continue
        # Find global latest snapshot date for this source
        latest_date_row = connector.execute(
            "SELECT MAX(snapshot_date) FROM holdings WHERE source_system = ? AND is_shadow = FALSE",
            (source,)
        ).fetchone()

        if latest_date_row and latest_date_row[0]:
            latest_date = latest_date_row[0]
            # Shadow any older holdings of this source
            res = connector.execute(
                """
                UPDATE holdings
                SET is_shadow = TRUE
                WHERE source_system = ?
                  AND is_shadow = FALSE
                  AND snapshot_date < ?
                RETURNING asset_id
                """,
                (source, latest_date)
            ).fetchall()
            shadowed_total += len(res)
    return shadowed_total


def _auto_register_new_assets(connector: DatabaseConnector) -> int:
    """Automatically register newly seen assets from holdings and transactions in asset_registry.

    This ensures that when a new asset is imported via reader files (e.g. Schwab, CN Fund, Insurance),
    it is registered so that it shows up in the classification/audit pages and can be classified.
    """
    # 1. Query holdings for assets not in asset_registry (excluding UNKNOWN_)
    holdings_assets = connector.execute("""
        SELECT DISTINCT asset_id, asset_name, source_system
        FROM holdings
        WHERE asset_id NOT IN (SELECT canonical_id FROM asset_registry)
          AND asset_id NOT LIKE 'UNKNOWN_%'
    """).fetchall()

    # 2. Query transactions for assets not in asset_registry (excluding UNKNOWN_)
    txn_assets = connector.execute("""
        SELECT DISTINCT asset_id, asset_name, source_system
        FROM transactions
        WHERE asset_id NOT IN (SELECT canonical_id FROM asset_registry)
          AND asset_id NOT LIKE 'UNKNOWN_%'
    """).fetchall()

    # Combine them, prioritizing holdings info if present
    missing_assets = {}
    for aid, name, src in holdings_assets + txn_assets:
        if aid not in missing_assets or (name and not missing_assets[aid]['name']):
            missing_assets[aid] = {'name': name, 'source': src}

    if not missing_assets:
        return 0

    registered_count = 0

    for asset_id, info in missing_assets.items():
        name = info['name'] or asset_id
        source = info['source']

        # Determine base currency
        if asset_id.startswith(('US_', 'RSU_')) or asset_id == 'CASH_USD':
            base_currency = 'USD'
        else:
            base_currency = 'CNY'

        # Insert into asset_registry as pending
        connector.execute("""
            INSERT INTO asset_registry (
                canonical_id, display_name, base_currency, is_active, is_pending, created_at, updated_at
            ) VALUES (?, ?, ?, TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (canonical_id) DO NOTHING
        """, (asset_id, name, base_currency))

        # Insert into asset_source_mappings
        connector.execute("""
            INSERT INTO asset_source_mappings (
                canonical_id, source_system, source_id, mapping_type, created_at
            ) VALUES (?, ?, ?, 'auto', CURRENT_TIMESTAMP)
            ON CONFLICT (source_system, source_id) DO NOTHING
        """, (asset_id, source, asset_id))

        registered_count += 1

    # Run auto-tagger to classify newly registered assets if rules match
    try:
        tagger = AutoTagger(connector)
        tagger.classify_registry(connector)
    except Exception as e:
        logger.warning(f"AutoTagger failed after auto-registration: {e}")

    return registered_count


def _shadow_stale_historical_holdings(connector: DatabaseConnector) -> int:
    """Mark historical holdings as shadow if they are older than the latest snapshot PER ASSET.

    This is used for sources like Financial_Summary_Excel where multiple historical snapshots
    are loaded, but only the most recent one for each specific asset (e.g. Wealth_CMB)
    should be considered active.
    """
    shadowed_total = 0
    for source in HISTORICAL_HOLDING_SOURCES:
        # Find latest date per asset for this source
        rows = connector.execute(
            """
            UPDATE holdings
            SET is_shadow = TRUE
            WHERE source_system = ?
              AND is_shadow = FALSE
              AND (asset_id, snapshot_date) NOT IN (
                  SELECT asset_id, MAX(snapshot_date)
                  FROM holdings
                  WHERE source_system = ? AND is_shadow = FALSE
                  GROUP BY asset_id
              )
            RETURNING asset_id
            """,
            (source, source)
        ).fetchall()
        shadowed_total += len(rows)
    return shadowed_total


def _shadow_coauthority_tombstone(connector: DatabaseConnector, as_of_date=None) -> int:
    """Shadow stale broker holdings for assets that left via ACAT transfer (no sell signal).

    Background (ACAT gap): When an asset transfers from Schwab to IBKR via ACAT, Schwab simply
    omits the asset from the next CSV file — there is no sell transaction. The standard stale-reader
    shadow phase (`_shadow_stale_reader_holdings`) requires a sell signal and therefore misses this
    case, leaving a stale Schwab row active and causing a double-count alongside the IBKR row.

    This phase detects co-authority broker sources (those appearing in any rule with ≥2 declared
    authorities, e.g. {Schwab_CSV, Broker_IBKR}) and tombstones assets that are present in an older
    snapshot of that source but absent from the source's latest file.

    Scope: co-authority broker sources ONLY. CN funds, Gold, Insurance, RSU are deliberately excluded
    — QDII funds legitimately lag 2+ days, and using this logic on them would cause false positives.
    (Those single-authority sources will not appear in the co-authority broker set anyway, making this
    a defense-in-depth guard.)

    Integrity gate: check #6 (`shadow_mutual_exclusion`) exempts zero-qty tombstone rows and rows
    superseded by a Consolidated source, so the tombstone itself does not trip the gate.

    Args:
        connector: DatabaseConnector instance.
        as_of_date: The tombstone snapshot_date (defaults to date.today()). Pass an explicit value
                    in tests for deterministic output.

    Returns:
        Total number of stale active rows shadowed (tombstone INSERTs are not counted).
    """
    if as_of_date is None:
        as_of_date = date.today()

    resolver = AuthorityResolver()

    # Derive co-authority broker sources: sources that appear in any rule with ≥2 declared authorities.
    coauth_sources = resolver.coauthority_sources()

    if not coauth_sources:
        logger.info("_shadow_coauthority_tombstone: no co-authority broker sources found — skipping")
        return 0

    logger.info(
        "_shadow_coauthority_tombstone: co-authority broker sources = %s, as_of_date = %s",
        coauth_sources,
        as_of_date,
    )

    shadowed_total = 0

    for source in sorted(coauth_sources):  # sorted for deterministic ordering
        # (a) Find the latest file date for this source
        row = connector.execute(
            "SELECT MAX(snapshot_date) FROM holdings WHERE source_system = ? AND is_shadow = FALSE",
            (source,),
        ).fetchone()
        if not row or row[0] is None:
            logger.debug("_shadow_coauthority_tombstone: source=%s has no active holdings, skipping", source)
            continue

        latest_file_date = row[0]

        # (b) Find dropped candidates: rows from OLDER snapshots whose asset_id is NOT in the latest file
        dropped_rows = connector.execute(
            """
            SELECT DISTINCT h.asset_id, h.asset_name, h.asset_type, h.currency, h.account, h.unit
            FROM holdings h
            WHERE h.source_system = ?
              AND h.is_shadow = FALSE
              AND h.snapshot_date < ?
              AND h.asset_id NOT IN (
                  SELECT DISTINCT asset_id
                  FROM holdings
                  WHERE source_system = ?
                    AND is_shadow = FALSE
                    AND snapshot_date = ?
              )
            """,
            (source, latest_file_date, source, latest_file_date),
        ).fetchall()

        if not dropped_rows:
            logger.debug(
                "_shadow_coauthority_tombstone: source=%s latest=%s — no dropped candidates",
                source,
                latest_file_date,
            )
            continue

        for row in dropped_rows:
            asset_id, asset_name, asset_type, currency, account, unit = row

            # (c) Confirm the asset is genuinely co-authority (defense-in-depth)
            auth_set = resolver.resolve_authorities(asset_id)  # no available_sources → full rule set
            if source not in auth_set or len(auth_set) < 2:
                logger.debug(
                    "_shadow_coauthority_tombstone: asset=%s source=%s not confirmed co-authority "
                    "(auth_set=%s) — skipping",
                    asset_id,
                    source,
                    auth_set,
                )
                continue

            # (e) Shadow the stale active row(s) for this asset in this source
            shadow_res = connector.execute(
                """
                UPDATE holdings
                SET is_shadow = TRUE
                WHERE source_system = ?
                  AND asset_id = ?
                  AND is_shadow = FALSE
                  AND snapshot_date < ?
                RETURNING asset_id
                """,
                (source, asset_id, latest_file_date),
            ).fetchall()
            shadowed_count = len(shadow_res)
            shadowed_total += shadowed_count

            if shadowed_count:
                logger.info(
                    "_shadow_coauthority_tombstone: shadowed %d stale row(s) for asset=%s source=%s",
                    shadowed_count,
                    asset_id,
                    source,
                )

            # (f) Write a current-dated tombstone if not already present (idempotent)
            existing = connector.execute(
                """
                SELECT 1 FROM holdings
                WHERE asset_id = ?
                  AND source_system = ?
                  AND snapshot_date = ?
                LIMIT 1
                """,
                (asset_id, source, as_of_date),
            ).fetchone()

            if not existing:
                connector.execute(
                    """
                    INSERT INTO holdings (
                        snapshot_date, asset_id, asset_name, asset_type,
                        quantity, unit, cost_price_unit, market_price_unit, market_value,
                        currency, account, source_system, price_source, is_shadow
                    ) VALUES (?, ?, ?, ?, 0, ?, 0, 0, 0, ?, ?, ?, 'coauthority_tombstone', TRUE)
                    """,
                    (as_of_date, asset_id, asset_name, asset_type, unit, currency, account, source),
                )
                logger.info(
                    "_shadow_coauthority_tombstone: inserted tombstone for asset=%s source=%s date=%s",
                    asset_id,
                    source,
                    as_of_date,
                )

    return shadowed_total


def _consolidate_coauthority_holdings(connector: DatabaseConnector, as_of_date=None) -> int:
    """Materialize one merged holdings row per co-authority asset (C3.4).

    Background: production current-holdings queries (`data.py`, `context_generator.py`, etc.)
    all do `SELECT asset_id, MAX(snapshot_date) ... WHERE is_shadow=FALSE GROUP BY asset_id` —
    i.e. they pick the single newest-dated source per asset, they do NOT sum two brokers.
    For a co-authority asset held at both Schwab and IBKR (e.g. SGOV), that under-counts the
    true position. Rather than rewrite ~35 queries, this phase writes ONE merged
    `source_system='Consolidated'` row per co-authority asset dated `as_of_date` (>= all
    contributing source dates, so the existing MAX(snapshot_date) queries pick it up) and
    shadows the contributing per-broker rows so they are not double-counted.

    Securities: qty = Σqty, market_value = Σmarket_value, market_price_unit = the most
    recent broker's native per-unit price, cost = merged-lifetime-FIFO total_cost / Σqty
    (via `select_transaction_sources` + `CostBasisCalculator`, mirroring
    `_backfill_fifo_cost_basis`'s exact df-build pattern. IBKR's transferred-in lots carry
    cost 0, so a naive per-broker cost sum would be wrong — the merged ledger is required.

    Cash (`CASH_%`): qty = 1 (sentinel — cash is not a share count, so it is NOT summed),
    market_value = Σmarket_value, market_price_unit = Σ(per-row USD balances),
    cost = market_price_unit (cash P&L is always ~0).

    Self-correcting: any existing active Consolidated row for a co-authority asset is
    shadowed first, then rebuilt from the latest broker data — so a stale Consolidated row
    from a prior run (e.g. before a broker dropped an asset) never lingers as active.

    Idempotent: re-running with the same as_of_date and unchanged broker data produces the
    same Consolidated row (delete-then-insert under the same UNIQUE(snapshot_date, asset_id,
    source_system) key) and re-shadows the same broker rows (UPDATE is a no-op on rows
    already is_shadow=TRUE).

    Rule 3 compliance: every "latest" lookup here is per-(asset, source), NEVER a global
    MAX(snapshot_date) — see `_common.py` and AGENTS.md Rule 3.

    Args:
        connector: DatabaseConnector instance.
        as_of_date: The Consolidated row's snapshot_date (defaults to date.today()). Pass an
                    explicit value in tests for deterministic output.

    Returns:
        Total number of contributing broker rows shadowed by this run.
    """
    if as_of_date is None:
        as_of_date = date.today()

    resolver = AuthorityResolver()
    coauth_sources = resolver.coauthority_sources()

    if not coauth_sources:
        logger.info("_consolidate_coauthority_holdings: no co-authority broker sources — skipping")
        return 0

    # Step 2: self-correcting — shadow any stale active Consolidated rows before rebuilding.
    connector.execute(
        """
        UPDATE holdings
        SET is_shadow = TRUE
        WHERE source_system = 'Consolidated'
          AND is_shadow = FALSE
        """
    )

    coauth_list = ", ".join(f"'{s}'" for s in sorted(coauth_sources))

    # Step 3: find co-authority asset_ids with >=2 active broker rows, each at its own
    # per-(asset, source) latest snapshot_date (Rule 3 — never a global MAX).
    latest_rows = connector.execute(
        f"""
        WITH latest_per_asset_source AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
              AND source_system IN ({coauth_list})
            GROUP BY asset_id, source_system
        )
        SELECT h.asset_id, h.source_system, h.quantity, h.market_value,
               h.market_price_unit, h.currency, h.account, h.asset_name,
               h.asset_type, h.unit, h.snapshot_date
        FROM holdings h
        JOIN latest_per_asset_source lpas
          ON h.asset_id = lpas.asset_id
         AND h.source_system = lpas.source_system
         AND h.snapshot_date = lpas.max_date
        WHERE h.is_shadow = FALSE
          AND COALESCE(h.quantity, 0) != 0
        """
    ).fetchall()

    # Group rows by asset_id
    by_asset: dict = {}
    for row in latest_rows:
        asset_id = row[0]
        by_asset.setdefault(asset_id, []).append(row)

    shadowed_total = 0

    for asset_id, rows in sorted(by_asset.items()):
        if len(rows) < 2:
            continue  # single-source co-authority asset (after C3.2) — leave as-is

        # Confirm the asset is genuinely co-authority (defense-in-depth, mirrors C3.2).
        auth_set = resolver.resolve_authorities(asset_id)
        if len(auth_set) < 2:
            logger.debug(
                "_consolidate_coauthority_holdings: asset=%s has %d active broker rows but "
                "auth_set=%s is not co-authority — skipping",
                asset_id, len(rows), auth_set,
            )
            continue

        is_cash = asset_id.startswith("CASH_")

        cons_qty: float
        cons_price: float

        if is_cash:
            # Cash: qty=1 sentinel (do NOT sum — cash is a balance, not a share count).
            cons_qty = 1.0
            cons_mv = sum(float(r[3] or 0) for r in rows)
            # market_price_unit = sum of per-row USD balances.
            cons_price = sum(float(r[4] or 0) for r in rows)
            cons_cost = cons_price  # cash P&L is always ~0
        else:
            cons_qty = sum(float(r[2] or 0) for r in rows)
            cons_mv = sum(float(r[3] or 0) for r in rows)
            # market_price_unit = native price of the row with the MAX snapshot_date.
            latest_row = max(rows, key=lambda r: r[10])
            cons_price = float(latest_row[4] or 0)

            cons_cost = None
            try:
                selected_sources = select_transaction_sources(connector, asset_id, resolver=resolver)
                if selected_sources:
                    placeholders = ", ".join(["?"] * len(selected_sources))
                    tx_rows = connector.execute(
                        f"""
                        SELECT transaction_type, quantity, price_unit, amount_net,
                               currency, transaction_date
                        FROM transactions
                        WHERE asset_id = ?
                          AND source_system IN ({placeholders})
                        ORDER BY transaction_date ASC
                        """,
                        [asset_id, *selected_sources],
                    ).fetchall()
                    if tx_rows:
                        df = pd.DataFrame(
                            tx_rows,
                            columns=[
                                "transaction_type",
                                "quantity",
                                "price_unit",
                                "amount_net",
                                "currency",
                                "transaction_date",
                            ],
                        )
                        df["transaction_date"] = pd.to_datetime(df["transaction_date"])
                        df.set_index("transaction_date", inplace=True)

                        calc = CostBasisCalculator(asset_id)
                        calc.process_transactions(df)

                        total_cost = calc.get_total_cost_basis()
                        remaining_qty = calc.get_current_position()
                        if remaining_qty > 0:
                            cons_cost = round(total_cost / remaining_qty, 8)
            except Exception as e:
                logger.warning(
                    "_consolidate_coauthority_holdings: merged-FIFO cost calc failed for asset=%s: %s",
                    asset_id, e,
                )
                cons_cost = None

        # Use any row's static fields (currency/account/name/type/unit) — currency is
        # asset-level, not source-level, for co-authority assets (both brokers in USD).
        sample = rows[0]
        currency, account, asset_name, asset_type, unit = (
            sample[5], "Multi-broker", sample[7], sample[8], sample[9]
        )

        # Step 5: idempotent insert — delete-then-insert under the same
        # (snapshot_date, asset_id, 'Consolidated') UNIQUE key.
        connector.execute(
            """
            DELETE FROM holdings
            WHERE snapshot_date = ? AND asset_id = ? AND source_system = 'Consolidated'
            """,
            (as_of_date, asset_id),
        )
        connector.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, asset_name, asset_type,
                quantity, unit, cost_price_unit, market_price_unit, market_value,
                currency, account, source_system, authority_source, price_source, is_shadow
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Consolidated', 'Consolidated', 'consolidated', FALSE)
            """,
            (
                as_of_date, asset_id, asset_name, asset_type,
                cons_qty, unit, cons_cost, cons_price, cons_mv,
                currency, account,
            ),
        )

        # Step 6: shadow the contributing broker rows.
        for row in rows:
            src_source = row[1]
            src_date = row[10]
            res = connector.execute(
                """
                UPDATE holdings
                SET is_shadow = TRUE
                WHERE asset_id = ?
                  AND source_system = ?
                  AND snapshot_date = ?
                  AND is_shadow = FALSE
                RETURNING asset_id
                """,
                (asset_id, src_source, src_date),
            ).fetchall()
            shadowed_total += len(res)

        logger.info(
            "_consolidate_coauthority_holdings: consolidated asset=%s from %d broker rows "
            "(qty=%s, mv=%s, cost=%s) at %s",
            asset_id, len(rows), cons_qty, cons_mv, cons_cost, as_of_date,
        )

    return shadowed_total


def _shadow_legacy_holdings(connector: DatabaseConnector, reader_sources: set[str] = None) -> int:
    """Mark legacy PIS holdings as shadow when a reader source exists for the same asset.

    Args:
        connector: DB connector
        reader_sources: Set of source systems that should supersede legacy sources.
                       If None, uses global READER_HOLDING_SOURCES.
    """
    if reader_sources is None:
        reader_sources = READER_HOLDING_SOURCES

    legacy_list = ", ".join(f"'{source}'" for source in LEGACY_HOLDING_SOURCES)
    reader_list = ", ".join(f"'{source}'" for source in reader_sources)

    rows = connector.execute(
        f"""
        SELECT DISTINCT leg.asset_id, leg.source_system
        FROM holdings leg
        WHERE leg.is_shadow = FALSE
          AND leg.source_system IN ({legacy_list})
          AND EXISTS (
              SELECT 1 FROM holdings rdr
              WHERE rdr.asset_id = leg.asset_id
                AND rdr.is_shadow = FALSE
                AND rdr.source_system IN ({reader_list})
          )
        """,
    ).fetchall()

    if not rows:
        return 0

    connector.execute(
        f"""
        UPDATE holdings
        SET is_shadow = TRUE
        WHERE is_shadow = FALSE
          AND source_system IN ({legacy_list})
          AND (asset_id, snapshot_date) IN (
              SELECT DISTINCT leg.asset_id, leg.snapshot_date
              FROM holdings leg
              WHERE leg.is_shadow = FALSE
                AND leg.source_system IN ({legacy_list})
                AND EXISTS (
                    SELECT 1 FROM holdings rdr
                    WHERE rdr.asset_id = leg.asset_id
                      AND rdr.is_shadow = FALSE
                      AND rdr.source_system IN ({reader_list})
                )
          )
        """,
    )
    return len(rows)
