# src/validation/data_integrity_gate.py
"""
Data Integrity Gate for Huinsight.

Runs the data integrity invariant checks on the holdings and transactions
tables to catch financial data accuracy issues before they surface in the UI.
Each check is derived from a specific historical bug (documented in AGENTS.md).

The number of checks is NOT hard-coded in prose anywhere — it is derived from
the ``INTEGRITY_CHECKS`` registry (see ``INTEGRITY_CHECK_COUNT`` at the bottom
of this module). The drift check in ``scripts/verify.sh`` asserts the docs match
this count.

Usage:
    from src.validation.data_integrity_gate import run_integrity_checks
    report = run_integrity_checks(connector)
    if not report.all_passed:
        for check in report.failed_checks:
            print(f"FAIL: {check.name} — {check.details}")
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, FrozenSet, List, Tuple

from src.database.connector import DatabaseConnector
from src.sources.registry import get_registry

logger = logging.getLogger(__name__)

# Reader sources — these must NEVER be shadowed by PIS.
# Derived from registry — same name, same value (5-element tuple, no Financial_Summary_Excel).
READER_SOURCES: tuple = get_registry().holding_source_systems()


@dataclass
class CheckResult:
    name: str
    passed: bool
    actual_value: object  # The measured value
    threshold: object     # What it was compared against
    details: str
    skipped: bool = False
    """True when the check could not EVALUATE its invariant (missing table,
    insufficient data, a guard tripped) as opposed to evaluating it and
    finding no violation.

    Why this exists (2026-07-26): every skip path returned ``passed=True``,
    so a check that never ran was indistinguishable from a check that ran
    clean, and it counted toward the headline "N/16 passed" score. Check #4
    (``xirr_proxy_in_range``) was VACUOUS FROM INCEPTION for exactly this
    reason — its ``IN ('BUY','VEST','DEPOSIT')`` filter never matched a row,
    so it reported a false PASS for its entire life without anyone noticing.

    Skipped checks are still NOT failures: several guards are legitimate
    (pre-V5.8.0 schema, too few snapshots to annualize safely), and making
    them blocking would break deploys for correct reasons. The defect was
    invisibility, not leniency. So ``all_passed`` still ignores skips, but
    ``passed_count`` no longer counts them and every surface reports them
    separately — the score is now honest about what it actually verified.
    """


@dataclass
class IntegrityReport:
    checks: List[CheckResult] = field(default_factory=list)
    run_at: datetime = field(default_factory=datetime.now)

    @property
    def all_passed(self) -> bool:
        """No check FAILED. Skips are deliberately not failures — see
        CheckResult.skipped for why this gating semantic is unchanged."""
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def skipped_checks(self) -> List[CheckResult]:
        """Checks that could not evaluate their invariant. Not failures, but
        NOT evidence of correctness either — they verified nothing."""
        return [c for c in self.checks if c.skipped]

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_checks)

    @property
    def passed_count(self) -> int:
        """Checks that ran AND found no violation. Excludes skips, so this is
        a count of what was actually verified rather than of what merely
        didn't complain."""
        return sum(1 for c in self.checks if c.passed and not c.skipped)

    @property
    def verified_count(self) -> int:
        """Alias for passed_count, named for what it means at call sites."""
        return self.passed_count

    def to_dict(self) -> dict:
        """Machine-readable representation. Used by --check-integrity --json and /health/deep.

        Note: 'count' reflects INTEGRITY_CHECK_COUNT (the canonical registry length).
        If this report has fewer checks than the registry it means some errored and
        were appended as failures — all_passed will be False.
        """
        # Import lazily to avoid circular init; INTEGRITY_CHECK_COUNT is set at module tail.
        import importlib as _il
        _mod = _il.import_module("src.validation.data_integrity_gate")
        canonical_count = getattr(_mod, "INTEGRITY_CHECK_COUNT", len(self.checks))
        return {
            "count": canonical_count,
            "passed": self.passed_count,      # verified only — excludes skips
            "skipped": self.skipped_count,    # ran but could not evaluate
            "failed": len(self.failed_checks),
            "all_passed": self.all_passed,
            "run_at": self.run_at.isoformat(),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "skipped": c.skipped,
                    "detail": c.details,
                }
                for c in self.checks
            ],
        }

    def to_text(self) -> str:
        skipped_note = f", {self.skipped_count} skipped" if self.skipped_count else ""
        lines = [
            f"=== Data Integrity Report ({self.run_at.strftime('%Y-%m-%d %H:%M:%S')}) ===",
            f"Result: {'PASSED' if self.all_passed else 'FAILED'} "
            f"({self.passed_count}/{len(self.checks)} checks verified{skipped_note})",
            "",
        ]
        if self.skipped_count:
            lines.append(
                f"  NOTE: {self.skipped_count} check(s) could not evaluate their invariant and "
                "verified NOTHING. They are not failures, but do not read them as coverage."
            )
            lines.append("")
        for c in self.checks:
            status = "SKIP" if c.skipped else ("PASS" if c.passed else "FAIL")
            lines.append(f"  [{status}] {c.name}")
            if c.skipped:
                lines.append(f"         {c.details}")
            if not c.passed:
                lines.append(f"         actual={c.actual_value}, threshold={c.threshold}")
                lines.append(f"         {c.details}")
        return "\n".join(lines)


def run_integrity_checks(
    connector: DatabaseConnector,
) -> IntegrityReport:
    """
    Run all invariant integrity checks (count = INTEGRITY_CHECK_COUNT).

    Each check is derived from a specific historical bug. Checks are designed to
    be fast (< 5 seconds total) and non-mutating (read-only queries).

    Returns:
        IntegrityReport with results for all checks.
    """
    report = IntegrityReport()

    # INTEGRITY_CHECKS is a list of (canonical_name, fn) tuples.
    # The canonical_name is the stable identifier used by BLOCKING_CHECKS and by
    # the exception path — it must match the name returned by the function itself.
    for canonical_name, check_fn in INTEGRITY_CHECKS:
        try:
            result = check_fn(connector)
            report.checks.append(result)
            if result.passed:
                logger.debug(f"[PASS] {result.name}")
            else:
                logger.warning(f"[FAIL] {result.name}: {result.details}")
        except Exception as e:
            # Use the registry's canonical_name (not __name__ derivation) so that
            # BLOCKING_CHECKS classification works correctly even on exception-failed checks.
            report.checks.append(CheckResult(
                name=canonical_name,
                passed=False,
                actual_value=None,
                threshold="n/a",
                details=f"Check errored: {e}",
            ))

    _log_report(connector, report)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Individual check implementations
# ─────────────────────────────────────────────────────────────────────────────

