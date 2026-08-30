"""Behavioral metrics computation — pure SQL/Python, no LLM calls.

Computes 6 investor behavior dimensions from trade history and portfolio data.
Each dimension is normalized to a 0.0-1.0 score with a human-readable label.

Data sources (priority order):
- `transactions` table — primary source for all trade-dependent metrics
- `trade_logs` table — supplementary AI-specific records (may supplement reasoning context)
- `holdings`, `target_allocations`, `taxonomy_classes` — allocation-based metrics
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import duckdb

from src.services.decision_scorer import _resolve_market_codes
from src.services.verification_config import load_verification_config

logger = logging.getLogger(__name__)

# transaction_type values treated as buys / sells in the transactions table
_BUY_TYPES = ("'buy'", "'Buy'", "'dividend_reinvest'", "'transfer_in'", "'vest'")
_SELL_TYPES = ("'sell'", "'Sell'", "'transfer_out'")

_BUY_IN = ", ".join(_BUY_TYPES)
_SELL_IN = ", ".join(_SELL_TYPES)


@dataclass
class MetricResult:
    dimension: str
    score: float          # 0.0-1.0 normalized
    raw_value: float      # raw computed value (%, days, etc.)
    computation_window_days: int
    label: str            # human-readable label for the value
    description: str      # explanation of what it means
    # Optional extra structured payload. Introduced for F4.1 (PRD
    # 2026-07-07): rebalance_discipline exposes both the design-intent
    # (carved) drift figures and the raw (un-carved) drift here so callers
    # can display "after design-intent exclusions" transparently. Additive
    # field — every other metric leaves this None.
    metadata: Optional[dict] = None


class BehavioralMetricsComputer:
    def __init__(self, db_path: str = "data/unified.duckdb"):
        self._db_path = db_path

    @contextmanager
    def _conn_context(self, conn: Optional[object] = None, read_only: bool = True) -> Iterator:
        """Yield *conn* if provided; otherwise open and close a local DuckDB connection.

        When the caller supplies an existing connection (e.g. the orchestrator's
        write connection), this is a zero-overhead pass-through — the context
        manager does NOT close the caller's connection on exit.  This satisfies
        the V7.0.0 DuckDB-connection-model constraint: a single open read-write
        connection must not be mixed with a second read-only connection on the
        same file within the same process.
        """
        if conn is not None:
            yield conn
        else:
            local = duckdb.connect(self._db_path, read_only=read_only)
            try:
                yield local
            finally:
                local.close()

    def _ai_active_since(self, conn: Optional[object] = None) -> Optional[str]:
        """Return the earliest AI-advisory date as 'YYYY-MM-DD', or None if no memos exist.

        Uses MIN(memo_date) from strategy_memos — the actual date memos were written,
        not the import/created_at timestamp. This reflects when AI-driven analysis began.
        """
        try:
            with self._conn_context(conn, read_only=True) as _conn:
                row = _conn.execute("SELECT MIN(memo_date) FROM strategy_memos").fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception as e:
            logger.warning(f"_ai_active_since failed: {e}")
        return None

    def compute_all(self, window_days: int = 90, conn: Optional[object] = None) -> list:  # type: ignore[type-arg]
        """Compute all behavioral dimensions. Returns list of MetricResult, one per dimension.

        8 MetricResults total: the original 6 dimensions, PLUS (PRD 2026-07-07 F5)
        two decomposed contrarian sub-dimensions — 'systematic_contrarian' and
        'manual_contrarian' — computed from trade_logs.order_origin. The legacy
        'contrarian_tendency' dimension is kept unchanged (backward compatibility
        for stored ai_behavioral_log history and any code reading that dimension
        name) but is now flagged deprecated via metadata.

        Args:
            window_days: Look-back window for trade history (default 90).
            conn: Optional existing DB connection to reuse (V7.0.0 model — never
                  opens a second connection while the caller's write connection is
                  live).  When None, each method opens its own short-lived
                  read-only connection as before.
        """
        results = []
        methods = [
            self._contrarian_tendency,
            self._systematic_contrarian,
            self._manual_contrarian,
            self._position_sizing_discipline,
            self._decision_speed,
            self._loss_tolerance,
            self._strategy_compliance,
            self._rebalance_discipline,
        ]
        for method in methods:
            try:
                # conn=None → each method's _conn_context opens its own local
                # connection; identical to the old no-kwarg call.
                result = method(window_days, conn=conn)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to compute {method.__name__}: {e}")
                results.append(MetricResult(
                    dimension=method.__name__.lstrip('_'),
                    score=0.0,
                    raw_value=0.0,
                    computation_window_days=window_days,
                    label="N/A",
                    description="Insufficient data to compute",
                ))
        return results

    def save_to_db(self, results: list, conn: Optional[object] = None) -> None:  # type: ignore[type-arg]
        """Persist computed metrics to ai_behavioral_log.

        Args:
            results: List of MetricResult instances.
            conn: Optional existing write connection to reuse (see compute_all).
                  When None, opens its own short-lived read-write connection.
        """
        try:
            with self._conn_context(conn, read_only=False) as _conn:
                for r in results:
                    payload = {"label": r.label, "description": r.description}
                    if r.metadata:
                        payload["metadata"] = r.metadata
                    _conn.execute(
                        """INSERT INTO ai_behavioral_log
                           (dimension, score, raw_value, computation_window_days, metadata_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        [
                            r.dimension,
                            r.score,
                            r.raw_value,
                            r.computation_window_days,
                            json.dumps(payload),
                        ],
                    )
        except Exception as e:
            logger.error(f"Failed to save behavioral metrics: {e}")

    # ------------------------------------------------------------------
    # Private metric methods
    # ------------------------------------------------------------------

    # Legacy metadata attached to every _contrarian_tendency return (PRD 2026-07-07
    # F5): this conflated dimension is superseded by systematic_contrarian /
    # manual_contrarian below, which decompose it by order_origin. Kept emitting
    # unchanged for backward compatibility of stored ai_behavioral_log history and
    # any existing UI/reporting code still reading this dimension name.
    _CONTRARIAN_TENDENCY_DEPRECATED_METADATA = {
        "deprecated": True,
        "replaced_by": ["systematic_contrarian", "manual_contrarian"],
    }

    def _contrarian_tendency(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Fraction of buy trades made on market down-days (>2% drop).

        Primary source: ``transactions`` table (transaction_type IN buy variants).
        Falls back gracefully when no market_daily data overlaps.

        DEPRECATED (PRD 2026-07-07 F5): this conflates preset/automated contrarian
        execution (discipline) with manual contrarian decisions (the owner's
        documented weakness), which makes it misleading as a behavioral signal on
        its own. See _systematic_contrarian / _manual_contrarian for the
        order_origin-aware decomposition. This method is kept byte-for-byte
        unchanged (aside from the deprecated metadata tag below) so historical
        ai_behavioral_log rows and any code still reading 'contrarian_tendency'
        keep working.
        """
        sql = f"""
        WITH trade_dates AS (
            SELECT transaction_date AS log_date, asset_id
            FROM transactions
            WHERE LOWER(transaction_type) IN ('buy', 'vest', 'transfer_in', 'dividend_reinvest')
              AND transaction_date >= CURRENT_DATE - INTERVAL '{window_days}' DAY
        ),
        market_returns AS (
            SELECT t.log_date AS trade_date,
                   (SELECT m.close / NULLIF(m2.close, 0) - 1
                    FROM market_daily m
                    JOIN market_daily m2 ON m2.date = (
                        SELECT MAX(date) FROM market_daily
                        WHERE date < m.date AND code = m.code
                    )
                    WHERE m.date = (
                        SELECT MAX(date) FROM market_daily
                        WHERE date <= t.log_date AND date >= t.log_date - INTERVAL '3' DAY
                    )
                    AND m.code IN ('110020', '000300', 'SPY', '^GSPC')
                    LIMIT 1
                   ) AS market_return_pct
            FROM trade_dates t
        )
        SELECT COUNT(*) FILTER (WHERE market_return_pct < -0.02) AS contrarian_buys,
               COUNT(*) AS total_buys
        FROM market_returns
        WHERE market_return_pct IS NOT NULL
        """
        with self._conn_context(conn, read_only=True) as _conn:
            row = _conn.execute(sql).fetchone()

        contrarian_buys = row[0] if row else 0
        total_buys = row[1] if row else 0

        if total_buys < 3:
            return MetricResult(
                dimension="contrarian_tendency",
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="Insufficient transaction data",
                description="Need ≥3 buy trades with market data to compute contrarian tendency",
                metadata=dict(self._CONTRARIAN_TENDENCY_DEPRECATED_METADATA),
            )

        score = contrarian_buys / total_buys
        raw_value = score * 100

        return MetricResult(
            dimension="contrarian_tendency",
            score=score,
            raw_value=raw_value,
            computation_window_days=window_days,
            label=f"{raw_value:.1f}% contrarian buy rate",
            description="Fraction of buys made on market down-days (>2% drop)",
            metadata=dict(self._CONTRARIAN_TENDENCY_DEPRECATED_METADATA),
        )

    # ------------------------------------------------------------------
    # F5 — Contrarian metric decomposition (PRD 2026-07-07)
    # ------------------------------------------------------------------
    #
    # The legacy _contrarian_tendency() above conflates two very different
    # behaviors under one "contrarian buy rate" number:
    #   - preset/automated execution (auto_dca, conditional_order) landing in a
    #     drawdown window — this is discipline: the owner pre-committed to a
    #     rule and the rule happened to fire during a dip. Higher is better.
    #   - manual buys placed by hand during a drawdown — this is the owner's
    #     documented weakness (manual dip-buying at lows, historically often
    #     followed by further declines). This is a WATCHED behavior, not a
    #     virtue, so it must not be rewarded by the radar geometry.
    #
    # Both dimensions below share:
    #   - the same buy universe: trade_logs rows with LOWER(action) = 'buy' in
    #     the trailing window_days (trade_logs, NOT transactions — order_origin
    #     only exists on trade_logs, per migration 010/PRD F1.1).
    #   - the same drawdown-window definition (see _is_buy_in_drawdown_window).
    #   - the same honesty rules: rows with order_origin NULL/blank are EXCLUDED
    #     from both metrics (never silently defaulted to either side) and
    #     reported via metadata['untagged_count']; rows whose instrument has no
    #     market_daily price data are EXCLUDED and reported via
    #     metadata['excluded_no_price_count'].

    @staticmethod
    def _classify_order_origin(raw_origin: Optional[str]) -> Optional[str]:
        """Map a raw trade_logs.order_origin value to 'systematic' | 'manual' | None.

        None means "exclude from both F5 metrics" — either the value is
        NULL/blank (untagged, the common case pre-backfill) or it is some
        unrecognized value (defensive: treated the same as untagged rather than
        guessed into either bucket).
        """
        origin = (raw_origin or "").strip().lower()
        if origin in ("auto_dca", "conditional_order"):
            return "systematic"
        if origin == "manual":
            return "manual"
        return None

    def _fetch_contrarian_buys(self, window_days: int, conn: Optional[object] = None) -> tuple[list[dict], int]:
        """Return (buys, untagged_count) for all trade_logs buys in the window.

        buys: list of {'asset_id', 'log_date', 'group'} for rows with a
        recognized order_origin ('systematic' or 'manual'). untagged_count is the
        count of buy rows excluded because order_origin was NULL/blank/unrecognized.
        """
        sql = f"""
        SELECT asset_id, log_date, order_origin
        FROM trade_logs
        WHERE LOWER(action) = 'buy'
          AND log_date >= CURRENT_DATE - INTERVAL '{window_days}' DAY
        """
        with self._conn_context(conn, read_only=True) as _conn:
            rows = _conn.execute(sql).fetchall()

        buys: list[dict] = []
        untagged_count = 0
        for asset_id, log_date, raw_origin in rows:
            group = self._classify_order_origin(raw_origin)
            if group is None:
                untagged_count += 1
                continue
            buys.append({"asset_id": asset_id, "log_date": log_date, "group": group})
        return buys, untagged_count

    @staticmethod
    def _is_buy_in_drawdown_window(
        conn: object,
        asset_id: str,
        log_date: object,
        window_trading_days: int,
        threshold_pct: float,
    ) -> Optional[bool]:
        """True/False whether *asset_id* was bought within a qualifying drawdown window.

        PRD 2026-07-07 F5/A2 interpretation: a buy is "contrarian" when
        (rolling_max_close - close_at_buy) / rolling_max_close * 100 >= threshold_pct,
        where rolling_max_close is the max close over the *window_trading_days*
        trading days ending at (and including) the trading day nearest at-or-before
        log_date. This reuses the instrument code-resolution helper
        (decision_scorer._resolve_market_codes) so canonical asset_ids resolve to
        the raw broker/market codes used in market_daily, exactly like the
        outcome-scoring path.

        Returns None (honest exclusion — NOT False) when no candidate market code
        has price data near log_date; callers must track this separately from a
        real "not contrarian" result (Cross-Cutting Requirement 3 — never fabricate
        data as if it were real).
        """
        candidates = _resolve_market_codes(conn, asset_id)
        if not candidates:
            return None

        sql = """
        WITH series AS (
            SELECT date, close, ROW_NUMBER() OVER (ORDER BY date) AS rn
            FROM market_daily
            WHERE code = ?
        ),
        buy_anchor AS (
            SELECT rn, close AS close_at_buy
            FROM series
            WHERE date <= ?
              AND date >= CAST(? AS DATE) - INTERVAL '7' DAY
            ORDER BY date DESC
            LIMIT 1
        )
        SELECT b.close_at_buy,
               (SELECT MAX(s.close) FROM series s
                WHERE s.rn <= b.rn AND s.rn > b.rn - ?) AS rolling_max
        FROM buy_anchor b
        """
        for code in candidates:
            try:
                row = conn.execute(sql, [code, log_date, log_date, window_trading_days]).fetchone()
            except Exception as exc:
                logger.debug(
                    "_is_buy_in_drawdown_window: query failed for code=%s asset_id=%s: %s",
                    code, asset_id, exc,
                )
                continue
            if not row or row[0] is None or row[1] is None:
                continue
            close_at_buy, rolling_max = float(row[0]), float(row[1])
            if rolling_max <= 0:
                continue
            drawdown_pct = (rolling_max - close_at_buy) / rolling_max * 100
            return drawdown_pct >= threshold_pct

        return None

    def _compute_contrarian_group_metric(
        self,
        dimension: str,
        group: str,
        window_days: int,
        conn: Optional[object] = None,
    ) -> MetricResult:
        """Shared computation for _systematic_contrarian / _manual_contrarian.

        group is 'systematic' or 'manual' — selects which order_origin bucket
        this MetricResult reports on. Both metrics use identical exclusion rules
        (untagged, no-price) and drawdown-window logic; only the scoring
        direction and alert logic differ, applied by the two thin wrappers below.
        """
        cfg = load_verification_config().contrarian

        with self._conn_context(conn, read_only=True) as _conn:
            buys, untagged_count = self._fetch_contrarian_buys(window_days, conn=_conn)
            group_buys = [b for b in buys if b["group"] == group]

            contrarian_count = 0
            excluded_no_price_count = 0
            month_counts: dict[str, int] = defaultdict(int)
            for b in group_buys:
                result = self._is_buy_in_drawdown_window(
                    _conn, b["asset_id"], b["log_date"],
                    cfg.drawdown_window_trading_days, cfg.drawdown_threshold_pct,
                )
                if result is None:
                    excluded_no_price_count += 1
                    continue
                if result:
                    contrarian_count += 1
                    month_key = str(b["log_date"])[:7]  # 'YYYY-MM'
                    month_counts[month_key] += 1

        priced_total = len(group_buys) - excluded_no_price_count

        # Nothing at all to work with (no buys in the window, or every buy in
        # the window was untagged — order_origin NULL/blank/unrecognized).
        # Cross-Cutting Requirement 3: render "insufficient data", never a
        # fabricated default styled as real.
        if len(buys) == 0:
            return MetricResult(
                dimension=dimension,
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="No data",
                description=(
                    "No tagged buys available to compute this dimension — all buys "
                    "in the window are untagged (order_origin NULL) or there were no "
                    "buys at all"
                ),
                metadata={"untagged_count": untagged_count, "excluded_no_price_count": 0},
            )

        if len(group_buys) == 0:
            # Tagged buys exist, but none belong to this group (e.g. every
            # tagged buy in the window was systematic and none were manual).
            return MetricResult(
                dimension=dimension,
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="No data",
                description=(
                    f"No {group} buys tagged in the window — all tagged buys "
                    "belong to the other origin group"
                ),
                metadata={"untagged_count": untagged_count, "excluded_no_price_count": 0},
            )

        if priced_total == 0:
            # There were buys tagged for this group, but none had usable price data.
            return MetricResult(
                dimension=dimension,
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="No price data",
                description=(
                    f"No market_daily price data available for any {group} buy in "
                    "the window — cannot evaluate drawdown-window membership"
                ),
                metadata={
                    "untagged_count": untagged_count,
                    "excluded_no_price_count": excluded_no_price_count,
                },
            )

        rate = contrarian_count / priced_total
        max_month_count = max(month_counts.values(), default=0)

        return MetricResult(
            dimension=dimension,
            score=rate,  # placeholder — overwritten by group-specific wrappers below
            raw_value=rate * 100,
            computation_window_days=window_days,
            label=f"{rate * 100:.1f}% {group} contrarian rate ({contrarian_count}/{priced_total})",
            description="",  # filled in by wrapper
            metadata={
                "untagged_count": untagged_count,
                "excluded_no_price_count": excluded_no_price_count,
                f"{group}_contrarian_buys": contrarian_count,
                f"{group}_total_buys": priced_total,
                "max_month_contrarian_count": max_month_count,
            },
        )

    def _systematic_contrarian(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Systematic (auto_dca | conditional_order) contrarian buy rate.

        Higher = better: a preset/automated order landing during a qualifying
        drawdown window reflects pre-committed execution discipline, not manual
        timing — the score maps directly to the rate (no neutral flattening).
        Scored OPPOSITE to manual_contrarian below: see that method's docstring
        for why the same underlying behavior (buying during a drawdown) is
        scored as a virtue here but neutrally there.
        """
        result = self._compute_contrarian_group_metric(
            "systematic_contrarian", "systematic", window_days, conn=conn
        )
        if result.label in ("No data", "No price data"):
            result.description = (
                "Fraction of preset/automated buys (auto_dca, conditional_order) "
                "executed within a qualifying drawdown window — insufficient data "
                "to compute (see metadata for untagged/no-price counts)"
            )
            return result

        result.description = (
            "Fraction of preset/automated buys (auto_dca, conditional_order) "
            "executed within a qualifying drawdown window, out of all such priced "
            "systematic buys. Higher is better: this measures preset EXECUTION "
            "DISCIPLINE (the order was pre-committed before the dip occurred), "
            "which is the opposite behavior from manual_contrarian (a human "
            "choosing to buy the dip by hand) — see that dimension's description."
        )
        return result

    def _manual_contrarian(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Manual contrarian buy rate — the WATCHED behavioral-weakness metric.

        Scored with a constant neutral score of 0.5 regardless of the raw rate.
        This is deliberate: the owner's documented weakness is manual dip-buying
        at lows (historically often followed by further declines), so a HIGH
        manual contrarian rate must not paint the radar green the way
        systematic_contrarian does for the same nominal behavior. The raw rate
        and an alert flag (metadata['alert']) carry the actual signal instead of
        the score/radar geometry, so this dimension cannot reward or punish via
        radar shape — only the alert and raw_value communicate the finding.

        alert = True when EITHER:
          - rate > cfg.contrarian.manual_alert_rate_pct, OR
          - manual dip-buys in any single calendar month of the window >
            cfg.contrarian.manual_alert_monthly_count
        """
        cfg = load_verification_config().contrarian
        result = self._compute_contrarian_group_metric(
            "manual_contrarian", "manual", window_days, conn=conn
        )
        if result.label in ("No data", "No price data"):
            result.description = (
                "Fraction of MANUAL buys executed within a qualifying drawdown "
                "window — insufficient data to compute (see metadata for "
                "untagged/no-price counts). This is the watched behavioral-weakness "
                "metric (manual dip-buying); scored neutrally (0.5), never rewarded."
            )
            result.metadata["alert"] = False
            return result

        # Force the neutral score — this metric must never reward/punish via
        # radar geometry (see docstring above).
        result.score = 0.5
        rate_pct = result.raw_value
        max_month_count = result.metadata.get("max_month_contrarian_count", 0)
        alert = (
            rate_pct > cfg.manual_alert_rate_pct
            or max_month_count > cfg.manual_alert_monthly_count
        )
        result.metadata["alert"] = alert
        result.metadata["manual_alert_rate_threshold_pct"] = cfg.manual_alert_rate_pct
        result.metadata["manual_alert_monthly_threshold"] = cfg.manual_alert_monthly_count
        result.description = (
            "Fraction of MANUAL buys executed within a qualifying drawdown window, "
            "out of all such priced manual buys. This is the WATCHED metric — the "
            "owner's documented weakness is manual dip-buying at lows — so it is "
            "scored neutrally (constant 0.5) rather than rewarded like "
            "systematic_contrarian, which reflects preset execution discipline "
            "instead of manual timing. See metadata['alert'] for the "
            "rate/monthly-count warning threshold."
        )
        return result

    def _position_sizing_discipline(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Average drift of current weights from target allocation.

        Queries holdings + target_allocations. Falls back if Strategic_Profile rows absent.
        """
        # Try Strategic_Profile first; fall back to any available source
        sql = """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        ),
        current_weights AS (
            SELECT h.asset_id,
                   COALESCE(ptc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                   100.0 * h.market_value / SUM(h.market_value) OVER() AS actual_pct
            FROM holdings h
            JOIN latest_per_asset lpa
                ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes ptc ON tc.parent_id = ptc.id
            WHERE h.is_shadow = FALSE AND h.market_value > 0
        ),
        targets AS (
            SELECT asset_class, target_pct FROM target_allocations
            WHERE source = 'Strategic_Profile'
        ),
        targets_fallback AS (
            SELECT asset_class, target_pct FROM target_allocations
            WHERE source IS NULL OR source != 'Strategic_Profile'
        ),
        effective_targets AS (
            SELECT asset_class, target_pct FROM targets
            UNION ALL
            SELECT asset_class, target_pct FROM targets_fallback
            WHERE NOT EXISTS (SELECT 1 FROM targets)
        ),
        diffs AS (
            SELECT ABS(COALESCE(cw.actual_pct, 0) - COALESCE(t.target_pct, 0)) AS abs_diff
            FROM current_weights cw
            FULL OUTER JOIN effective_targets t ON cw.top_class = t.asset_class
        )
        SELECT AVG(abs_diff) AS avg_drift FROM diffs
        """
        try:
            with self._conn_context(conn, read_only=True) as _conn:
                row = _conn.execute(sql).fetchone()

            avg_drift = row[0] if (row and row[0] is not None) else None

            if avg_drift is None:
                return MetricResult(
                    dimension="position_sizing_discipline",
                    score=0.5,
                    raw_value=0.0,
                    computation_window_days=window_days,
                    label="No target allocation data",
                    description="No target allocation data available",
                )

            # Linear interpolation: drift ≤2% → 1.0, drift ≥20% → 0.0
            if avg_drift <= 2.0:
                score = 1.0
            elif avg_drift >= 20.0:
                score = 0.0
            else:
                score = 1.0 - (avg_drift - 2.0) / 18.0

            return MetricResult(
                dimension="position_sizing_discipline",
                score=round(score, 4),
                raw_value=avg_drift,
                computation_window_days=window_days,
                label=f"avg drift {avg_drift:.1f}%",
                description="Average deviation of current holdings from target allocation",
            )
        except Exception as e:
            logger.warning(f"_position_sizing_discipline failed: {e}")
            return MetricResult(
                dimension="position_sizing_discipline",
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="N/A",
                description="暂无目标配置数据",
            )

    def _decision_speed(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Median days between insight creation and related trade execution.

        Primary source: joins ``ai_insights.entity_refs`` with ``transactions``
        on asset_id to find the first trade after each insight about that asset.
        Supplements with ``trade_logs`` when available (via linked_transaction_id).
        """
        # Primary: ai_insights → transactions join
        sql_primary = f"""
        WITH insight_assets AS (
            -- Unnest comma-separated entity_refs into one row per asset per insight
            SELECT i.id AS insight_id,
                   i.created_at::DATE AS insight_date,
                   TRIM(ref.val) AS asset_id
            FROM ai_insights i,
                 UNNEST(STRING_SPLIT(i.entity_refs, ',')) AS ref(val)
            WHERE i.entity_refs IS NOT NULL AND TRIM(i.entity_refs) != ''
        ),
        first_trade_after_insight AS (
            SELECT ia.insight_id,
                   ia.insight_date,
                   MIN(t.transaction_date) AS first_trade_date
            FROM insight_assets ia
            JOIN transactions t
                ON t.asset_id = ia.asset_id
               AND t.transaction_date >= ia.insight_date
               AND t.transaction_date <= ia.insight_date + INTERVAL '{window_days}' DAY
               AND LOWER(t.transaction_type) IN ('buy', 'sell', 'vest', 'transfer_in', 'transfer_out')
            GROUP BY ia.insight_id, ia.insight_date
        )
        SELECT MEDIAN(DATEDIFF('day', insight_date, first_trade_date)) AS median_days,
               COUNT(*) AS n_pairs
        FROM first_trade_after_insight
        WHERE first_trade_date IS NOT NULL
        """
        try:
            with self._conn_context(conn, read_only=True) as _conn:
                row = _conn.execute(sql_primary).fetchone()

            median_days = row[0] if (row and row[0] is not None) else None
            n_pairs = row[1] if row else 0

            if median_days is None or n_pairs < 3:
                return MetricResult(
                    dimension="decision_speed",
                    score=0.5,
                    raw_value=0.0,
                    computation_window_days=window_days,
                    label="Insufficient transaction data",
                    description="Need ≥3 insight→trade pairs to compute decision speed",
                )

            # Score by range
            if median_days < 7:
                score = 0.3   # too reactive
            elif median_days <= 30:
                score = 1.0   # optimal
            elif median_days <= 90:
                score = 0.7
            else:
                score = 0.3

            return MetricResult(
                dimension="decision_speed",
                score=score,
                raw_value=float(median_days),
                computation_window_days=window_days,
                label=f"median {median_days:.0f} day decision cycle",
                description="Median days from insight creation to related trade execution",
            )
        except Exception as e:
            logger.warning(f"_decision_speed failed: {e}")
            return MetricResult(
                dimension="decision_speed",
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="Insufficient transaction data",
                description="数据积累中",
            )

    def _loss_tolerance(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Average drawdown at time of selling a losing position.

        Primary source: ``transactions`` table.
        Finds sell transactions, computes average buy price from prior buy
        transactions for the same asset, then measures the loss %.
        """
        sql = f"""
        WITH sells AS (
            SELECT asset_id,
                   transaction_date AS sell_date,
                   price_unit        AS sell_price
            FROM transactions
            WHERE LOWER(transaction_type) IN ('sell')
              AND transaction_date >= CURRENT_DATE - INTERVAL '{window_days}' DAY
              AND price_unit > 0
        ),
        avg_buy_cost AS (
            -- Average buy price for each asset, from all prior buys
            SELECT s.asset_id,
                   s.sell_date,
                   s.sell_price,
                   AVG(b.price_unit) AS avg_buy_price
            FROM sells s
            JOIN transactions b
                ON b.asset_id = s.asset_id
               AND LOWER(b.transaction_type) IN ('buy', 'vest', 'dividend_reinvest', 'transfer_in')
               AND b.transaction_date < s.sell_date
               AND b.price_unit > 0
            GROUP BY s.asset_id, s.sell_date, s.sell_price
        ),
        loss_sells AS (
            SELECT asset_id,
                   (sell_price - avg_buy_price) / NULLIF(avg_buy_price, 0) * 100 AS loss_pct
            FROM avg_buy_cost
            WHERE sell_price < avg_buy_price
        ),
        drawdowns AS (
            SELECT asset_id, MIN(loss_pct) AS max_drawdown_pct
            FROM loss_sells
            GROUP BY asset_id
        )
        SELECT AVG(max_drawdown_pct) AS avg_loss_pct,
               COUNT(*) AS n_assets
        FROM drawdowns
        """
        with self._conn_context(conn, read_only=True) as _conn:
            row = _conn.execute(sql).fetchone()

        avg_loss_pct = row[0] if (row and row[0] is not None) else None
        n_assets = row[1] if row else 0

        if avg_loss_pct is None or n_assets < 1:
            return MetricResult(
                dimension="loss_tolerance",
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="Insufficient transaction data",
                description="No loss-taking sell records found in transactions",
            )

        raw_value = abs(avg_loss_pct)

        # Score by average loss size
        if raw_value < 5:
            score = 0.9   # cuts losses quickly
        elif raw_value < 15:
            score = 0.7
        elif raw_value < 30:
            score = 0.5
        else:
            score = 0.2

        return MetricResult(
            dimension="loss_tolerance",
            score=score,
            raw_value=raw_value,
            computation_window_days=window_days,
            label=f"avg loss cut {raw_value:.1f}%",
            description="Average drawdown when selling a losing position",
        )

    def _strategy_compliance(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Fraction of AI-era traded assets that appear in strategy memos.

        Only considers trades within the AI-active window (earliest strategy_memos.memo_date)
        to avoid penalising pre-AI-era brokerage history that can never match.
        The floor is ai_active_since directly — window_days is overridden by ai_since
        for this metric so that pre-AI trades are never counted in the denominator.
        """
        ai_since = self._ai_active_since(conn=conn)
        if ai_since is None:
            return MetricResult(
                dimension="strategy_compliance",
                score=0.5,
                raw_value=50.0,
                computation_window_days=window_days,
                label="No AI advisory data yet",
                description="Strategy compliance requires at least one AI brief or strategy memo",
            )

        from datetime import date
        effective_window_days = (date.today() - date.fromisoformat(str(ai_since))).days

        sql = f"""
        WITH ai_floor AS (
            SELECT '{ai_since}'::DATE AS floor_date
        ),
        recent_trades AS (
            SELECT DISTINCT asset_id
            FROM transactions, ai_floor
            WHERE transaction_date >= ai_floor.floor_date
              AND LOWER(transaction_type) IN ('buy', 'sell', 'vest', 'transfer_in', 'transfer_out')
        ),
        strategy_tickers AS (
            SELECT DISTINCT UPPER(ticker_raw) AS ticker
            FROM strategy_memos
            CROSS JOIN UNNEST(REGEXP_EXTRACT_ALL(
                COALESCE(title, '') || ' ' || CAST(COALESCE(key_directives, '') AS VARCHAR) || ' ' || CAST(COALESCE(content, '') AS VARCHAR),
                '[A-Za-z][A-Za-z0-9]{{2,9}}'
            )) AS t(ticker_raw)
            WHERE UPPER(ticker_raw) IN (
                SELECT DISTINCT UPPER(SPLIT_PART(canonical_id, '_', -1))
                FROM asset_registry
                WHERE NOT REGEXP_MATCHES(SPLIT_PART(canonical_id, '_', -1), '^[0-9]+$')
                  AND asset_class NOT IN ('Cash Checking', 'Bank Wealth', 'Property', 'Insurance', 'Insurance Products')
                  AND UPPER(SPLIT_PART(canonical_id, '_', -1)) NOT IN ('PERSONAL', 'NAN')
            )
        ),
        recent_trade_symbols AS (
            SELECT DISTINCT
                asset_id,
                UPPER(SPLIT_PART(asset_id, '_', -1)) AS symbol
            FROM recent_trades
        ),
        class_keywords(asset_class, keyword) AS (
            VALUES
                ('CN Equity', 'A股'),
                ('US Equity', 'QDII'), ('US Equity', '美股'), ('US Equity', '标普'),
                ('Money Market', '货币'),
                ('HK ETF', '港股'),
                ('CN Bonds', '债券'),
                ('Gold', '黄金')
        ),
        memo_full_text AS (
            SELECT COALESCE(title, '') || ' ' || CAST(COALESCE(key_directives, '') AS VARCHAR) || ' ' || CAST(COALESCE(content, '') AS VARCHAR) AS full_text
            FROM strategy_memos
        ),
        cn_fund_matches AS (
            SELECT DISTINCT rts.asset_id
            FROM recent_trade_symbols rts
            JOIN asset_registry ar ON rts.asset_id = ar.canonical_id
            JOIN class_keywords ck ON ar.asset_class = ck.asset_class
            WHERE EXISTS (
                  SELECT 1 FROM memo_full_text mt WHERE LOWER(mt.full_text) LIKE '%' || LOWER(ck.keyword) || '%'
              )
        )
        SELECT
            COUNT(DISTINCT rts.asset_id) FILTER (
                WHERE rts.symbol IN (SELECT ticker FROM strategy_tickers)
                   OR rts.asset_id IN (SELECT asset_id FROM cn_fund_matches)
            ) AS strategy_trades,
            COUNT(DISTINCT rts.asset_id) AS total_trades
        FROM recent_trade_symbols rts
        """
        try:
            with self._conn_context(conn, read_only=True) as _conn:
                row = _conn.execute(sql).fetchone()

            strategy_trades = row[0] if row else 0
            total_trades = row[1] if row else 0

            if total_trades == 0:
                return MetricResult(
                    dimension="strategy_compliance",
                    score=0.5,
                    raw_value=50.0,
                    computation_window_days=effective_window_days,
                    label="No trades in AI-active window",
                    description=f"No trades found within AI-active window (since {ai_since})",
                )

            score = strategy_trades / total_trades
            raw_value = score * 100

            return MetricResult(
                dimension="strategy_compliance",
                score=score,
                raw_value=raw_value,
                computation_window_days=effective_window_days,
                label=f"{raw_value:.0f}% trades within strategy (since {ai_since})",
                description=f"Fraction of AI-era trades involving assets mentioned in strategy memos (window: {ai_since} to present)",
            )
        except Exception as e:
            logger.warning(f"_strategy_compliance failed: {e}")
            return MetricResult(
                dimension="strategy_compliance",
                score=0.5,
                raw_value=50.0,
                computation_window_days=effective_window_days,
                label="N/A",
                description="No strategy memo data available",
            )

    @staticmethod
    def _summarize_top_class_drift(rows: list[dict]) -> tuple[int, float, dict[str, float]]:
        """Reduce compass_allocation top-level rows to (drifted_count, max_abs_drift_pp, per_class_drift)."""
        top_rows = [r for r in rows if r.get("is_top_level")]
        per_class = {str(r["asset_class"]): float(r["drift_pct"]) for r in top_rows}
        drifted = sum(1 for v in per_class.values() if abs(v) > 5)
        max_drift = max((abs(v) for v in per_class.values()), default=0.0)
        return drifted, max_drift, per_class

    def _rebalance_discipline(self, window_days: int, conn: Optional[object] = None) -> MetricResult:
        """Count of asset classes drifted >5% from target allocation.

        PRD 2026-07-07 F4.1 fix: this used to run its own SQL against the
        legacy `target_allocations` table (populated by `sync_target_allocations`,
        which was removed in ADR-003 Phase-9 PIS deprecation — that table is no
        longer written by the live pipeline). Joining `current_weights` to an
        empty/stale `effective_targets` CTE silently produced 0 matching rows,
        so `_rebalance_discipline` always reported "0 classes drifted" even
        while the official allocation table (compass_allocation /
        `/compass/allocation`, backed by the live `risk_profile_allocations` +
        `taxonomy_classes` engine) showed real drift (cash +12.0pp, equity
        -11.7pp). Fixed by recomputing drift from the SAME engine
        (`build_compass_allocation`) instead of a second, stale data path.
        """
        from src.services.compass_allocation import build_compass_allocation

        try:
            with self._conn_context(conn, read_only=True) as _conn:
                # Guard against "no active risk profile" (no target data at all) —
                # distinguish this from a real 0-drift result.
                has_targets = _conn.execute(
                    """
                    SELECT COUNT(*) FROM risk_profile_allocations rpa
                    JOIN risk_profiles rp ON rpa.profile_id = rp.id
                    WHERE rp.is_active = TRUE
                    """
                ).fetchone()
                if not has_targets or not has_targets[0]:
                    return MetricResult(
                        dimension="rebalance_discipline",
                        score=0.5,
                        raw_value=0.0,
                        computation_window_days=window_days,
                        label="No target allocation data",
                        description="No target allocation data available",
                    )

                # Carved view: same engine + same exclusions (Rule 7 — filtering
                # reads taxonomy_classes.is_rebalanceable, e.g. SGOV dual-role /
                # emergency-fund carve-outs) as the official allocation table.
                carved_rows = build_compass_allocation(_conn, include_non_rebalanceable=False)
                # Raw view: identical engine, no design-intent exclusions applied.
                raw_rows = build_compass_allocation(_conn, include_non_rebalanceable=True)

            if not isinstance(carved_rows, list) or not carved_rows:
                return MetricResult(
                    dimension="rebalance_discipline",
                    score=0.5,
                    raw_value=0.0,
                    computation_window_days=window_days,
                    label="No target allocation data",
                    description="No target allocation data available",
                )

            overdue, max_drift, carved_per_class = self._summarize_top_class_drift(carved_rows)
            raw_overdue, raw_max_drift, raw_per_class = self._summarize_top_class_drift(
                raw_rows if isinstance(raw_rows, list) else []
            )

            carve_outs_applied = carved_per_class.keys() != raw_per_class.keys() or any(
                abs(carved_per_class[k] - raw_per_class[k]) > 0.01
                for k in carved_per_class
                if k in raw_per_class
            )

            if overdue == 0:
                score = 1.0
            elif overdue == 1:
                score = 0.7
            elif overdue == 2:
                score = 0.4
            else:
                score = 0.1

            suffix = " (after design-intent exclusions)" if carve_outs_applied else ""
            description = (
                "Number of asset classes currently exceeding ±5pp drift from target "
                "allocation, computed from the same allocation engine (compass_allocation) "
                "as the official allocation table"
            )
            if carve_outs_applied:
                description += (
                    " — after design-intent exclusions (e.g. SGOV dual-role, "
                    "emergency-fund non-rebalanceable holdings); raw un-carved drift is "
                    "also available in metadata"
                )

            return MetricResult(
                dimension="rebalance_discipline",
                score=score,
                raw_value=float(overdue),
                computation_window_days=window_days,
                label=f"{int(overdue)} class(es) drifted >5%{suffix}",
                description=description,
                metadata={
                    "max_drift_pp": round(max_drift, 2),
                    "per_class_drift_pp": {k: round(v, 2) for k, v in carved_per_class.items()},
                    "carve_outs_applied": carve_outs_applied,
                    "raw_classes_drifted": raw_overdue,
                    "raw_max_drift_pp": round(raw_max_drift, 2),
                    "raw_per_class_drift_pp": {k: round(v, 2) for k, v in raw_per_class.items()},
                },
            )
        except Exception as e:
            logger.warning(f"_rebalance_discipline failed: {e}")
            return MetricResult(
                dimension="rebalance_discipline",
                score=0.5,
                raw_value=0.0,
                computation_window_days=window_days,
                label="N/A",
                description="暂无目标配置数据",
            )
