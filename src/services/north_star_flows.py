"""North Star F3.1 — cash-flow classification (PRD 2026-07-07, Batch B6).

Split out of src/services/north_star.py to keep each file under the 400-line
guideline; north_star.py re-exports everything here and is the intended
import surface for routes/tests.

D6 — classification is additive tagging on cash_flow_tags, never a rewrite of
transactions/income_expense_monthly. classify_flows_heuristic only ever
assigns 'internal_transfer' — the one classification inferable with high
confidence from same-day pairing (matched transfer_in/transfer_out; a
same-day SGOV/money-market leg switched into/out of another asset).
'external_contribution' and 'income_reinvested' are manual-only (PUT
/north-star/flows/tag) — the system never guesses "this is new money"
(Cross-Cutting Requirement 3).

Candidate universe for contribution classification ("flow rows"):
  - transactions with transaction_type IN ('transfer_in', 'transfer_out')
  - transactions with transaction_type IN ('buy', 'sell') whose asset_id
    matches a liquidity-parking pattern (SGOV — same pattern rule_buckets.py
    uses for the 'liquidity' bucket)

income_expense_monthly rows are EXCLUDED from the classifier scope: they are
Excel monthly-summary calculations, not actual transactions. They must never
appear in the unclassified list, the classified list, or the contribution
counts. Only 'transactions' rows are flow candidates.

Anything in the candidate universe with no cash_flow_tags row is "unclassified"
and is surfaced with a visible count — never silently bucketed either way.

WS-1 (plan 2026-07-20-fs-cash-flows-attribution.md) extends the candidate
universe with a second source: Financial-Summary cash/deposit accounts
(source_system='Financial_Summary_Excel', asset_id CASH_* or Wealth_CMB) are
stored as monthly BALANCES in holdings, not transactions — their
month-over-month deltas are real cash flows but are invisible to the
transactions-only scan above. fs_cash_flow_candidates() computes those deltas
on the fly (nothing written to holdings/transactions) and tags live under
cash_flow_tags.source_table='fs_cash_delta' with a stable natural key
(`fscash:{asset_id}|{YYYY-MM}`) — manual-only, same as external_contribution/
income_reinvested elsewhere in this module (the system never guesses "new
money").
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

from src.services.currency import get_today_usd_cny_rate

_LIQUIDITY_ASSET_PATTERNS = ("SGOV",)
_VALID_CLASSIFICATIONS = ("external_contribution", "internal_transfer", "income_reinvested")
_AMOUNT_TOLERANCE_CNY = 1.0
_AMOUNT_TOLERANCE_PCT = 0.02


def _is_liquidity_asset(asset_id: Optional[str]) -> bool:
    aid = (asset_id or "").upper()
    return any(p in aid for p in _LIQUIDITY_ASSET_PATTERNS)


# ── V81 — stable natural key for cash_flow_tags.source_row_key ─────────────
# _replace_transactions (src/sync/phases/_ingest.py) deletes and reinserts
# transactions rows on every sync — ids regenerate — so a tag keyed on
# transactions.id orphans on every re-import. The natural key below is
# EXACTLY the identity _ingest.py's incremental delete-match uses
# (transaction_date, asset_id, transaction_type, amount_gross, source_system),
# so a tag keyed this way survives re-import by construction: the row that
# gets deleted-and-reinserted keeps the same 5-tuple identity, hence the same
# natural key. Single choke point — every read/write path in this module
# that touches cash_flow_tags.source_row_key for source_table='transactions'
# goes through compose_natural_key / parse_natural_key / is_natural_key /
# _resolve_transactions_row so the format only needs to be right in one place.
_NK_PREFIX = "nk:"


def is_natural_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(_NK_PREFIX)


def compose_natural_key(
    source_system: Any, transaction_date: Any, asset_id: Any, transaction_type: Any, amount_gross: Any,
) -> str:
    """Build the stable `nk:` key for a transactions row.

    transaction_date may be a date object (typical, straight from a DuckDB
    fetch) or an ISO string (already parsed). amount_gross is formatted to
    2dp (transactions.amount_gross is DECIMAL(20,2)) with None treated as 0,
    matching the COALESCE(amount_gross, 0) semantics of the delete-match
    predicate in _ingest.py.
    """
    date_str = transaction_date.isoformat() if hasattr(transaction_date, "isoformat") else str(transaction_date)
    amt = float(amount_gross) if amount_gross is not None else 0.0
    if amt == 0.0:
        amt = 0.0  # normalize -0.0 -> 0.0 so a sign-flipped zero never composes a different key
    return f"{_NK_PREFIX}{source_system}|{date_str}|{asset_id}|{transaction_type}|{amt:.2f}"


def parse_natural_key(key: str) -> Optional[dict]:
    """Inverse of compose_natural_key. Returns None if `key` is not a
    well-formed nk: key (caller treats that the same as an unresolvable row)."""
    if not is_natural_key(key):
        return None
    body = key[len(_NK_PREFIX):]
    parts = body.split("|")
    if len(parts) != 5:
        return None
    source_system, date_str, asset_id, transaction_type, amount_str = parts
    try:
        amount_gross = float(amount_str)
    except ValueError:
        return None
    return {
        "source_system": source_system,
        "transaction_date": date_str,
        "asset_id": asset_id,
        "transaction_type": transaction_type,
        "amount_gross": amount_gross,
    }


def _resolve_transactions_row(db, source_row_key: str) -> Optional[dict]:
    """Resolve a cash_flow_tags.source_row_key (source_table='transactions')
    back to its live transactions row.

    Supports both the legacy numeric-id key (pre-V81 tags, or any tag not
    yet touched since) and the stable `nk:` key. Returns None when the row
    cannot be resolved — a permanently orphaned tag (its transaction was
    deleted/changed and no live row matches), or a malformed key. This is
    the single resolution choke point every read/write path uses; a row this
    function can't resolve is, by construction, an orphan.
    """
    skey = str(source_row_key)
    if is_natural_key(skey):
        parsed = parse_natural_key(skey)
        if parsed is None:
            return None
        row = db.execute(
            """
            SELECT id, transaction_date, asset_id, transaction_type, amount_net,
                   amount_gross, currency, source_system
            FROM transactions
            WHERE source_system = ?
              AND transaction_date = CAST(? AS DATE)
              AND asset_id = ?
              AND transaction_type = ?
              AND ABS(COALESCE(amount_gross, 0) - ?) < 0.005
            LIMIT 1
            """,
            [parsed["source_system"], parsed["transaction_date"], parsed["asset_id"],
             parsed["transaction_type"], parsed["amount_gross"]],
        ).fetchone()
    else:
        try:
            tx_id = int(skey)
        except (TypeError, ValueError):
            return None
        row = db.execute(
            """
            SELECT id, transaction_date, asset_id, transaction_type, amount_net,
                   amount_gross, currency, source_system
            FROM transactions WHERE id = ?
            """,
            [tx_id],
        ).fetchone()

    if row is None:
        return None
    tx_id, tx_date, asset_id, tx_type, amount_net, amount_gross, currency, source_system = row
    return {
        "id": tx_id,
        "transaction_date": tx_date,
        "asset_id": asset_id,
        "transaction_type": tx_type,
        "amount_net": amount_net,
        "amount_gross": amount_gross,
        "currency": currency,
        "source_system": source_system,
    }


def _resolve_and_compose_key(db, source_row_key: str) -> tuple[str, dict]:
    """Resolve source_row_key (legacy id or nk:) to its live transactions row
    and the natural key that row composes to *right now* (always the
    up-to-date nk, even if the input was a legacy id or a stale nk from
    before a value correction). Raises LookupError if unresolvable."""
    resolved = _resolve_transactions_row(db, source_row_key)
    if resolved is None:
        raise LookupError(f"transactions row {source_row_key} not found")
    nk = compose_natural_key(
        resolved["source_system"], resolved["transaction_date"], resolved["asset_id"],
        resolved["transaction_type"], resolved["amount_gross"],
    )
    return nk, resolved


def _amount_to_cny(amount_net, currency: str | None, fx_rate: float) -> float:
    """Convert a native-currency transactions.amount_net to a CNY MAGNITUDE.

    amount_net: numeric (float/Decimal/int) or None
    currency:   'USD' or 'CNY' (case-insensitive); anything else treated as CNY
    fx_rate:    today's USD→CNY rate (pass once per top-level function, not per row)

    Sign contract (see AGENTS.md Rule 26): transactions.amount_net carries THREE
    different per-reader sign conventions with no normalization layer —
    CN_Fund_Excel / Gold_Excel / Insurance_Excel / AIA are always positive;
    Schwab_CSV stores buys negative; RSU_Excel stores sells negative. The raw
    sign therefore carries no reliable economic direction across sources, so
    this helper returns the ABSOLUTE MAGNITUDE in CNY — callers must derive
    inflow/outflow direction from `transaction_type` (LOWER()-cased), not from
    the stored sign.

    This function is used ONLY on the transactions path. FS-cash /
    income_expense_monthly amounts (read via `info["amount_cny"]` elsewhere in
    this module) deliberately do NOT flow through here: their sign is genuine
    economic direction (a cash-balance decrease, or income-minus-expense) and
    must NOT be abs()ed. Do not add this helper to that path.
    """
    if amount_net is None:
        return 0.0
    amt = abs(float(amount_net))
    return amt * fx_rate if (currency or "CNY").upper() == "USD" else amt


def _income_expense_net(payload_json: Any, mapping=None) -> float:
    """Net = total income - total expense for one income_expense_monthly row.

    Both sides are DERIVED from the 月度收支 leaf columns via the `ie_column`
    role mapping (src/services/ie_ledger.py) — never read from the Excel's own
    总收入合计 / 总支出 aggregates (owner ruling 2026-08-01). Same derivation as
    src/financial_analysis/cash_flow.py::parse_monthly_cash_flows, through the
    same shared helper: that function aggregates many rows into monthly
    buckets; this is the per-row equivalent needed for a single record_key.
    Note `total_outflow` includes investment, because the Excel's 总支出 does.
    """
    from src.services.ie_ledger import (  # noqa: PLC0415 — lazy, avoids an import cycle
        default_ie_column_mapping,
        payload_dict,
        role_totals,
    )

    payload = payload_dict(payload_json)
    if not payload:
        return 0.0
    totals = role_totals(payload, mapping if mapping is not None else default_ie_column_mapping())
    income = totals.gross_income
    expense = totals.total_outflow
    if income == 0 and expense == 0 and "amount" in payload and "type" in payload:
        amount = float(payload.get("amount", 0.0) or 0.0)
        flow_type = str(payload.get("type", "")).lower()
        if flow_type == "income":
            income = amount
        elif flow_type == "expense":
            expense = amount
    return round(income - expense, 2)


# ── WS-1 — Financial-Summary cash-flow deltas (plan 2026-07-20) ────────────
# FS cash/deposit accounts are monthly balance snapshots in `holdings`
# (source_system='Financial_Summary_Excel'), not transactions. This section
# turns their month-over-month deltas into flow candidates so the owner can
# tag them the same way as any other flow. See fs_cash_flow_candidates()
# docstring below for the full delta-computation contract.
FS_CASH_SOURCE = "Financial_Summary_Excel"
FS_CASH_FLOW_MIN_CNY = 1000.0  # materiality threshold — suppresses rounding noise
FS_CASH_NK_PREFIX = "fscash:"
# keep in sync with attribution.HISTORY_FLOOR_MONTH; defined here (not imported)
# to avoid a circular import — attribution imports helpers from this module.
FS_CASH_FLOW_FLOOR_MONTH = "2026-01"


def _is_fs_cash_asset(asset_id: Optional[str]) -> bool:
    """Scope predicate: liquid cash/deposits + Wealth_CMB only.

    Excludes Property_* (flat valuation, no real cash flow) and
    Pension_Personal (contributions+returns entangled, can't be split into a
    clean flow) — both out of scope per the plan's owner-approved decision.
    """
    aid = asset_id or ""
    return aid.startswith("CASH_") or aid == "Wealth_CMB"


def _compose_fs_cash_key(asset_id: str, year_month: str) -> str:
    """Stable natural key for an FS-cash monthly delta: `fscash:{asset}|{YYYY-MM}`.

    Derived purely from asset + month (never a transient id), so it survives
    every re-sync by construction — same invariant as the `nk:` keys above.
    """
    return f"{FS_CASH_NK_PREFIX}{asset_id}|{year_month}"


def _parse_fs_cash_key(key: Any) -> Optional[dict]:
    """Inverse of _compose_fs_cash_key. Returns None if `key` is not a
    well-formed fscash: key (caller treats that the same as an unresolvable row)."""
    if not isinstance(key, str) or not key.startswith(FS_CASH_NK_PREFIX):
        return None
    body = key[len(FS_CASH_NK_PREFIX):]
    asset_id, sep, month = body.rpartition("|")
    if not sep or not asset_id or not month:
        return None
    return {"asset_id": asset_id, "month": month}


def _fs_cash_month_str(snapshot_date_val: Any) -> str:
    """YYYY-MM for a holdings.snapshot_date value (date object or ISO string)."""
    if hasattr(snapshot_date_val, "strftime"):
        return snapshot_date_val.strftime("%Y-%m")
    return str(snapshot_date_val)[:7]


def fs_cash_flow_candidates(
    db, floor_month: str = FS_CASH_FLOW_FLOOR_MONTH,
) -> dict[tuple[str, str], dict]:
    """FS cash/deposit account month-over-month balance deltas as flow candidates.

    Keyed the same shape as _candidate_universe's transactions candidates
    (source_table, source_row_key) -> {amount_cny, flow_date, transaction_type,
    asset_id} so it merges directly into the shared candidate universe.

    CRITICAL: per-asset, per-month LATEST snapshot — NEVER a global
    MAX(snapshot_date) (CLAUDE.md Data Accuracy Rule #3; a different asset or
    a different month must never borrow another's latest date). is_shadow is
    intentionally NOT filtered: FS monthly history is is_shadow=TRUE by
    design (superseded snapshot != invalid — see the
    holdings-historical-valuation memory) and filtering it would erase the
    very history this function needs to compute deltas from.

    delta[m] = mv[m] - mv[most recent PRIOR existing month for that asset];
    the first-ever month for an asset has no prior baseline, so
    delta = mv[first] (implicit-zero baseline — "this account first appeared
    with this balance"). Only deltas with abs(amount) >= FS_CASH_FLOW_MIN_CNY
    survive (materiality threshold).

    floor_month bounds which months are EMITTED as candidates (attribution
    and Net Flows are bounded to attribution.HISTORY_FLOOR_MONTH, so
    pre-floor candidates are noise the owner would have to hand-tag for no
    reporting benefit). The FULL history is still walked to compute deltas —
    only the emit step is skipped for pre-floor months — so the first
    in-window month's delta is still computed against the prior real month's
    balance, not an implicit zero.
    """
    rows = db.execute(
        """
        SELECT asset_id, snapshot_date, market_value
        FROM holdings
        WHERE source_system = ?
        ORDER BY asset_id, snapshot_date
        """,
        [FS_CASH_SOURCE],
    ).fetchall()

    # Group by (asset_id, year-month), keeping only the latest snapshot_date
    # row within each group — per-asset, per-month latest, never a global MAX.
    latest_by_asset_month: dict[str, dict[str, tuple]] = defaultdict(dict)
    for asset_id, snapshot_date_val, market_value in rows:
        if not _is_fs_cash_asset(asset_id):
            continue
        month = _fs_cash_month_str(snapshot_date_val)
        existing = latest_by_asset_month[asset_id].get(month)
        if existing is None or snapshot_date_val > existing[0]:
            latest_by_asset_month[asset_id][month] = (snapshot_date_val, market_value)

    candidates: dict[tuple[str, str], dict] = {}
    for asset_id, months in latest_by_asset_month.items():
        prev_value: Optional[float] = None
        for month in sorted(months.keys()):
            snapshot_date_val, market_value = months[month]
            mv = float(market_value) if market_value is not None else 0.0
            delta = mv if prev_value is None else mv - prev_value
            prev_value = mv  # tracks the real last balance regardless of materiality filter below
            if month < floor_month:
                continue  # pre-floor: delta computed (prev_value advanced above) but not emitted
            if abs(delta) < FS_CASH_FLOW_MIN_CNY:
                continue
            key = _compose_fs_cash_key(asset_id, month)
            candidates[("fs_cash_delta", key)] = {
                "amount_cny": round(delta, 2),
                "flow_date": snapshot_date_val,
                "transaction_type": "cash_delta",
                "asset_id": asset_id,
            }
    return candidates


def _candidate_universe(db, fx_rate: float = 7.0) -> dict[tuple[str, str], dict]:
    """Every candidate flow row keyed by (source_table, source_row_key).

    Scope: transactions, plus FS-cash monthly deltas (WS-1). income_expense_monthly
    rows are Excel monthly summaries, not actual transactions, and are excluded
    from the classifier.
    """
    candidates: dict[tuple[str, str], dict] = {}

    tx_rows = db.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, amount_net, currency
        FROM transactions
        WHERE is_provisional = FALSE
          AND LOWER(transaction_type) IN ('transfer_in', 'transfer_out', 'buy', 'sell', 'vest')
        """
    ).fetchall()
    for tx_id, tx_date, asset_id, tx_type, amount_net, currency in tx_rows:
        tx_type_l = (tx_type or "").lower()
        if tx_type_l in ("transfer_in", "transfer_out", "vest") or _is_liquidity_asset(asset_id):
            candidates[("transactions", str(tx_id))] = {
                "amount_cny": _amount_to_cny(amount_net, currency, fx_rate),
                "flow_date": tx_date,
                "transaction_type": tx_type_l,
                "asset_id": asset_id,
            }

    candidates.update(fs_cash_flow_candidates(db))
    return candidates


def _existing_tag_keys(db) -> set[tuple[str, str]]:
    """Existing cash_flow_tags rows, normalized to transactions.id space so
    they can be compared directly against _candidate_universe's keys
    (which are always built from live transactions.id).

    A tag's source_row_key may be a legacy numeric id or a V81 `nk:` stable
    key — either way it's resolved via _resolve_transactions_row. A tag that
    can't be resolved to a live transaction (a genuine orphan) contributes no
    key here, which is correct: it can never match a live candidate.

    WS-1: source_table='fs_cash_delta' rows fall into the `else` branch below
    and are added as a literal (source_table, source_row_key) pair — no
    normalization needed, because fscash: keys are already stable natural
    keys derived from (asset_id, month), same as income_expense_monthly's
    record_key. This is what makes list_unclassified_flows /
    flow_contamination_status pick up untagged FS-cash deltas automatically
    once fs_cash_flow_candidates() feeds _candidate_universe.
    """
    rows = db.execute("SELECT source_table, source_row_key FROM cash_flow_tags").fetchall()
    keys: set[tuple[str, str]] = set()
    for source_table, source_row_key in rows:
        skey = str(source_row_key)
        if source_table == "transactions":
            resolved = _resolve_transactions_row(db, skey)
            if resolved is not None:
                keys.add(("transactions", str(resolved["id"])))
            continue
        keys.add((source_table, skey))
    return keys


def _manual_key_set(db) -> set[tuple[str, str]]:
    """Manually-tagged keys, normalized to the same nk: space the classifier
    uses so 'already manually tagged, never overwrite' skip logic still
    works after V81 (a manual tag might be a pre-V81 legacy id or a stable
    nk: key). A legacy-id manual tag that can no longer resolve to a live
    transaction is kept under its original (non-matching) key — it simply
    won't collide with anything the classifier computes for a live row,
    which is correct: that transaction, in its original form, is gone.
    """
    rows = db.execute(
        "SELECT source_table, source_row_key FROM cash_flow_tags WHERE tagged_by = 'manual'"
    ).fetchall()
    keys: set[tuple[str, str]] = set()
    for source_table, source_row_key in rows:
        skey = str(source_row_key)
        if source_table == "transactions" and not is_natural_key(skey):
            resolved = _resolve_transactions_row(db, skey)
            if resolved is not None:
                skey = compose_natural_key(
                    resolved["source_system"], resolved["transaction_date"], resolved["asset_id"],
                    resolved["transaction_type"], resolved["amount_gross"],
                )
        keys.add((source_table, skey))
    return keys


def _classified_transaction_rows(
    db, classification: Optional[str] = None, since: Optional[date] = None,
) -> list[dict]:
    """cash_flow_tags rows (source_table='transactions'), each resolved in
    Python to its live transaction (natural-key aware — a SQL JOIN on
    CAST(tx.id AS VARCHAR) = source_row_key silently drops every nk:-keyed
    and orphaned row, which is exactly the class of bug V81 fixes).

    Returns one dict per tag row:
      {tag_id, source_row_key, classification, tagged_by, flow_date, note,
       rule_id, resolved}
    where 'resolved' is the _resolve_transactions_row() dict, or None for an
    orphan. Callers decide how to treat orphans (list_classified_flows shows
    them with orphaned=True; contribution sums skip them, same as today).
    """
    params: list = []
    where = "WHERE cft.source_table = 'transactions'"
    if classification is not None:
        where += " AND cft.classification = ?"
        params.append(classification)
    if since is not None:
        where += " AND cft.flow_date >= ? AND cft.flow_date IS NOT NULL"
        params.append(since.isoformat())

    rows = db.execute(
        f"""
        SELECT cft.id, cft.source_row_key, cft.classification, cft.tagged_by,
               cft.flow_date, cft.note, cft.rule_id
        FROM cash_flow_tags cft
        {where}
        ORDER BY cft.flow_date DESC NULLS LAST, cft.id DESC
        """,
        params,
    ).fetchall()

    out = []
    for tag_id, source_row_key, cls, tagged_by, flow_date, note, rule_id in rows:
        out.append({
            "tag_id": tag_id,
            "source_row_key": str(source_row_key),
            "classification": cls,
            "tagged_by": tagged_by,
            "flow_date": flow_date,
            "note": note,
            "rule_id": rule_id,
            "resolved": _resolve_transactions_row(db, str(source_row_key)),
        })
    return out


def _classified_fs_cash_rows(
    db, classification: Optional[str] = None, since: Optional[date] = None,
) -> list[dict]:
    """cash_flow_tags rows (source_table='fs_cash_delta'), each paired with its
    live candidate info from fs_cash_flow_candidates() for display — mirrors
    _classified_transaction_rows for the FS-cash path (WS-1).

    Returns one dict per tag row:
      {tag_id, source_row_key, classification, tagged_by, flow_date, note,
       rule_id, info}
    where 'info' is the fs_cash_flow_candidates() entry for this key (real
    current delta/asset/flow_date), or None if the key is no longer a live
    candidate (e.g. the delta dropped below FS_CASH_FLOW_MIN_CNY after a data
    correction — treated as orphaned, same semantics as an unresolvable
    transactions tag).
    """
    params: list = []
    where = "WHERE cft.source_table = 'fs_cash_delta'"
    if classification is not None:
        where += " AND cft.classification = ?"
        params.append(classification)
    if since is not None:
        where += " AND cft.flow_date >= ? AND cft.flow_date IS NOT NULL"
        params.append(since.isoformat())

    rows = db.execute(
        f"""
        SELECT cft.id, cft.source_row_key, cft.classification, cft.tagged_by,
               cft.flow_date, cft.note, cft.rule_id
        FROM cash_flow_tags cft
        {where}
        ORDER BY cft.flow_date DESC NULLS LAST, cft.id DESC
        """,
        params,
    ).fetchall()

    candidates = fs_cash_flow_candidates(db)
    out = []
    for tag_id, source_row_key, cls, tagged_by, flow_date, note, rule_id in rows:
        skey = str(source_row_key)
        out.append({
            "tag_id": tag_id,
            "source_row_key": skey,
            "classification": cls,
            "tagged_by": tagged_by,
            "flow_date": flow_date,
            "note": note,
            "rule_id": rule_id,
            "info": candidates.get(("fs_cash_delta", skey)),
        })
    return out


def _upsert_tag(
    db, source_table: str, source_row_key: str, classification: str, tagged_by: str,
    amount_cny: Optional[float], flow_date: Any, note: Optional[str],
    rule_id: Optional[str] = None,
) -> None:
    db.execute(
        """
        INSERT INTO cash_flow_tags
            (source_table, source_row_key, classification, tagged_by, amount_cny, flow_date, note, rule_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_table, source_row_key) DO UPDATE SET
            classification = excluded.classification,
            tagged_by = excluded.tagged_by,
            amount_cny = excluded.amount_cny,
            flow_date = excluded.flow_date,
            note = excluded.note,
            rule_id = excluded.rule_id
        """,
        [source_table, str(source_row_key), classification, tagged_by, amount_cny, flow_date, note, rule_id],
    )


def classify_flows_heuristic(db, dry_run: bool = False) -> dict:
    """Tag high-confidence flows by ordered deterministic rules; never touch manual rows.

    Rule precedence (internal-transfer rules run BEFORE external):
      R0 security_transfer_pair  → internal_transfer (cross-source ACAT leg pair, ≤7-day window)
      R1 same_day_transfer_pair  → internal_transfer (matched transfer_in/transfer_out same-day)
      R2 money_market_move       → internal_transfer (SGOV/liquidity same-day switch)
      R3 rsu_vest                → external_contribution (transaction_type='vest')
      Residual                   → left unclassified (never guessed)

    Rows tagged_by='manual' are never overwritten. A row already tagged by an
    earlier rule in this same run is also never re-tagged by a later rule
    (counted the same as a manual skip) — R0 runs before R1/R2 specifically so
    a cross-source ACAT pair that also happens to be same-day/same-amount
    isn't double-processed.
    When dry_run=True: runs the full matching logic but does NOT write to the DB.
    """
    fx_rate = get_today_usd_cny_rate()
    # V81: manual_keys lives in nk: space (see _manual_key_set) so the
    # 'never overwrite a manual tag' check below still works once tags are
    # keyed by the stable natural key instead of a transactions.id that
    # regenerates on every sync.
    manual_keys = _manual_key_set(db)
    tagged = 0
    skipped_manual = 0
    tagged_pairs: list[tuple[str, str]] = []
    tagged_this_run: set[tuple[str, str]] = set()

    def _tag(
        source_table: str, row_key: Any, flow_date: Any, note: str,
        classification: str, amount_cny: float, rule_id: Optional[str],
    ) -> None:
        nonlocal tagged, skipped_manual
        key = (source_table, str(row_key))
        if key in manual_keys or key in tagged_this_run:
            skipped_manual += 1
            return
        if not dry_run:
            _upsert_tag(
                db, source_table, str(row_key), classification, "heuristic",
                amount_cny, flow_date, note, rule_id=rule_id,
            )
        tagged_pairs.append((source_table, str(row_key)))
        tagged_this_run.add(key)
        tagged += 1

    # ── R0: security_transfer_pair (cross-source ACAT, windowed) ────────────
    # Schwab's 'Security Transfer' action (V79, src.database.mapping_seeds)
    # resolves to transfer_out/transfer_in by quantity sign at the reader
    # hook — the counterpart IBKR leg is typically already transfer_in/out on
    # a DIFFERENT date (source-lag, not same-day), so R1's exact-date grouping
    # cannot match them. Matches on asset_id + |quantity| (not amount — both
    # legs are always ~$0) within a 7-day window, cross-source.
    # source_system + amount_gross are selected (in addition to the matching
    # fields already needed) so each leg's V81 natural key can be composed
    # here rather than re-querying (design: rules already have the row
    # fields in hand).
    transfer_leg_rows = db.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, quantity,
               source_system, amount_gross
        FROM transactions
        WHERE is_provisional = FALSE
          AND LOWER(transaction_type) IN ('transfer_in', 'transfer_out')
          AND ABS(COALESCE(amount_net, 0)) < 0.005
          AND ABS(COALESCE(quantity, 0)) > 0.0001
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()
    legs_in: list[dict] = []
    legs_out: list[dict] = []
    for tx_id, tx_date, asset_id, tx_type, quantity, source_system, amount_gross in transfer_leg_rows:
        nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
        # A manually-tagged leg must not be used as a pairing candidate at
        # all — tagging its partner 'internal_transfer' would assert a pair
        # relationship the owner may have manually overridden to something
        # else entirely (e.g. external_contribution). manual_keys is fixed
        # for this whole run, so this filter is sufficient (R0 runs first).
        if ("transactions", nk) in manual_keys:
            continue
        leg = {"id": tx_id, "date": tx_date, "asset_id": asset_id, "qty": abs(float(quantity or 0.0)), "nk": nk}
        (legs_in if (tx_type or "").lower() == "transfer_in" else legs_out).append(leg)

    used_transfer_legs: set = set()
    for leg_in in legs_in:
        if leg_in["id"] in used_transfer_legs or leg_in["date"] is None:
            continue
        candidates = [
            o for o in legs_out
            if o["id"] not in used_transfer_legs
            and o["date"] is not None
            and o["asset_id"] == leg_in["asset_id"]
            and abs(o["qty"] - leg_in["qty"]) <= 1e-6
            and abs((o["date"] - leg_in["date"]).days) <= 7
        ]
        if not candidates:
            continue
        # Greedy: pair with the nearest date when more than one candidate matches.
        match = min(candidates, key=lambda o: abs((o["date"] - leg_in["date"]).days))
        used_transfer_legs.add(leg_in["id"])
        used_transfer_legs.add(match["id"])
        _tag("transactions", leg_in["nk"], leg_in["date"],
             "heuristic: cross-source security transfer pair (ACAT)",
             "internal_transfer", 0.0, "security_transfer_pair")
        _tag("transactions", match["nk"], match["date"],
             "heuristic: cross-source security transfer pair (ACAT)",
             "internal_transfer", 0.0, "security_transfer_pair")

    # ── R1: same_day_transfer_pair ───────────────────────────────────────────
    transfer_rows = db.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, amount_net,
               amount_gross, source_system
        FROM transactions
        WHERE is_provisional = FALSE AND LOWER(transaction_type) IN ('transfer_in', 'transfer_out')
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()
    by_date: dict[Any, list[dict]] = defaultdict(list)
    for tx_id, tx_date, asset_id, tx_type, amount_net, amount_gross, source_system in transfer_rows:
        nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
        by_date[tx_date].append({
            "id": tx_id, "type": (tx_type or "").lower(), "amount": float(amount_net or 0.0), "nk": nk,
        })

    for tx_date, legs in by_date.items():
        ins = [leg for leg in legs if leg["type"] == "transfer_in"]
        outs = [leg for leg in legs if leg["type"] == "transfer_out"]
        used: set = set()
        for leg_in in ins:
            match = next(
                (o for o in outs if o["id"] not in used
                 and abs(abs(o["amount"]) - abs(leg_in["amount"])) <= _AMOUNT_TOLERANCE_CNY),
                None,
            )
            if match is None:
                continue
            used.add(match["id"])
            _tag("transactions", leg_in["nk"], tx_date,
                 "heuristic: matched transfer_in/transfer_out pair",
                 "internal_transfer", 0.0, "same_day_transfer_pair")
            _tag("transactions", match["nk"], tx_date,
                 "heuristic: matched transfer_in/transfer_out pair",
                 "internal_transfer", 0.0, "same_day_transfer_pair")

    # ── R2: money_market_move ────────────────────────────────────────────────
    bs_rows = db.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, amount_net,
               amount_gross, source_system
        FROM transactions
        WHERE is_provisional = FALSE AND LOWER(transaction_type) IN ('buy', 'sell')
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()
    by_date_bs: dict[Any, list[dict]] = defaultdict(list)
    for tx_id, tx_date, asset_id, tx_type, amount_net, amount_gross, source_system in bs_rows:
        nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
        by_date_bs[tx_date].append({
            "id": tx_id, "asset_id": asset_id, "type": (tx_type or "").lower(),
            "amount": float(amount_net or 0.0), "nk": nk,
        })

    for tx_date, legs in by_date_bs.items():
        liquidity_legs = [leg for leg in legs if _is_liquidity_asset(leg["asset_id"])]
        other_legs = [leg for leg in legs if not _is_liquidity_asset(leg["asset_id"])]
        used2: set = set()
        for liq in liquidity_legs:
            tolerance = max(_AMOUNT_TOLERANCE_CNY, abs(liq["amount"]) * _AMOUNT_TOLERANCE_PCT)
            match = next(
                (o for o in other_legs if o["id"] not in used2
                 and o["type"] != liq["type"]
                 and abs(abs(o["amount"]) - abs(liq["amount"])) <= tolerance),
                None,
            )
            if match is None:
                continue
            used2.add(match["id"])
            _tag("transactions", liq["nk"], tx_date,
                 "heuristic: SGOV/money-market same-day switch",
                 "internal_transfer", 0.0, "money_market_move")
            _tag("transactions", match["nk"], tx_date,
                 "heuristic: SGOV/money-market same-day switch",
                 "internal_transfer", 0.0, "money_market_move")

    # ── R3: rsu_vest ─────────────────────────────────────────────────────────
    # No separate 'already tagged by R1/R2' pre-check needed: R0-R2 only ever
    # process transfer_in/transfer_out/buy/sell rows, R3 only processes
    # 'vest' rows — disjoint transaction_type sets by construction — and
    # _tag()'s own tagged_this_run/manual_keys check (now in nk: space)
    # already guards against any accidental overlap.
    vest_rows = db.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, amount_net,
               amount_gross, currency, source_system
        FROM transactions
        WHERE is_provisional = FALSE AND LOWER(transaction_type) = 'vest'
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()
    for tx_id, tx_date, asset_id, tx_type, amount_net, amount_gross, currency, source_system in vest_rows:
        nk = compose_natural_key(source_system, tx_date, asset_id, tx_type, amount_gross)
        real_amount = _amount_to_cny(amount_net, currency, fx_rate)
        _tag("transactions", nk, tx_date,
             "heuristic: RSU vest inflow",
             "external_contribution", real_amount, "rsu_vest")

    # ── Candidate / unclassified count ───────────────────────────────────────
    candidates = _candidate_universe(db, fx_rate)
    tagged_keys = _existing_tag_keys(db)
    unclassified_count = sum(1 for key in candidates if key not in tagged_keys)

    # Collect tagged_ids from DB when not dry_run
    tagged_ids: list[int] = []
    if not dry_run and tagged_pairs:
        for src_table, src_key in tagged_pairs:
            id_row = db.execute(
                "SELECT id FROM cash_flow_tags WHERE source_table = ? AND source_row_key = ?",
                [src_table, src_key],
            ).fetchone()
            if id_row is not None:
                tagged_ids.append(id_row[0])

    return {
        "tagged": tagged,
        "skipped_manual": skipped_manual,
        "unclassified_count": unclassified_count,
        "tagged_ids": tagged_ids,
        "dry_run": dry_run,
        "would_tag": tagged if dry_run else None,
    }


def list_unclassified_flows(db) -> list[dict]:
    """Candidate flow rows with no cash_flow_tags entry yet (for the tagging UI)."""
    fx_rate = get_today_usd_cny_rate()
    candidates = _candidate_universe(db, fx_rate)
    tagged_keys = _existing_tag_keys(db)
    out = []
    for (source_table, source_row_key), info in candidates.items():
        if (source_table, source_row_key) in tagged_keys:
            continue
        out.append({
            "source_table": source_table,
            "source_row_key": source_row_key,
            "amount_cny": info["amount_cny"],
            "flow_date": str(info["flow_date"]) if info["flow_date"] is not None else None,
            "transaction_type": info.get("transaction_type"),
            "asset_id": info.get("asset_id"),
        })
    out.sort(key=lambda r: r["flow_date"] or "", reverse=True)
    return out


def tag_flow_manual(db, source_table: str, source_row_key: str, classification: str, note: Optional[str] = None) -> dict:
    """Manual tag upsert (PUT /north-star/flows/tag). Always tagged_by='manual'.

    Resolves amount_cny/flow_date from the source row. Raises ValueError for
    an invalid classification/source_table, LookupError for an unresolvable
    source row (route maps these to 422/404).

    V81: the frontend contract is unchanged — it may pass either a raw
    transactions.id (first-time tag, from the unclassified list) or an
    already-`nk:` key (re-tagging a row surfaced by the classified list).
    Either way this resolves to the live transaction and stores under that
    transaction's *current* natural key, so the tag survives the next sync's
    delete+reinsert regardless of which form the caller sent.
    """
    if classification not in _VALID_CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {_VALID_CLASSIFICATIONS}, got {classification!r}")

    if source_table == "transactions":
        nk, resolved = _resolve_and_compose_key(db, source_row_key)
        flow_date = resolved["transaction_date"]
        fx_rate = get_today_usd_cny_rate()
        amount_cny = _amount_to_cny(resolved["amount_net"], resolved["currency"], fx_rate)
        stored_key = nk
        # Defensive: if the caller's key differs from the current natural key
        # (legacy id being tagged for the first time, or a stale nk from
        # before a value correction), drop any row still sitting under the
        # old key so we don't leave a duplicate/orphaned twin behind.
        if stored_key != str(source_row_key):
            db.execute(
                "DELETE FROM cash_flow_tags WHERE source_table = ? AND source_row_key = ?",
                [source_table, str(source_row_key)],
            )
    elif source_table == "income_expense_monthly":
        row = db.execute(
            "SELECT transaction_date, payload FROM income_expense_monthly WHERE record_key = ?",
            [source_row_key],
        ).fetchone()
        if row is None:
            raise LookupError(f"income_expense_monthly row {source_row_key} not found")
        flow_date, payload = row
        amount_cny = _income_expense_net(payload)
        stored_key = str(source_row_key)
    elif source_table == "fs_cash_delta":
        # WS-1: parse the key first so a malformed key fails fast with a
        # clear error rather than silently missing the candidate-map lookup.
        parsed = _parse_fs_cash_key(str(source_row_key))
        if parsed is None:
            raise LookupError(f"fs_cash_delta key {source_row_key!r} is not a well-formed fscash: key")
        info = fs_cash_flow_candidates(db).get(("fs_cash_delta", str(source_row_key)))
        if info is None:
            raise LookupError(
                f"fs_cash_delta row {source_row_key!r} not found "
                "(no material delta for this asset/month — may have dropped below threshold)"
            )
        flow_date = info["flow_date"]
        amount_cny = info["amount_cny"]
        stored_key = str(source_row_key)
    else:
        raise ValueError(
            f"source_table must be 'transactions', 'income_expense_monthly', or 'fs_cash_delta', "
            f"got {source_table!r}"
        )

    # internal_transfer is always ¥0 contribution by definition (PRD F3.1 acceptance).
    stored_amount = 0.0 if classification == "internal_transfer" else amount_cny
    _upsert_tag(db, source_table, stored_key, classification, "manual", stored_amount, flow_date, note)

    return {
        "source_table": source_table,
        "source_row_key": stored_key,
        "classification": classification,
        "tagged_by": "manual",
        "amount_cny": stored_amount,
        "flow_date": str(flow_date) if flow_date is not None else None,
        "note": note,
    }


def list_classified_flows(db, classification: Optional[str] = None) -> list[dict]:
    """Returns already-tagged cash_flow_tags rows, newest flow_date first.

    Scoped to transactions + fs_cash_delta rows (WS-1; income_expense_monthly
    rows are excluded from the classifier). Optionally filtered by
    classification. Resolves the REAL source amount and transaction_type from
    the live source row, NOT the tag's stored amount_cny (which is 0 for
    internal_transfer by design).

    This is DISPLAY-only: the stored tag amount_cny in cash_flow_tags is
    unchanged (internal_transfer still stores 0 for contribution math).

    V81 orphan visibility: a tag whose transaction can no longer be resolved
    (re-imported with a different identity, or genuinely deleted) is still
    returned — never silently dropped — with "orphaned": true and
    amount_cny/asset_id/transaction_type all null. flow_date, classification,
    tagged_by, note, rule_id are preserved from the tag itself (those never
    depended on the live transaction row). fs_cash_delta tags follow the same
    orphan contract: a key no longer present in fs_cash_flow_candidates()
    (e.g. its delta dropped below FS_CASH_FLOW_MIN_CNY after a data
    correction) is returned with orphaned=True and a null amount.
    """
    if classification is not None and classification not in _VALID_CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {_VALID_CLASSIFICATIONS}, got {classification!r}")

    fx_rate = get_today_usd_cny_rate()
    tag_rows = _classified_transaction_rows(db, classification=classification)

    out = []
    for row in tag_rows:
        resolved = row["resolved"]
        if resolved is not None:
            real_amount = _amount_to_cny(resolved["amount_net"], resolved["currency"], fx_rate)
            asset_id = resolved["asset_id"]
            real_tx_type = (resolved["transaction_type"] or "").lower() if resolved["transaction_type"] else None
            orphaned = False
        else:
            real_amount = None
            asset_id = None
            real_tx_type = None
            orphaned = True

        out.append({
            "source_table": "transactions",
            "source_row_key": row["source_row_key"],
            "classification": row["classification"],
            "tagged_by": row["tagged_by"],
            "amount_cny": real_amount,
            "flow_date": str(row["flow_date"]) if row["flow_date"] is not None else None,
            "asset_id": asset_id,
            "transaction_type": real_tx_type,
            "note": row["note"],
            "rule_id": row["rule_id"],
            "orphaned": orphaned,
        })

    # WS-1: FS-cash tagged rows, resolved against the live candidate map
    # (always the REAL current delta — same "display resolves from source,
    # not the possibly-zeroed stored tag amount" contract as the transactions
    # path above).
    fs_tag_rows = _classified_fs_cash_rows(db, classification=classification)
    for row in fs_tag_rows:
        info = row["info"]
        if info is not None:
            out.append({
                "source_table": "fs_cash_delta",
                "source_row_key": row["source_row_key"],
                "classification": row["classification"],
                "tagged_by": row["tagged_by"],
                "amount_cny": info["amount_cny"],
                "flow_date": str(info["flow_date"]) if info["flow_date"] is not None else None,
                "asset_id": info["asset_id"],
                "transaction_type": info["transaction_type"],
                "note": row["note"],
                "rule_id": row["rule_id"],
                "orphaned": False,
            })
        else:
            out.append({
                "source_table": "fs_cash_delta",
                "source_row_key": row["source_row_key"],
                "classification": row["classification"],
                "tagged_by": row["tagged_by"],
                "amount_cny": None,
                "flow_date": str(row["flow_date"]) if row["flow_date"] is not None else None,
                "asset_id": None,
                "transaction_type": None,
                "note": row["note"],
                "rule_id": row["rule_id"],
                "orphaned": True,
            })

    out.sort(key=lambda r: r["flow_date"] or "", reverse=True)
    return out


def tag_flows_bulk(db, items: list[dict], classification: str) -> dict:
    """Bulk manual tag upsert. Each item is {source_table, source_row_key}.

    Validates classification against the enum first (422 on bad value).
    Resolves amount_cny / flow_date from the source row for each item (same
    logic as tag_flow_manual — V81 natural-key resolution + re-key-on-write).
    Skips items whose source row cannot be found and counts them in
    'not_found'. Returns {"tagged": n, "not_found": m}.
    """
    if classification not in _VALID_CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {_VALID_CLASSIFICATIONS}, got {classification!r}")

    fx_rate = get_today_usd_cny_rate()
    tagged = 0
    not_found = 0
    stored_amount_for_internal = classification == "internal_transfer"
    fs_cash_candidates: Optional[dict] = None  # lazy — only queried if an fs_cash_delta item shows up

    for item in items:
        source_table = item.get("source_table", "")
        source_row_key = str(item.get("source_row_key", ""))

        if source_table == "transactions":
            resolved = _resolve_transactions_row(db, source_row_key)
            if resolved is None:
                not_found += 1
                continue
            flow_date = resolved["transaction_date"]
            amount_cny = 0.0 if stored_amount_for_internal else _amount_to_cny(
                resolved["amount_net"], resolved["currency"], fx_rate,
            )
            stored_key = compose_natural_key(
                resolved["source_system"], resolved["transaction_date"], resolved["asset_id"],
                resolved["transaction_type"], resolved["amount_gross"],
            )
            if stored_key != source_row_key:
                db.execute(
                    "DELETE FROM cash_flow_tags WHERE source_table = ? AND source_row_key = ?",
                    [source_table, source_row_key],
                )
        elif source_table == "income_expense_monthly":
            row = db.execute(
                "SELECT transaction_date, payload FROM income_expense_monthly WHERE record_key = ?",
                [source_row_key],
            ).fetchone()
            if row is None:
                not_found += 1
                continue
            flow_date, payload = row
            amount_cny = 0.0 if stored_amount_for_internal else _income_expense_net(payload)
            stored_key = source_row_key
        elif source_table == "fs_cash_delta":
            # WS-1: same lookup/skip contract as tag_flow_manual, but batched —
            # fs_cash_candidates is queried once (lazily) and reused across items.
            if fs_cash_candidates is None:
                fs_cash_candidates = fs_cash_flow_candidates(db)
            info = fs_cash_candidates.get(("fs_cash_delta", source_row_key))
            if info is None:
                not_found += 1
                continue
            flow_date = info["flow_date"]
            amount_cny = 0.0 if stored_amount_for_internal else info["amount_cny"]
            stored_key = source_row_key
        else:
            not_found += 1
            continue

        _upsert_tag(db, source_table, stored_key, classification, "manual", amount_cny, flow_date, None)
        tagged += 1

    return {"tagged": tagged, "not_found": not_found}


def untag_flows(db, items: list[dict]) -> dict:
    """Delete cash_flow_tags rows for the given (source_table, source_row_key) pairs.

    Scoped delete on the overlay table only — never touches transactions or
    income_expense_monthly (same precedent as revert_flow_classification).
    Uses a before/after count because DuckDB's connector abstraction does not
    expose a reliable rowcount after DELETE. Returns {"deleted": n}.

    V81 backward compat: matches on whatever key is actually stored, which
    may not be the key the caller passed. list_classified_flows/
    list_unclassified_flows already echo back the row's true stored key
    (nk: for a resolved tag, the legacy id for an unresolved orphan or an
    untagged candidate) so the common path is an exact match. But a caller
    that still has an old transactions.id in hand (e.g. re-using an id from
    before the row was tagged and re-keyed to nk: in the same session) needs
    that id resolved to the row's current natural key too — so both the
    literal input key and its resolved natural key (when resolvable) are
    tried for source_table='transactions'.
    """
    if not items:
        return {"deleted": 0}

    before = db.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    for item in items:
        source_table = item.get("source_table", "")
        source_row_key = str(item.get("source_row_key", ""))
        keys_to_try = {source_row_key}
        if source_table == "transactions":
            resolved = _resolve_transactions_row(db, source_row_key)
            if resolved is not None:
                keys_to_try.add(compose_natural_key(
                    resolved["source_system"], resolved["transaction_date"], resolved["asset_id"],
                    resolved["transaction_type"], resolved["amount_gross"],
                ))
        for key in keys_to_try:
            db.execute(
                "DELETE FROM cash_flow_tags WHERE source_table = ? AND source_row_key = ?",
                [source_table, key],
            )
    after = db.execute("SELECT COUNT(*) FROM cash_flow_tags").fetchone()[0]
    return {"deleted": before - after}


def contributions_summary(db, window_months: int = 12) -> dict:
    """Extended contributions payload for GET /north-star/contributions.

    Args:
        window_months: trailing window (in DATA months, see
            investment_contributions.py) passed through to
            contributions_summary_v2() for the investment.* sub-object AND,
            via investment.window_start_month/window_end_month, to rsu.*
            (the coupling is intentional — see the rsu.* block below, never
            let the two windows diverge). Does NOT affect ytd_sum/
            trailing_12m_sum/by_classification (base) below, which remain the
            legacy cash_flow_tags-derived trailing-12M/YTD figures — those are
            retired from display (ADR-025 §4a) and were never toggle-driven.
            Default 12 preserves the pre-existing behaviour of every other
            caller of this function.

    Returns:
    - ytd_sum, trailing_12m_sum, unclassified_count from contribution_metrics()
    - by_classification: per-classification trailing-12m sum of amount_cny from
      cash_flow_tags. Uses trailing-12M window for consistency with
      contribution_metrics trailing_12m_sum (same trailing_start = today - 365 days).

    Rationale for trailing-12M on by_classification: it matches the primary
    metric window (trailing_12m_sum) so all numbers in the response are
    comparable without the user needing to know different time windows apply.
    """
    fx_rate = get_today_usd_cny_rate()
    today = date.today()
    trailing_start = today - timedelta(days=365)

    base = contribution_metrics(db)

    # Per-classification trailing-12M sums from REAL source amounts.
    # cft.amount_cny is 0 for internal_transfer (by PRD design), so we must
    # resolve amounts from source transactions rows to show true volume.
    # Scoped to source_table IN ('transactions', 'fs_cash_delta') —
    # income_expense_monthly is excluded from the classifier scope. V81:
    # resolved in Python via _classified_transaction_rows (natural-key
    # aware) rather than a SQL JOIN on CAST(tx.id AS VARCHAR) — that JOIN
    # silently drops every nk:-keyed and orphaned row, which is exactly the
    # undercounting bug this fixes.
    # An orphaned tag contributes no amount here (its real current amount is
    # unknowable) — same as today's behavior for a genuinely unresolvable row.
    tagged_rows = _classified_transaction_rows(db, since=trailing_start)

    by_classification: dict[str, float] = {
        "external_contribution": 0.0,
        "internal_transfer": 0.0,
        "income_reinvested": 0.0,
    }
    for row in tagged_rows:
        cls = row["classification"]
        if cls not in by_classification:
            continue
        resolved = row["resolved"]
        if resolved is None:
            continue
        real_amount = _amount_to_cny(resolved["amount_net"], resolved["currency"], fx_rate)
        by_classification[cls] = round(by_classification[cls] + real_amount, 2)

    # WS-1: same trailing-12M fold-in for fs_cash_delta tags — real current
    # delta from fs_cash_flow_candidates(), same "show true volume regardless
    # of classification" rule as the transactions loop above.
    fs_tagged_rows = _classified_fs_cash_rows(db, since=trailing_start)
    for row in fs_tagged_rows:
        cls = row["classification"]
        if cls not in by_classification:
            continue
        info = row["info"]
        if info is None:
            continue
        by_classification[cls] = round(by_classification[cls] + info["amount_cny"], 2)

    # investment.* is the authoritative 月度收支-derived portfolio
    # contribution/savings figure (plan 2026-07-20-investment-contributions-
    # savings.md §Reconciliation) — the tag-based sums above (ytd_sum,
    # trailing_12m_sum, by_classification) are the per-row flow view derived
    # from cash_flow_tags. NEVER sum the two: they can double-count the same
    # money recorded once in the owner's monthly ledger and once as a
    # brokerage/bank transaction or FS-cash delta.
    from src.services.investment_contributions import contributions_summary_v2

    investment = contributions_summary_v2(db, window_months=window_months)

    # rsu.* fills the gap ADR-025 left open (plan
    # 2026-07-25-cash-flow-classification-completion.md §3.3): RSU shares
    # that vest and are KEPT never appear in `投资理财` (the ledger books RSU
    # as income, not investment), so investment.net_external_ttm alone misses
    # them. Uses the EXACT SAME window investment.* was computed over — read
    # off `investment`, never recomputed independently — so the two never
    # drift out of sync (window_start_month/window_end_month is None when
    # income_expense_monthly has no rows at all; the rsu.* fields degrade to
    # zeros/None in that case rather than guessing a different window).
    from src.services.rsu_contributions import rsu_retained_ttm, rsu_vest_gross_ttm

    window_start = investment["window_start_month"]
    window_end = investment["window_end_month"]
    if window_start is not None and window_end is not None:
        vest_gross_ttm = rsu_vest_gross_ttm(db, window_start, window_end)
        retained = rsu_retained_ttm(db, window_start, window_end)
        retained_ttm = retained["retained_cny"]
        retained_shares = retained["retained_shares"]
        oversold_shares = retained["oversold_shares"]
    else:
        vest_gross_ttm = 0.0
        retained_ttm = 0.0
        retained_shares = 0.0
        oversold_shares = 0.0

    rsu = {
        "vest_gross_ttm": vest_gross_ttm,
        "retained_ttm": retained_ttm,
        "retained_shares": retained_shares,
        "oversold_shares": oversold_shares,
        "window_start_month": window_start,
        "window_end_month": window_end,
    }

    return {
        "ytd_sum": base["ytd_sum"],
        "trailing_12m_sum": base["trailing_12m_sum"],
        "unclassified_count": base["unclassified_count"],
        "by_classification": by_classification,
        "investment": investment,
        "rsu": rsu,
    }


def flow_contamination_status(db) -> dict:
    """Returns contamination-check data for the glide-path run-rate gate (Fix 5).

    Contamination means the candidate flow universe has too many unclassified
    rows for the run-rate to be trustworthy.  Two triggers:
    - unclassified_count / total_count > 5%  (or total_count == 0 = clean)
    - any unclassified inflow > ¥50,000 (a missed salary deposit would inflate
      the run-rate dramatically)

    Returns:
        {
          "contaminated": bool,
          "unclassified_count": int,
          "total_count": int,
          "has_large_untagged_inflow": bool,
        }
    """
    fx_rate = get_today_usd_cny_rate()
    candidates = _candidate_universe(db, fx_rate)
    tagged_keys = _existing_tag_keys(db)

    unclassified = [(k, v) for k, v in candidates.items() if k not in tagged_keys]
    unclassified_count = len(unclassified)
    total_count = len(candidates)

    # "any untagged inflow >¥50,000"
    has_large_untagged_inflow = any(
        info["amount_cny"] > 50_000.0 for _, info in unclassified
    )

    contaminated = (
        (total_count > 0 and (unclassified_count / total_count) > 0.05)
        or has_large_untagged_inflow
    )

    return {
        "contaminated": contaminated,
        "unclassified_count": unclassified_count,
        "total_count": total_count,
        "has_large_untagged_inflow": has_large_untagged_inflow,
    }


def contribution_metrics(db) -> dict:
    """YTD/trailing-12m/monthly external_contribution sums + unclassified count.

    V81: resolved in Python via _classified_transaction_rows (natural-key
    aware) instead of a SQL INNER JOIN on CAST(tx.id AS VARCHAR) — that join
    silently dropped every nk:-keyed and orphaned tag from the sum.

    WS-1: also folds in fs_cash_delta external_contribution amounts (real
    current delta from fs_cash_flow_candidates()) into the same YTD/trailing/
    monthly sums, so tagged FS savings show up alongside transaction-based
    contributions. unclassified_count already includes untagged FS-cash
    deltas automatically — it comes from _candidate_universe(), which merges
    fs_cash_flow_candidates() in.
    """
    fx_rate = get_today_usd_cny_rate()
    today = date.today()
    year_start = date(today.year, 1, 1)
    trailing_start = today - timedelta(days=365)

    tag_rows = _classified_transaction_rows(db, classification="external_contribution")

    ytd_sum = 0.0
    trailing_12m_sum = 0.0
    monthly: dict[str, float] = defaultdict(float)
    for row in tag_rows:
        resolved = row["resolved"]
        if resolved is None:
            continue
        amt = _amount_to_cny(resolved["amount_net"], resolved["currency"], fx_rate)
        d = row["flow_date"] if isinstance(row["flow_date"], date) else None
        if d is None:
            continue
        if d >= year_start:
            ytd_sum += amt
        if d >= trailing_start:
            trailing_12m_sum += amt
        monthly[d.strftime("%Y-%m")] += amt

    fs_tag_rows = _classified_fs_cash_rows(db, classification="external_contribution")
    for row in fs_tag_rows:
        info = row["info"]
        if info is None:
            continue
        amt = info["amount_cny"]
        d = info["flow_date"] if isinstance(info["flow_date"], date) else None
        if d is None:
            continue
        if d >= year_start:
            ytd_sum += amt
        if d >= trailing_start:
            trailing_12m_sum += amt
        monthly[d.strftime("%Y-%m")] += amt

    monthly_series = [{"month": m, "amount": round(v, 2)} for m, v in sorted(monthly.items())][-24:]

    candidates = _candidate_universe(db, fx_rate)
    tagged_keys = _existing_tag_keys(db)
    unclassified_count = sum(1 for key in candidates if key not in tagged_keys)

    return {
        "ytd_sum": round(ytd_sum, 2),
        "trailing_12m_sum": round(trailing_12m_sum, 2),
        "monthly_series": monthly_series,
        "unclassified_count": unclassified_count,
    }