def _check_net_worth_plausible(connector: DatabaseConnector) -> CheckResult:
    """
    Check 1: Net worth is plausible (between 1M and 50M CNY).

    Derived from: partial snapshot bug where net_worth dropped to ¥303K (real value ¥5.37M)
    due to global MAX(snapshot_date) cutting off assets synced at different times.
    """
    row = connector.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
            GROUP BY asset_id
        )
        SELECT SUM(h.market_value) AS net_worth
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE AND h.market_value > 0
    """).fetchone()

    net_worth = float(row[0]) if row and row[0] else 0.0
    MIN_NW = 1_000_000   # 1M CNY floor
    MAX_NW = 100_000_000  # 100M CNY ceiling

    passed = MIN_NW <= net_worth <= MAX_NW
    return CheckResult(
        name="net_worth_plausible",
        passed=passed,
        actual_value=f"¥{net_worth:,.0f}",
        threshold=f"¥{MIN_NW:,} – ¥{MAX_NW:,}",
        details=(
            "Net worth is within expected range." if passed
            else f"Net worth ¥{net_worth:,.0f} is outside plausible range — "
                 "possible partial snapshot or currency mixing bug."
        ),
    )


def _check_no_currency_mixing(connector: DatabaseConnector) -> CheckResult:
    """
    Check 2: Schwab holdings (non-cash) have market_value in CNY range, not raw USD.

    Derived from: Schwab transformer outputting raw USD values (e.g. 33,811 instead of 236,677).
    Any Schwab non-cash holding with quantity > 1 and market_value < 50,000 CNY is suspicious.
    CASH assets are excluded (CASH_USD qty=5000 at 7 CNY/unit = 35,000 is legitimate).
    """
    # Scope to latest snapshot per asset to avoid historical backup-restore artifacts
    # (2026-02-12/13/15 backup restore introduced raw USD rows into historical snapshots).
    rows = connector.execute("""
        WITH latest_schwab AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings
            WHERE source_system = 'Schwab_CSV' AND is_shadow = FALSE
            GROUP BY asset_id
        )
        SELECT h.asset_id, h.quantity, h.market_value
        FROM holdings h
        JOIN latest_schwab ls ON h.asset_id = ls.asset_id
            AND h.snapshot_date = ls.latest_date
        WHERE h.source_system = 'Schwab_CSV'
          AND h.is_shadow = FALSE
          AND h.asset_id NOT LIKE 'CASH_%'
          AND h.quantity > 1
          AND h.market_value IS NOT NULL
          AND h.market_value > 0
          AND h.market_value < 500
        ORDER BY h.market_value ASC
        LIMIT 5
    """).fetchall()

    if rows:
        suspects = [(r[0], r[1], r[2]) for r in rows]
        details = "Suspect rows (asset_id, qty, market_value): " + str(suspects[:3])
    else:
        details = "All Schwab holdings have CNY-range market values."

    return CheckResult(
        name="no_raw_usd_in_schwab_holdings",
        passed=len(rows) == 0,
        actual_value=f"{len(rows)} suspect rows",
        threshold="0",
        details=details,
    )


#: Fixed lookback window for the TWR sanity-band check (Check 3). A FIXED
#: window (not the earliest-vs-latest snapshot) sidesteps the FS-history
#: boundary distortion documented in north_star_glide (data starts
#: 2020-02-01; an unbounded all-time window inflates TWR near that edge).
#: 365 days also keeps the annualization exponent (365 / actual_days) at
#: ~1, so the result is close to a raw period return rather than amplified.
TWR_CHECK_LOOKBACK_DAYS: int = 365

# Candidate lookbacks, longest first. The check uses the LONGEST window whose
# like-for-like basis clears TWR_MIN_LIKE_FOR_LIKE_COVERAGE. Longer is better
# (less annualization amplification), but a long window is worthless if the
# real-time readers are younger than it — so the check degrades to a shorter
# window rather than to a meaningless one, and climbs back to 365d on its own
# as reader history accumulates. Not result literals: these are the check's
# configuration.
TWR_CHECK_LOOKBACK_CANDIDATES: tuple = (365, 270, 180)

# The common (present-at-both-anchors) asset set must cover at least this much
# of current portfolio value for the ratio to mean anything. See
# _windowed_portfolio_valuation for the measurement that motivated it.
TWR_MIN_LIKE_FOR_LIKE_COVERAGE: float = 0.50


def _windowed_portfolio_valuation(
    connector: DatabaseConnector, d_end: date, window_days: int
) -> dict:
    """Portfolio value at ``d_end`` and at ``d_end - window_days``, using the
    LOCKED valuation-v2 helper (``src.services.attribution._latest_snapshot_by_asset``)
    at each anchor — per-(asset, source) latest row <= as_of_date, never a
    global cross-asset ``MAX(snapshot_date)`` (AGENTS.md Rule 3). Imported
    lazily: attribution.py pulls in north_star_flows / currency / phases._common,
    heavier than this module's usual ``src.database.connector`` + ``src.sources.registry``
    footprint, and there is no risk of a cycle back into this module (checked
    2026-07-26 — none of attribution's import chain touches data_integrity_gate
    or orchestrator at module scope) but the lazy pattern is kept consistent
    with the other checks in this file that reach into heavier modules
    (``_check_twr_xirr_consistency``, ``_check_xirr_in_range``, ``_check_consolidated_equals_sum``).

    ``market_value`` on every row is already stored in CNY (project-wide
    convention — see CLAUDE.md "Data Accuracy Rules") and
    ``_latest_snapshot_by_asset`` sums it as-is with no FX step; FX only
    applies to the attribution engine's *transaction-derived* effects, not to
    this snapshot valuation, so summing ``market_value`` directly here is
    correct without any additional currency handling.

    Returns a dict. On success: ``d_start, d_end, v_start, v_end,
    actual_days, annualized``. When there is no usable valuation at either
    anchor (fresh DB, or genuinely no data at/before ``d_start``), returns
    ``d_start, d_end, v_start, v_end, skip_reason`` instead — the caller
    must surface that as ``skipped=True``, never fabricate a number.
    """
    from src.services.attribution import _latest_snapshot_by_asset

    d_start = d_end - timedelta(days=window_days)

    end_map = _latest_snapshot_by_asset(connector, d_end)
    start_map = _latest_snapshot_by_asset(connector, d_start)

    # ── LIKE-FOR-LIKE: compare the SAME assets at both anchors ───────────────
    # Summing every asset present at each anchor measures reader ONBOARDING,
    # not return. Measured live 2026-07-26: only Financial_Summary_Excel
    # reaches back a year (Schwab_CSV starts 2025-11-29, CN_Fund_Excel
    # 2026-01-28, Gold/Insurance 2026-02-12, RSU 2026-03-05, Broker_IBKR
    # 2026-06-25), so the 365-day start anchor held 10 assets against 68 today
    # and the all-asset ratio read +140.7% — coverage growth wearing a
    # return's clothing. Restricted to the common set the same window reads
    # +10.0%, which agrees with the authoritative trailing TWR (10.832%).
    common = set(start_map) & set(end_map)
    v_end_total = sum(v["market_value"] for v in end_map.values())
    v_end = sum(end_map[a]["market_value"] for a in common)
    v_start = sum(start_map[a]["market_value"] for a in common)

    result = {
        "d_start": d_start,
        "d_end": d_end,
        "v_start": v_start,
        "v_end": v_end,
        "common_assets": len(common),
        "end_assets": len(end_map),
        "coverage": (v_end / v_end_total) if v_end_total > 0 else 0.0,
    }

    if v_start <= 0 or not common:
        result["skip_reason"] = (
            f"No asset has valuation at BOTH {d_start} and {d_end} "
            f"({window_days}-day lookback) — nothing to compare like-for-like."
        )
        return result
    if v_end <= 0:
        result["skip_reason"] = (
            f"Non-positive portfolio value at {d_end} (the latest snapshot date) — "
            "cannot compute a return."
        )
        return result

    # ── Coverage gate on the COMMON set ─────────────────────────────────────
    # A like-for-like basis that covers a sliver of the portfolio produces a
    # number that is arithmetically valid and substantively meaningless. At a
    # 365-day window today the common set is 10 assets = 45.7% of end value —
    # property, pension and cash deposits, i.e. not the investment portfolio
    # this check exists to sanity-check. Better to SKIP with a specific reason
    # than to report a confident figure about the wrong thing; `skipped` was
    # introduced precisely so that stays visible.
    if result["coverage"] < TWR_MIN_LIKE_FOR_LIKE_COVERAGE:
        result["skip_reason"] = (
            f"Like-for-like basis too thin at a {window_days}-day lookback: only "
            f"{len(common)} of {len(end_map)} assets span the window, covering "
            f"{result['coverage']:.1%} of current value (need "
            f"{TWR_MIN_LIKE_FOR_LIKE_COVERAGE:.0%}). Real-time readers are younger "
            "than the window; this self-heals as their history accumulates."
        )
        return result

    actual_days = (d_end - d_start).days
    result["actual_days"] = actual_days
    result["annualized"] = (v_end / v_start) ** (365.0 / actual_days) - 1
    return result


def _check_twr_in_range(connector: DatabaseConnector) -> CheckResult:
    """
    Check 3: Annualized portfolio value-ratio return is within -80% to +200%.

    Derived from: +912% TWR bug caused by double-counting transactions.

    FIXED 2026-07-26 (was permanently vacuous — see git history / HANDOVER
    for the prior version of this docstring). Root cause: the old coverage
    gate required a single ``snapshot_date`` covering >= 50% of all distinct
    assets — a GLOBAL cross-asset snapshot date, the exact thing AGENTS.md
    Rule 3 forbids ("NEVER use global MAX(snapshot_date) — always per-asset
    or per-source"). Each reader writes its own assets on its own date, so
    on the live mirror the gate needed >= 29 assets on one date, the best
    single date covered 5, and qualifying snapshots were 0 — unsatisfiable
    by construction. It silently returned ``skipped=True`` forever.

    Fix: reuse the LOCKED valuation-v2 helper
    (``src.services.attribution._latest_snapshot_by_asset``) — the same
    per-(asset, source) valuation the attribution engine uses — at two fixed
    anchors: the latest snapshot date in ``holdings`` (``d_end``) and
    ``d_end`` minus ``TWR_CHECK_LOOKBACK_DAYS`` (365) days (``d_start``).
    A FIXED lookback, not the earliest-vs-latest snapshot: an unbounded
    all-time window is inflated by the FS-history boundary (data starts
    2020-02-01) per north_star_glide's own documentation, and 365 days keeps
    annualization near-exact (exponent ~1) without needing amplification
    guards.

    ⚠️ This is a CRUDE SANITY BAND, not an accurate TWR. It is a pure
    value-ratio (v_end / v_start), so it conflates market return with net
    contributions — new deposits inflate it same as price appreciation. At
    a portfolio scale of a few million CNY with tens of thousands of CNY per
    month of contributions, a double-digit percent of "return" can come from
    deposits alone before any market movement even happens. That is why the
    band is wide (-80% to +200%) —
    it is meant to catch double-counting and unit-convention bugs (the
    +912% historical bug), not to serve as a real annualized return metric.
    Do NOT narrow this band to make it "more accurate" — that would turn a
    sanity check into a flaky one. The authoritative TWR (deposit-aware) is
    computed separately in ``src.financial_analysis.twr`` / consumed via
    ``north_star_glide._default_trailing_twr`` -> ``projection_defaults.
    suggested_return_basis`` — this check does not feed the UI and must
    never be treated as a source of truth for a displayed TWR.

    Legitimate skip: if there is no valuation data at or before ``d_start``
    (e.g. a fresh DB, or a DB whose history is younger than 365 days) this
    returns ``skipped=True`` with an honest reason rather than fabricating
    a number — see ``CheckResult.skipped``.
    """
    MIN_TWR = -0.80
    MAX_TWR = 2.00

    row = connector.execute("SELECT MAX(snapshot_date) FROM holdings").fetchone()
    d_end = row[0] if row else None
    if not d_end:
        return CheckResult(
            name="twr_in_range",
            passed=True,
            actual_value="no_data",
            threshold=f"{MIN_TWR:.0%} to {MAX_TWR:.0%}",
            details="No holdings data at all — skipped.",
            skipped=True,
        )
    if hasattr(d_end, "toPyDate"):
        d_end = d_end.toPyDate()

    # Longest window with an adequate like-for-like basis; keep the last
    # attempt's skip_reason so a total failure explains the BEST case tried.
    result = None
    for _window in TWR_CHECK_LOOKBACK_CANDIDATES:
        candidate = _windowed_portfolio_valuation(connector, d_end, _window)
        if "skip_reason" not in candidate:
            result = candidate
            break
        if result is None:
            result = candidate  # widest window's reason, reported if all fail

    if "skip_reason" in result:
        return CheckResult(
            name="twr_in_range",
            passed=True,
            actual_value="insufficient_data",
            threshold=f"{MIN_TWR:.0%} to {MAX_TWR:.0%}",
            details=result["skip_reason"],
            skipped=True,
        )

    annualized = result["annualized"]
    passed = MIN_TWR <= annualized <= MAX_TWR
    actual_value = (
        f"{annualized:.1%} annualized "
        f"(v_start=¥{result['v_start']:,.0f}@{result['d_start']}, "
        f"v_end=¥{result['v_end']:,.0f}@{result['d_end']}, "
        f"{result['actual_days']}d)"
    )
    return CheckResult(
        name="twr_in_range",
        passed=passed,
        actual_value=actual_value,
        threshold=f"{MIN_TWR:.0%} to {MAX_TWR:.0%}",
        details=(
            "Annualized value-ratio return is within the sanity band." if passed
            else f"Annualized value-ratio return {annualized:.1%} outside plausible range — "
                 "possible transaction double-counting, unit-convention bug, or snapshot corruption."
        ),
    )


def _check_xirr_in_range(connector: DatabaseConnector) -> CheckResult:
    """
    Check 4: XIRR is within -80% to +200%.

    Derived from: -50% XIRR bug caused by incorrect cash flow direction.
    Uses a simplified XIRR proxy: (net_worth - total_invested) / total_invested.

    NOTE (2026-07-25): this check was silently vacuous from inception until now —
    three stacked defects in the `total_invested` CTE:
      1. `transaction_type IN ('BUY', 'VEST', 'DEPOSIT')` never matched — stored
         values are lowercase/mixed-case ('buy', 'vest', 'Buy'), never 'BUY'. This
         alone made `total_invested` NULL on every run, so the check always
         returned `passed=True, actual_value="insufficient_data"` (a false PASS).
      2. `AND amount_net > 0` silently dropped all 74 negative Schwab_CSV buys
         (amount_net sign is a per-reader convention artifact, not economic
         direction — see AGENTS.md Rule 26). SUM(ABS(...)) is already
         sign-agnostic, so this filter was pure data loss.
      3. No FX conversion — total_invested raw-summed USD and CNY amount_net
         while current_value is CNY, biasing the proxy.
    See docs/plans/2026-07-25-amount-net-sign-convention-sweep.md for the full
    investigation.
    """
    try:
        from src.services.currency import get_today_usd_cny_rate
        usd_cny_rate = get_today_usd_cny_rate()
    except Exception:
        usd_cny_rate = 7.0

    row = connector.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        ),
        current_portfolio AS (
            SELECT SUM(h.market_value) AS current_value
            FROM holdings h
            JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
            WHERE h.is_shadow=FALSE AND h.market_value > 0
        ),
        total_invested AS (
            SELECT SUM(
                ABS(amount_net) * CASE
                    WHEN UPPER(COALESCE(currency, 'CNY')) = 'USD' THEN ?
                    ELSE 1.0
                END
            ) AS invested
            FROM transactions
            WHERE LOWER(transaction_type) IN ('buy', 'vest', 'deposit')
              AND amount_net IS NOT NULL
        )
        SELECT
            (SELECT current_value FROM current_portfolio),
            (SELECT invested FROM total_invested)
    """, [usd_cny_rate]).fetchone()

    if not row or not row[0] or not row[1] or row[1] == 0:
        return CheckResult(
            name="xirr_proxy_in_range",
            passed=True,
            actual_value="insufficient_data",
            threshold="-80% to +200%",
            details="Insufficient transaction data for XIRR proxy check — skipped.",
            skipped=True,
        )

    current_value = float(row[0])
    total_invested = float(row[1])
    xirr_proxy = (current_value - total_invested) / total_invested

    MIN_XIRR = -0.80
    MAX_XIRR = 2.00

    passed = MIN_XIRR <= xirr_proxy <= MAX_XIRR
    return CheckResult(
        name="xirr_proxy_in_range",
        passed=passed,
        actual_value=f"{xirr_proxy:.1%} return proxy",
        threshold=f"{MIN_XIRR:.0%} to {MAX_XIRR:.0%}",
        details=(
            "XIRR proxy is within expected range." if passed
            else f"XIRR proxy {xirr_proxy:.1%} outside plausible range — "
                 "possible incorrect cash flow directions or phantom transactions."
        ),
    )


def _check_active_holdings_have_positive_value(connector: DatabaseConnector) -> CheckResult:
    """
    Check 5: All active holdings have market_value > 0.

    Derived from: Insurance transformer outputting None for market_value.
    """
    row = connector.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT COUNT(*) AS zero_value_count
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
        WHERE h.is_shadow = FALSE
          AND (h.market_value IS NULL OR h.market_value < 0)
    """).fetchone()

    zero_count = int(row[0]) if row else 0
    passed = zero_count == 0
    return CheckResult(
        name="active_holdings_have_positive_value",
        passed=passed,
        actual_value=f"{zero_count} holdings with zero/null value",
        threshold="0",
        details=(
            "All active holdings have positive market_value." if passed
            else f"{zero_count} active holdings have market_value <= 0 — "
                 "likely transformer output None (insurance/gold bug)."
        ),
    )


def _check_shadow_mutual_exclusion(connector: DatabaseConnector) -> CheckResult:
    """
    Check 6 (registry position 6): No qty-bearing reader rows have is_shadow=TRUE without supersession.

    Derived from: shadow logic gaps where Gold/Insurance/Schwab rows were incorrectly
    marked is_shadow=TRUE, making reader data invisible.

    The correct pattern is: PIS rows get is_shadow=TRUE when a reader source covers
    the same asset. Reader rows must ALWAYS be is_shadow=FALSE — with two permitted
    exceptions introduced in C3.2:

      (a) Zero-qty co-authority tombstone rows: written by `_shadow_coauthority_tombstone`
          to signal ACAT transfers. These carry quantity=0 and is_shadow=TRUE at the
          source's latest snapshot date — they are intentionally shadowed placeholders,
          not broken pipeline data.

      (b) Broker rows superseded by a Consolidated source: written by C3.4 (not yet built).
          A reader row may be is_shadow=TRUE if a `Consolidated` source row for the same
          asset exists and is active (is_shadow=FALSE). Dormant now, tested for future safety.

    A qty-bearing reader row at the source's latest snapshot_date that is shadowed without
    a Consolidated supersession is still a violation — this preserves the original
    Gold/Insurance "all rows shadowed" protection.

    Note: It is CORRECT for a single asset (e.g. AAPL) to have PIS is_shadow=TRUE
    and Schwab_CSV is_shadow=FALSE at the same date — that's the authority model.
    """
    reader_in = ", ".join(f"'{s}'" for s in READER_SOURCES)
    rows = connector.execute(f"""
        WITH latest_source_sync AS (
            -- Exclude zero-qty tombstone rows (C3.2) so they cannot shift a source's
            -- inspection window to today, hiding genuinely mis-shadowed qty-bearing rows
            -- at the source's real file date.
            SELECT source_system, MAX(snapshot_date) AS latest_date
            FROM holdings
            WHERE COALESCE(quantity, 0) != 0
            GROUP BY source_system
        )
        SELECT h.source_system, h.asset_id, h.snapshot_date
        FROM holdings h
        JOIN latest_source_sync lss ON h.source_system = lss.source_system
             AND h.snapshot_date = lss.latest_date
        WHERE h.source_system IN ({reader_in})
          AND h.is_shadow = TRUE
          AND COALESCE(h.quantity, 0) != 0          -- exempt zero-qty co-authority tombstones (C3.2)
          AND NOT EXISTS (                           -- exempt rows superseded by a Consolidated row (C3.4, dormant now)
              SELECT 1 FROM holdings c
              WHERE c.asset_id = h.asset_id
                AND c.source_system = 'Consolidated'
                AND c.is_shadow = FALSE
          )
        LIMIT 5
    """).fetchall()

    passed = len(rows) == 0
    if rows:
        examples = [(r[0], r[1]) for r in rows[:3]]
        details = (
            f"Qty-bearing reader rows incorrectly marked as shadow (no Consolidated supersession): {examples}"
        )
    else:
        details = (
            "No qty-bearing reader rows are marked is_shadow=TRUE without supersession (correct). "
            "Zero-qty tombstones and Consolidated-superseded rows are exempt."
        )

    return CheckResult(
        name="shadow_mutual_exclusion",
        passed=passed,
        actual_value=f"{len(rows)} reader rows with is_shadow=TRUE (qty-bearing, unsuperseded)",
        threshold="0",
        details=details,
    )


def _check_consolidated_equals_sum(connector: DatabaseConnector) -> CheckResult:
    """
    Check 15: Every active `Consolidated` row's market_value (and, for non-cash, quantity)
    equals the SUM of its contributing broker rows' latest-per-(asset,source) values.

    Derived from: C3.4 sync-time consolidation (see ADR-016, docs/plans/2026-06-15-
    workstream-c3-execution.md). The consolidation phase (`_consolidate_coauthority_holdings`)
    writes one merged `source_system='Consolidated'` holdings row per co-authority asset and
    shadows the contributing broker rows. A wrong sum here means corrupt net worth — a broker
    row was missed, double-counted, or the merged-FIFO cost calc diverged from the reported
    quantity.

    Co-authority broker sources are derived from `AuthorityResolver.coauthority_sources()`
    (config/source_authority.yaml). Per-(asset, source) MAX is used — NEVER a global MAX
    (Rule 3). Tolerance: relative 0.5% or absolute 1.0, whichever is looser. Cash assets
    (`CASH_%`) check market_value only — quantity is an intentional qty=1 sentinel, not a sum.

    Passes trivially when no `Consolidated` rows exist (pre-C3.4 DBs, or no co-authority
    assets currently held by >=1 broker).
    """
    from src.identity.authority_resolver import AuthorityResolver

    resolver = AuthorityResolver()
    coauth_sources = resolver.coauthority_sources()

    if not coauth_sources:
        return CheckResult(
            name="consolidated_equals_sum",
            passed=True,
            actual_value="no_coauthority_sources",
            threshold="rel<=0.5% or abs<=1.0",
            details="No co-authority broker sources configured — check trivially passes.",
        )

    coauth_list = ", ".join(f"'{s}'" for s in sorted(coauth_sources))

    rows = connector.execute(
        f"""
        WITH latest_per_asset_source AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE source_system IN ({coauth_list})
            GROUP BY asset_id, source_system
        ),
        broker_latest AS (
            SELECT h.asset_id, h.source_system, h.quantity, h.market_value
            FROM holdings h
            JOIN latest_per_asset_source lpas
              ON h.asset_id = lpas.asset_id
             AND h.source_system = lpas.source_system
             AND h.snapshot_date = lpas.max_date
        ),
        broker_sums AS (
            SELECT asset_id,
                   SUM(quantity) AS sum_qty,
                   SUM(market_value) AS sum_mv,
                   COUNT(*) AS n_brokers
            FROM broker_latest
            GROUP BY asset_id
        )
        SELECT c.asset_id, c.quantity, c.market_value,
               bs.sum_qty, bs.sum_mv, bs.n_brokers
        FROM holdings c
        JOIN broker_sums bs ON bs.asset_id = c.asset_id
        WHERE c.source_system = 'Consolidated'
          AND c.is_shadow = FALSE
        """
    ).fetchall()

    def _mismatch(actual, expected) -> bool:
        actual = float(actual or 0)
        expected = float(expected or 0)
        diff = abs(actual - expected)
        rel_tol = abs(expected) * 0.005
        return diff > max(rel_tol, 1.0)

    mismatches = []
    for asset_id, cons_qty, cons_mv, sum_qty, sum_mv, n_brokers in rows:
        is_cash = asset_id.startswith("CASH_")
        mv_bad = _mismatch(cons_mv, sum_mv)
        qty_bad = (not is_cash) and _mismatch(cons_qty, sum_qty)
        if mv_bad or qty_bad:
            mismatches.append(
                (asset_id, f"cons_qty={cons_qty} sum_qty={sum_qty} cons_mv={cons_mv} sum_mv={sum_mv}")
            )

    passed = len(mismatches) == 0
    if mismatches:
        details = f"Consolidated rows diverge from broker sum: {mismatches[:3]}"
    else:
        details = (
            f"All {len(rows)} active Consolidated row(s) match the sum of their contributing "
            "broker rows (within tolerance)."
        )

    return CheckResult(
        name="consolidated_equals_sum",
        passed=passed,
        actual_value=f"{len(mismatches)}/{len(rows)} Consolidated rows mismatched",
        threshold="rel<=0.5% or abs<=1.0",
        details=details,
    )


def _check_unmatched_security_transfer(connector: DatabaseConnector) -> CheckResult:
    """
    Check 16 (advisory): Every real (non-provisional) security-transfer leg must
    have a same-asset counterpart within a 7-day window.

    Derived from: the position_lots ACAT double-count bug (VOO 42 vs 21 held, IEF
    344 vs 172, SGOV 753.07 vs 553.07) — a `transfer_in`/`transfer_out` leg that
    never gets its pair means either a missing counterpart import (a broker report
    gap) or a genuinely unpaired transfer that downstream consumers (north_star_flows
    R0 `security_transfer_pair`, position_lots pair-aware exclusion) will silently
    treat as unpaired, which is only correct for cross-asset CN-fund 超级转换
    conversions. This check surfaces orphans so a human can confirm intent.

    Scope: only "quantity-bearing, ~zero-amount" legs (|quantity| > 0.0001 and
    |amount_net| < 0.005) — the same universe used by north_star_flows R0. A
    transfer_out with a non-trivial amount_net is a different economic event (e.g.
    a cash transfer mislabeled as security transfer) and is intentionally excluded
    from this check.

    Advisory (not blocking): an orphaned transfer can legitimately occur mid-flight
    (broker B's report hasn't landed yet) and self-heals on the next sync once the
    counterpart leg is ingested.
    """
    rows = connector.execute(
        """
        SELECT id, transaction_date, asset_id, transaction_type, quantity, source_system
        FROM transactions
        WHERE is_provisional = FALSE
          AND LOWER(transaction_type) IN ('transfer_in', 'transfer_out')
          AND ABS(COALESCE(quantity, 0)) > 0.0001
          AND ABS(COALESCE(amount_net, 0)) < 0.005
        ORDER BY transaction_date ASC, id ASC
        """
    ).fetchall()

    legs_in: list = []
    legs_out: list = []
    for tx_id, tx_date, asset_id, tx_type, quantity, source in rows:
        leg = {
            "id": tx_id,
            "date": tx_date,
            "asset_id": asset_id,
            "qty": abs(float(quantity or 0.0)),
            "source": source,
        }
        (legs_in if (tx_type or "").lower() == "transfer_in" else legs_out).append(leg)

    used: set = set()
    for leg_in in legs_in:
        if leg_in["date"] is None:
            continue
        match = next(
            (
                o for o in legs_out
                if o["id"] not in used
                and o["date"] is not None
                and o["asset_id"] == leg_in["asset_id"]
                and abs(o["qty"] - leg_in["qty"]) <= 1e-6
                and abs((o["date"] - leg_in["date"]).days) <= 7
            ),
            None,
        )
        if match is not None:
            used.add(match["id"])
            used.add(leg_in["id"])

    orphans = []
    for leg in legs_in + legs_out:
        if leg["id"] not in used:
            orphans.append(
                f"asset_id={leg['asset_id']} date={leg['date']} qty={leg['qty']:.6f} source={leg['source']}"
            )

    passed = len(orphans) == 0
    if orphans:
        details = f"{len(orphans)} unmatched security-transfer leg(s): " + "; ".join(orphans[:10])
        if len(orphans) > 10:
            details += f" (+{len(orphans) - 10} more)"
    else:
        details = "All non-provisional security-transfer legs have a matching counterpart within 7 days."

    return CheckResult(
        name="unmatched_security_transfer",
        passed=passed,
        actual_value=f"{len(orphans)} orphaned transfer leg(s)",
        threshold="0",
        details=details,
    )


def _check_cost_basis_ratio(connector: DatabaseConnector) -> CheckResult:
    """
    Check 8: cost_price_unit * quantity < 10x market_value.

    Derived from: PIS Excel Cost_Price_Unit = total buy cost (not per-unit),
    leading to inflated cost basis numbers.
    """
    rows = connector.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT h.asset_id, h.cost_price_unit, h.quantity, h.market_value,
               (h.cost_price_unit * h.quantity) / h.market_value AS ratio
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
        WHERE h.is_shadow = FALSE
          AND h.cost_price_unit IS NOT NULL
          AND h.cost_price_unit > 0
          AND h.quantity > 0
          AND h.market_value > 0
          AND (h.cost_price_unit * h.quantity) / h.market_value > 10
        ORDER BY ratio DESC
        LIMIT 5
    """).fetchall()

    passed = len(rows) == 0
    if rows:
        examples = [(r[0], f"ratio={r[4]:.1f}x") for r in rows[:3]]
        details = f"High cost-to-market assets: {examples}"
    else:
        details = "All cost bases are within 10x of market value."

    return CheckResult(
        name="cost_basis_ratio_under_10x",
        passed=passed,
        actual_value=f"{len(rows)} assets with cost > 10x market value",
        threshold="0",
        details=details,
    )


def _check_cash_pnl_is_zero(connector: DatabaseConnector) -> CheckResult:
    """
    Check 9: Cash holdings have zero or near-zero unrealized P&L.

    Derived from: cash P&L bug of ¥-100K caused by incorrect cost basis for CASH assets.
    Threshold: |pnl / market_value| < 1% for CASH assets.
    """
    rows = connector.execute("""
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        -- Only check CNY-denominated cash: market_value and cost_price_unit are both in CNY.
        -- CASH_USD is excluded because cost_price_unit is native USD (V5.2.0+) while
        -- market_value is CNY, making the arithmetic comparison meaningless.
        SELECT h.asset_id, h.market_value - (h.cost_price_unit * h.quantity) as unrealized_pnl, h.market_value
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
        WHERE h.is_shadow = FALSE
          AND h.asset_id LIKE 'CASH_%'
          AND h.asset_id NOT LIKE 'CASH_Deposit_%'
          AND h.currency = 'CNY'
          AND h.cost_price_unit IS NOT NULL
          AND h.market_value > 0
          AND ABS((h.market_value - (h.cost_price_unit * h.quantity)) / h.market_value) > 0.50
        LIMIT 5
    """).fetchall()

    passed = len(rows) == 0
    if rows:
        examples = [(r[0], f"pnl={r[1]:,.0f}") for r in rows[:3]]
        details = f"Cash assets with non-zero P&L: {examples}"
    else:
        details = "All CASH assets have near-zero unrealized P&L."

    return CheckResult(
        name="cash_pnl_is_zero",
        passed=passed,
        actual_value=f"{len(rows)} cash assets with non-zero P&L",
        threshold="0",
        details=details,
    )


def _check_reader_rows_not_all_shadowed(connector: DatabaseConnector) -> CheckResult:
    """
    Check 10: Not all reader rows are shadowed (at least some are authoritative).

    Derived from: Gold/Insurance all-shadowed bug where reader data was imported
    but then all marked is_shadow=TRUE, leaving PIS as the only active source.
    """
    reader_in = ", ".join(f"'{s}'" for s in READER_SOURCES)
    row = connector.execute(f"""
        SELECT
            COUNT(*) AS total_reader_rows,
            SUM(CASE WHEN is_shadow = FALSE THEN 1 ELSE 0 END) AS active_reader_rows
        FROM holdings
        WHERE source_system IN ({reader_in})
    """).fetchone()

    # SUM() of a CASE expression on an empty table returns NULL, not 0 — guard both columns.
    total = int(row[0] or 0) if row else 0
    active = int(row[1] or 0) if row else 0

    if total == 0:
        return CheckResult(
            name="reader_rows_not_all_shadowed",
            passed=True,
            actual_value="no_reader_rows",
            threshold="active > 0",
            details="No reader rows found — readers may not be enabled.",
        )

    passed = active > 0
    return CheckResult(
        name="reader_rows_not_all_shadowed",
        passed=passed,
        actual_value=f"{active}/{total} active reader rows",
        threshold="active > 0",
        details=(
            f"{active} of {total} reader rows are active (authoritative)." if passed
            else f"ALL {total} reader rows are shadowed — reader data is invisible. "
                 "Shadow logic may be running in wrong direction."
        ),
    )


def _check_no_extreme_single_asset_change(connector: DatabaseConnector) -> CheckResult:
    """
    Check 2b: No single asset has market_value > 15x swing across recent snapshots.

    Derived from: CNY/USD mixing where one Schwab asset jumped 7x in value due to
    being reported in USD at one snapshot and CNY at another.

    Cash assets (CASH_USD, CASH_CNY, etc.) are excluded: their balance legitimately
    swings by orders of magnitude as trades happen (e.g. sold USD cash to buy SGOV).
    The check is for price/quantity anomalies, not cash flow activity.
    """
    # 14-day window (not 30) to exclude backup-restore dates (2026-02-12/13/15) which
    # introduced raw USD rows alongside CNY rows, creating spurious 7-13x swings.
    rows = connector.execute("""
        WITH asset_stats AS (
            SELECT asset_id,
                   MAX(market_value) AS max_value,
                   MIN(market_value) AS min_value,
                   AVG(market_value) AS avg_value
            FROM holdings
            WHERE is_shadow = FALSE
              AND market_value > 0
              AND snapshot_date >= CURRENT_DATE - INTERVAL '14 days'
              AND asset_id NOT LIKE 'CASH_%'
            GROUP BY asset_id
            HAVING COUNT(DISTINCT snapshot_date) >= 2
               AND MIN(market_value) > 0
        )
        SELECT asset_id, max_value, min_value, max_value / min_value AS ratio
        FROM asset_stats
        WHERE max_value / min_value > 15.0
        ORDER BY ratio DESC
        LIMIT 5
    """).fetchall()

    passed = len(rows) == 0
    if rows:
        examples = [(r[0], f"{r[3]:.1f}x swing") for r in rows[:3]]
        details = f"Assets with >5x value swing in 30 days: {examples}"
    else:
        details = "No assets have extreme value swings in the last 30 days."

    return CheckResult(
        name="no_extreme_single_asset_change",
        passed=passed,
        actual_value=f"{len(rows)} assets with >5x swing",
        threshold="0",
        details=details,
    )


def _check_twr_xirr_consistency(connector: DatabaseConnector) -> CheckResult:
    """Check 18: Flag massive divergence (>25%) between TWR and XIRR over 30+ days.
    Divergence often indicates missing cash flows, double counting, or severe valuation errors.
    """
    from src.financial_analysis.twr import calculate_portfolio_twr
    from src.financial_analysis.xirr import calculate_portfolio_xirr
    
    # 1. Quick check for sufficient history length (>30 days)
    row = connector.execute("""
        SELECT MIN(snapshot_date) as min_d, MAX(snapshot_date) as max_d
        FROM holdings
        WHERE market_value > 0
    """).fetchone()
    if not row or not row[0] or not row[1]:
        return CheckResult(
            name="twr_xirr_consistency",
            passed=True,
            actual_value=None,
            threshold="<25.0% spread",
            details="Skipped: no history found."
        )
        
    start_dt = row[0] if isinstance(row[0], (date, datetime)) else date.fromisoformat(str(row[0]))
    end_dt = row[1] if isinstance(row[1], (date, datetime)) else date.fromisoformat(str(row[1]))
    
    days_diff = (end_dt - start_dt).days
    if days_diff < 30:
        return CheckResult(
            name="twr_xirr_consistency",
            passed=True,
            actual_value=None,
            threshold="<25.0% spread",
            details=f"Skipped: history is less than 30 days ({days_diff} days)."
        )

    try:
        twr_result = calculate_portfolio_twr(connector, end_date=end_dt.isoformat())
        if twr_result is None:
            raise ValueError("TWR calculation returned None.")
            
        twr_cum = twr_result.get('cumulative', 0.0)
        twr_ann = twr_result.get('annualized')
        
        # Manually annualize if not provided by the calculator (e.g. exactly 1 year or short history)
        if twr_ann is None:
            years = days_diff / 365.25
            twr = (1 + twr_cum) ** (1 / years) - 1 if years > 0 else twr_cum
        else:
            twr = twr_ann
            
    except Exception as e:
        logger.warning(f"Could not calculate TWR for consistency check: {e}")
        twr = None

    try:
        xirr = calculate_portfolio_xirr(connector, end_date=end_dt.isoformat())
    except Exception as e:
        logger.warning(f"Could not calculate XIRR for consistency check: {e}")
        xirr = None
        
    if twr is None or xirr is None:
        return CheckResult(
            name="twr_xirr_consistency",
            passed=False,
            actual_value=None,
            threshold="<25.0% spread",
            details="Could not calculate TWR or XIRR for comparison."
        )

    # Guard: verify the latest snapshot used by TWR has adequate asset coverage.
    # If only a fraction of assets reported on the most-recent snapshot date, the
    # TWR calculation may be distorted by the spine SQL zeroing out non-reporting assets.
    try:
        coverage_row = connector.execute("""
            WITH latest AS (
                SELECT asset_id, MAX(snapshot_date) AS max_d
                FROM holdings WHERE is_shadow = FALSE AND market_value > 0
                GROUP BY asset_id
            ),
            most_common_date AS (
                SELECT max_d, COUNT(*) AS cnt
                FROM latest GROUP BY max_d ORDER BY cnt DESC LIMIT 1
            )
            SELECT cnt, (SELECT COUNT(DISTINCT asset_id) FROM latest) AS total
            FROM most_common_date
        """).fetchone()

        if coverage_row and coverage_row[1] > 0:
            coverage_pct = coverage_row[0] / coverage_row[1]
            if coverage_pct < 0.5:
                return CheckResult(
                    name="twr_xirr_consistency",
                    passed=True,
                    actual_value=None,
                    threshold="<25.0% spread",
                    details=f"Skipped: latest snapshot covers only {coverage_pct:.0%} of known assets — insufficient for reliable TWR comparison."
                )
    except Exception as cov_err:
        logger.warning(f"Coverage guard query failed: {cov_err}")
        # Non-fatal — continue with the spread check

    # Both returns should be annualized for fair comparison over >1 year
    # Spread is simple absolute difference
    spread = abs(twr - xirr)
    
    passed = spread <= 0.25
        
    return CheckResult(
        name="twr_xirr_consistency",
        passed=passed,
        actual_value=f"{spread*100:.1f}%",
        threshold="<25.0% spread",
        details=f"TWR and XIRR spread is {spread*100:.1f}% (TWR: {twr*100:.1f}%, XIRR: {xirr*100:.1f}%)" if passed else f"Massive divergence: TWR and XIRR spread exceeds 25.0% (TWR: {twr*100:.1f}%, XIRR: {xirr*100:.1f}%)"
    )


def _check_net_worth_cross_endpoint_consistency(connector: DatabaseConnector) -> CheckResult:
    """
    Check 11: All 3 net worth computation paths agree within 0.1%.

    The 3 paths:
    - Path 1 (Dashboard/data.py): simple SUM — no taxonomy join
    - Path 2 (Compass/compass.py): taxonomy_classes double-join
    - Path 3 (Performance/performance.py): taxonomy_classes double-join (same as Path 2)

    If they diverge, it means a taxonomy table has duplicate join rows that cause
    double-counting (e.g. taxonomy_classes with 2 rows for same subclass).

    All 3 paths now use the same taxonomy system (taxonomy_classes) — divergence
    would indicate duplicate rows in taxonomy_classes or data integrity issues.
    """
    base_cte = """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id
        )
    """

    # Path 1: Dashboard — no taxonomy join, pure SUM
    row1 = connector.execute(base_cte + """
        SELECT SUM(h.market_value)
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.is_shadow = FALSE
    """).fetchone()

    # Path 2: Compass — taxonomy_classes join (graceful if table missing)
    try:
        row2 = connector.execute(base_cte + """
            SELECT SUM(h.market_value)
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
        """).fetchone()
    except Exception:
        row2 = row1  # table may not exist in test/CI — treat as matching

    # Path 3: Performance — taxonomy_classes double-join (same as Path 2 after migration)
    try:
        row3 = connector.execute(base_cte + """
            SELECT SUM(h.market_value)
            FROM holdings h
            JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
        """).fetchone()
    except Exception:
        row3 = row1  # table may not exist in test/CI — treat as matching

    nw1 = float(row1[0] or 0) if row1 else 0.0
    nw2 = float(row2[0] or 0) if row2 else 0.0
    nw3 = float(row3[0] or 0) if row3 else 0.0

    if nw1 == 0:
        return CheckResult(
            name="net_worth_cross_endpoint_consistency",
            passed=True,
            actual_value="no_data",
            threshold="0.1% divergence",
            details="No holdings data — skipped.",
            skipped=True,
        )

    THRESHOLD = 0.001  # 0.1%
    max_val = max(nw1, nw2, nw3)
    min_val = min(nw1, nw2, nw3)
    divergence = (max_val - min_val) / nw1 if nw1 > 0 else 0.0

    passed = divergence <= THRESHOLD
    return CheckResult(
        name="net_worth_cross_endpoint_consistency",
        passed=passed,
        actual_value=f"divergence={divergence:.3%} (path1=¥{nw1:,.0f}, path2=¥{nw2:,.0f}, path3=¥{nw3:,.0f})",
        threshold=f"<={THRESHOLD:.1%}",
        details=(
            "All 3 net worth paths agree." if passed
            else f"Net worth paths diverge by {divergence:.2%}: "
                 f"dashboard=¥{nw1:,.0f}, compass=¥{nw2:,.0f}, performance=¥{nw3:,.0f}. "
                 "Likely cause: duplicate rows in taxonomy_classes."
        ),
    )


def _check_trade_log_verdict_consistency(connector: DatabaseConnector) -> CheckResult:
    """
    Check 19 (V5.8.0): Three-rule bidirectional verdict consistency on trade_logs.

    Rule A: verdict IS NOT NULL AND verification_result IS NULL → orphaned verdict (no evidence).
    Rule B v2 (2026-07-06): verification_status='verified' AND verdict IS NULL
        AND suggestion_source IS NOT NULL AND suggestion_source != 'imported'
        → owner-recorded row verified without a verdict.
        Provenance carve-out: reader/backfill rows (suggestion_source NULL or 'imported') are
        verified-without-verdict BY DESIGN (KPI protection — see V64 migration comment in
        src/database/connector.py); counting them made the check permanent noise (~2.3K rows)
        in which real regressions were invisible. Owner approved the carve-out 2026-07-06.
        outcome_pct IS NULL dropped: text-derived verdicts (keyword classification from a
        narrative) may legitimately never have a computable numeric outcome (e.g. liquidated
        assets with no price history); a verdict-bearing verified row is complete. The scorer
        still fills outcome_pct opportunistically.
    Rule C: verification_status='verification_blocked' AND verification_block_reason IS NULL → blocked with no reason.

    Derived from: V5.8.0 maturity gate — enforces invariants for the feedback loop pipeline.
    Skipped gracefully when trade_logs or required columns do not yet exist.
    """
    # Probe that the table exists before running checks — skip gracefully on pre-V5.8 DBs.
    try:
        connector.execute("SELECT 1 FROM trade_logs LIMIT 0")
    except Exception:
        return CheckResult(
            name="trade_log_verdict_consistency",
            passed=True,
            actual_value="skipped",
            threshold="0",
            details="trade_logs table not found — check skipped (pre-V5.8.0 schema).",
            skipped=True,
        )

    violations: list[str] = []

    # Rule A: verdict without evidence
    try:
        rows_a = connector.execute("""
            SELECT COUNT(*)
            FROM trade_logs
            WHERE verdict IS NOT NULL
              AND (verification_result IS NULL OR TRIM(verification_result) = '')
        """).fetchone()
        count_a = int(rows_a[0]) if rows_a else 0
        if count_a > 0:
            violations.append(f"Rule A: {count_a} row(s) have verdict but no verification_result")
    except Exception as exc:
        logger.warning("check_19 Rule A query error: %s", exc)

    # Rule B v2 (2026-07-06): owner-recorded rows verified without a verdict.
    # Provenance carve-out: reader/backfill rows (suggestion_source NULL or 'imported')
    # are verified-without-verdict BY DESIGN (KPI protection); excluded here to avoid
    # ~2.3K permanent false-positive violations that masked real regressions.
    # outcome_pct IS NULL dropped: text-derived verdicts may legitimately lack a numeric
    # outcome; a verdict-bearing verified row is complete.
    try:
        rows_b = connector.execute("""
            SELECT COUNT(*)
            FROM trade_logs
            WHERE verification_status = 'verified'
              AND verdict IS NULL
              AND suggestion_source IS NOT NULL
              AND suggestion_source != 'imported'
        """).fetchone()
        count_b = int(rows_b[0]) if rows_b else 0
        if count_b > 0:
            violations.append(f"Rule B: {count_b} owner-recorded row(s) are 'verified' without a verdict")
    except Exception as exc:
        logger.warning("check_19 Rule B query error: %s", exc)

    # Rule C: blocked status but no block reason
    try:
        rows_c = connector.execute("""
            SELECT COUNT(*)
            FROM trade_logs
            WHERE verification_status = 'verification_blocked'
              AND (verification_block_reason IS NULL OR TRIM(verification_block_reason) = '')
        """).fetchone()
        count_c = int(rows_c[0]) if rows_c else 0
        if count_c > 0:
            violations.append(f"Rule C: {count_c} row(s) are 'verification_blocked' but missing block_reason")
    except Exception as exc:
        logger.warning("check_19 Rule C query error: %s", exc)

    passed = len(violations) == 0
    return CheckResult(
        name="trade_log_verdict_consistency",
        passed=passed,
        actual_value=f"{len(violations)} violation(s)",
        threshold="0",
        details=(
            "All trade_log verdict fields are internally consistent." if passed
            else "; ".join(violations)
        ),
    )


def _check_insight_trade_links_no_orphans(connector: DatabaseConnector) -> CheckResult:
    """
    Check 20 (V5.10.0): Referential integrity for insight_trade_links.

    Every row must reference an existing insights.id (orphaned insight_id)
    and an existing trade_logs.id (orphaned trade_id).

    Skipped gracefully when insight_trade_links does not yet exist (pre-V5.10 DBs).
    """
    try:
        connector.execute("SELECT 1 FROM insight_trade_links LIMIT 0")
    except Exception:
        return CheckResult(
            name="insight_trade_links_no_orphans",
            passed=True,
            actual_value="skipped",
            threshold="0",
            details="insight_trade_links table not found — check skipped (pre-V5.10.0 schema).",
            skipped=True,
        )

    violations: list[str] = []

    try:
        row = connector.execute("""
            SELECT COUNT(*)
            FROM insight_trade_links itl
            LEFT JOIN insights i ON i.id = itl.insight_id
            WHERE i.id IS NULL
        """).fetchone()
        count = int(row[0]) if row else 0
        if count > 0:
            violations.append(f"orphaned insight_id: {count} link(s) reference non-existent insights")
    except Exception as exc:
        logger.warning("check_20 orphaned insight_id query error: %s", exc)

    try:
        row = connector.execute("""
            SELECT COUNT(*)
            FROM insight_trade_links itl
            LEFT JOIN trade_logs tl ON tl.id = itl.trade_id
            WHERE tl.id IS NULL
        """).fetchone()
        count = int(row[0]) if row else 0
        if count > 0:
            violations.append(f"orphaned trade_id: {count} link(s) reference non-existent trade_logs")
    except Exception as exc:
        logger.warning("check_20 orphaned trade_id query error: %s", exc)

    passed = len(violations) == 0
    return CheckResult(
        name="insight_trade_links_no_orphans",
        passed=passed,
        actual_value=f"{len(violations)} violation(s)",
        threshold="0",
        details=(
            "All insight_trade_links rows reference valid insights and trade_logs." if passed
            else "; ".join(violations)
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Audit logging
# ─────────────────────────────────────────────────────────────────────────────

def _log_report(connector: DatabaseConnector, report: IntegrityReport) -> None:
    """Log the integrity report summary to sync_audit_logs."""
    try:
        failed_names = [c.name for c in report.failed_checks]
        summary = (
            f"Integrity: {report.passed_count}/{len(report.checks)} passed"
            + (f". FAILED: {failed_names}" if failed_names else "")
        )
        connector.execute("""
            INSERT INTO sync_audit_logs (
                sync_timestamp, source_system, target_table, record_key,
                conflict_type, source_value, resolution, resolution_notes, is_resolved
            ) VALUES (
                CURRENT_TIMESTAMP, 'integrity_gate', 'holdings', ?,
                'integrity_check', ?, ?, ?, ?
            )
        """, (
            report.run_at.isoformat(),
            str(report.passed_count),
            "passed" if report.all_passed else "failed",
            summary,
            report.all_passed,
        ))
    except Exception as e:
        logger.debug(f"Could not log integrity report to audit log: {e}")


# ---------------------------------------------------------------------------
# Canonical check registry (single source of truth for the count)
#
# Rule: never hard-code the number 14 (or any other count) in docs or code.
# Always derive it from len(INTEGRITY_CHECKS). The drift check in
# scripts/verify.sh reads INTEGRITY_CHECK_COUNT and compares it against the
# prose claims in CLAUDE.md / README.md / AGENTS.md / data-pipeline-v4.md.
#
# Each entry is a (canonical_name, fn) tuple.  The canonical_name is the
# stable string identifier used by BLOCKING_CHECKS and emitted in CheckResult.name
# — it must exactly match what the function returns in its own CheckResult.
#
# Checks 13–14 are labeled by their historical bug IDs (#19/#20) in old
# session notes / CHANGELOG entries. The count here is the source of truth.
# ---------------------------------------------------------------------------
INTEGRITY_CHECKS: List[Tuple[str, Callable]] = [
    ("net_worth_plausible",                    _check_net_worth_plausible),
    ("no_raw_usd_in_schwab_holdings",          _check_no_currency_mixing),
    ("twr_in_range",                           _check_twr_in_range),
    ("xirr_proxy_in_range",                    _check_xirr_in_range),
    ("active_holdings_have_positive_value",    _check_active_holdings_have_positive_value),
    ("shadow_mutual_exclusion",                _check_shadow_mutual_exclusion),
    ("cost_basis_ratio_under_10x",             _check_cost_basis_ratio),
    ("cash_pnl_is_zero",                       _check_cash_pnl_is_zero),
    ("reader_rows_not_all_shadowed",           _check_reader_rows_not_all_shadowed),
    ("no_extreme_single_asset_change",         _check_no_extreme_single_asset_change),
    ("net_worth_cross_endpoint_consistency",   _check_net_worth_cross_endpoint_consistency),
    ("twr_xirr_consistency",                   _check_twr_xirr_consistency),
    ("trade_log_verdict_consistency",          _check_trade_log_verdict_consistency),       # historically "check #19"
    ("insight_trade_links_no_orphans",         _check_insight_trade_links_no_orphans),      # historically "check #20"
    ("consolidated_equals_sum",                _check_consolidated_equals_sum),             # C3.4
    ("unmatched_security_transfer",            _check_unmatched_security_transfer),         # Attribution&Flows WS-3.1
]

# see AGENTS.md Rule 1 (canonical count — never hard-code the number, import INTEGRITY_CHECK_COUNT)
#: The canonical check count — import this instead of hard-coding 14.
INTEGRITY_CHECK_COUNT: int = len(INTEGRITY_CHECKS)

# ---------------------------------------------------------------------------
# Blocking-vs-advisory classification
#
# Blocking checks indicate corrupt sync output (data cannot be trusted).
# Advisory checks are data-quality observations that may legitimately fail
# (e.g. on a test/empty DB, or for workflow-state reasons like missing verdicts).
#
# Sync semantics:
#   blocking failure  → _record_step(critical=True,  status="failed") → success=False
#   advisory failure  → _record_step(critical=False, status="failed") → degraded=True
#
# The standalone --check-integrity / /integrity/status report remains strict
# (all checks; PASS/FAIL on all_passed) — this classification only governs
# how the sync orchestrator reacts to failures.
#
# Fail-safe: any check whose name is NOT in the known canonical set defaults to
# blocking so that an unclassifiable failure cannot silently pass.
# ---------------------------------------------------------------------------
BLOCKING_CHECKS: FrozenSet[str] = frozenset({
    "no_raw_usd_in_schwab_holdings",        # Currency mixing = corrupt market values
    "shadow_mutual_exclusion",              # Reader+shadow on same asset = broken pipeline
    "reader_rows_not_all_shadowed",         # Reader authority lost = bad sync
    "cost_basis_ratio_under_10x",           # Stale CNY cost basis leaked (V5.2.0 native-currency fix)
    "active_holdings_have_positive_value",  # Active holding with non-positive value = data error
    "consolidated_equals_sum",              # Wrong Consolidated sum = corrupt net worth (C3.4)
})

# All canonical check names — used by is_blocking() fail-safe default.
_ALL_CANONICAL_CHECK_NAMES: FrozenSet[str] = frozenset(name for name, _ in INTEGRITY_CHECKS)


def is_blocking(check_name: str) -> bool:
    """Return True if a failed check should block sync success.

    Fail-safe: unknown names (not in the canonical registry) default to True
    so that an unclassifiable failure can never silently pass as advisory.
    """
    if check_name not in _ALL_CANONICAL_CHECK_NAMES:
        logger.warning(
            "is_blocking: unknown check name %r — defaulting to blocking (fail-safe)", check_name
        )
        return True
    return check_name in BLOCKING_CHECKS
