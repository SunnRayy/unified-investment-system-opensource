"""Process-based trade verification scoring (PRD 2026-07-07, F1.2/F1.3).

Replaces the emotive verdict engine (decision_scorer.py's good_call / regret /
missed_opportunity / bullet_dodged) for compliance/ratio/liquidity trades, and
moves price-outcome data for value-bucket trades out of a "verdict" column into
a purely informational `outcome_info` — never a judgment word.

Pure function library, no feature-flag branching of its own — callers (the
flag-gated routes in api/routes/decisions.py and api/routes/ai_advisor.py)
decide whether to invoke it based on cfg.process_verification.enabled.

Three process checks (F1.2), each tri-state (True / False / NULL=not checked):
  - process_authorized     — trade falls within an approved memo / standing rule
  - process_params_ok      — quantity/price/laddering conform to protocol
  - process_data_verified  — same-day data re-verification was performed

PASS = all three True. FAIL = at least one False (a single protocol violation
fails the trade regardless of the others). UNSCORED = none False, at least one
still NULL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from src.services.verification_config import (
    VALUE_BUCKET,
    VerificationConfig,
    load_verification_config,
)

PROCESS_PASS = "PASS"
PROCESS_FAIL = "FAIL"
PROCESS_UNSCORED = "UNSCORED"

# Display states for compliance/ratio/liquidity buckets — PRD F1.2: their only
# states are Compliant / Violation ('unreviewed' = not-yet-checked).
DISPLAY_COMPLIANT = "compliant"
DISPLAY_VIOLATION = "violation"
DISPLAY_UNREVIEWED = "unreviewed"

# outcome_info() statuses.
OUTCOME_MATURING = "maturing"
OUTCOME_EVALUATED = "evaluated"
OUTCOME_INSUFFICIENT_DATA = "insufficient_data"  # matured but no price data yet
OUTCOME_NOT_APPLICABLE = "n_a_by_rule"  # non-value buckets — never judged on price

_NON_VALUE_BUCKETS = frozenset({"compliance", "ratio", "liquidity"})
# Heuristic-only signals a trade came from an approved standing rule (used only
# by suggest_process_defaults — never authoritative, always human-overridable).
_MEMO_LIKE_SOURCES = frozenset({"memo", "dca", "auto_dca"})


def _normalize_bucket(rule_bucket: Optional[str]) -> str:
    """Unset/unknown/blank -> 'value' (mirrors rule_buckets.py's convention)."""
    text = (rule_bucket or "").strip().lower()
    return text if text in _NON_VALUE_BUCKETS else VALUE_BUCKET


def _to_date(value: Any) -> Optional[date]:
    """Best-effort coercion of a DuckDB/py value to a date. Never raises."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# F1.2 — process score
# ---------------------------------------------------------------------------


def evaluate_process(row: dict) -> dict:
    """Evaluate the three F1.2 process checks for one trade_logs row (dict with
    process_authorized/process_params_ok/process_data_verified, each True/False/None).

    Returns {status: 'PASS'|'FAIL'|'UNSCORED', failing_checks: [names], checked: bool}.
    FAIL beats UNSCORED: one explicit False is a violation even if the other two
    checks are still blank — a known breach must never hide behind "not reviewed".
    PASS requires all three explicitly True. `checked` is True once PASS/FAIL,
    False while still UNSCORED.
    """
    checks = {
        "authorized": row.get("process_authorized"),
        "params_ok": row.get("process_params_ok"),
        "data_verified": row.get("process_data_verified"),
    }
    failing_checks = [name for name, value in checks.items() if value is False]
    if failing_checks:
        return {"status": PROCESS_FAIL, "failing_checks": failing_checks, "checked": True}
    if all(value is True for value in checks.values()):
        return {"status": PROCESS_PASS, "failing_checks": [], "checked": True}
    return {"status": PROCESS_UNSCORED, "failing_checks": [], "checked": False}


def bucket_display_state(rule_bucket: Optional[str], process: dict) -> dict:
    """Map a rule_bucket + evaluate_process() result to its display state.

    compliance/ratio/liquidity: ONLY 'compliant' (PASS) / 'violation' (FAIL) /
    'unreviewed' (UNSCORED) — PRD F1.2. Value bucket gets the same process
    state (it can be unauthorized/unverified too) plus `outcome_eligible=True`,
    signalling it also carries a separate outcome_info() surface.
    """
    bucket = _normalize_bucket(rule_bucket)
    status = process.get("status")
    if status == PROCESS_PASS:
        state = DISPLAY_COMPLIANT
    elif status == PROCESS_FAIL:
        state = DISPLAY_VIOLATION
    else:
        state = DISPLAY_UNREVIEWED
    return {"rule_bucket": bucket, "state": state, "outcome_eligible": bucket == VALUE_BUCKET}


# ---------------------------------------------------------------------------
# F1.3 — outcome tracking (value bucket only)
# ---------------------------------------------------------------------------


def outcome_info(row: dict, today: date, window_days: int = 180) -> dict:
    """Value-bucket-only price-outcome info. NEVER emits a verdict word.

    row needs rule_bucket, log_date, outcome_pct.
    - Non-value bucket: {status: 'n_a_by_rule', reason: '...'} — compliance/
      ratio/liquidity trades are never judged on price outcome (PRD F1 category-
      error fix for AMZN RSU sells / gold buys).
    - Value, age < window_days: {status: 'maturing', message: '...'}.
    - Value, age >= window_days, outcome_pct present: {status: 'evaluated',
      outcome_pct: <float>} — informational only, no verdict/color judgment.
    - Value, matured but outcome_pct still NULL: {status: 'insufficient_data',
      message: '...'} — Cross-Cutting Req 3: never fabricate a number.
    """
    bucket = _normalize_bucket(row.get("rule_bucket"))
    if bucket != VALUE_BUCKET:
        return {
            "status": OUTCOME_NOT_APPLICABLE,
            "reason": "compliance/ratio/liquidity trades are never judged on price outcome",
        }

    log_date = _to_date(row.get("log_date"))
    if log_date is None:
        return {"status": OUTCOME_MATURING, "message": "trade date unknown — no evaluation"}

    age_days = (today - log_date).days
    if age_days < window_days:
        return {
            "status": OUTCOME_MATURING,
            "message": f"trade is {age_days}d old (< {window_days}d window) — no evaluation",
        }

    outcome_pct = row.get("outcome_pct")
    if outcome_pct is None:
        return {
            "status": OUTCOME_INSUFFICIENT_DATA,
            "message": "trade matured but no price data available yet — no evaluation",
        }
    return {"status": OUTCOME_EVALUATED, "outcome_pct": round(float(outcome_pct), 4)}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def set_process_checks(
    db: Any,
    trade_id: int,
    authorized: Optional[bool] = None,
    params_ok: Optional[bool] = None,
    data_verified: Optional[bool] = None,
    notes: Optional[str] = None,
) -> None:
    """Set the F1.2 process-check booleans for one trade_logs row.

    Partial-update semantics: None means "leave unchanged" (COALESCE), matching
    a toggle-UI that sends only the field just flipped. process_checked_at is
    always stamped to now. Does not touch verification_status/verdict — process
    checks are an independent lifecycle from the legacy verify/reopen flow.
    """
    db.execute(
        """
        UPDATE trade_logs
        SET process_authorized = COALESCE(?, process_authorized),
            process_params_ok = COALESCE(?, process_params_ok),
            process_data_verified = COALESCE(?, process_data_verified),
            process_checked_at = CURRENT_TIMESTAMP,
            process_notes = COALESCE(?, process_notes)
        WHERE id = ?
        """,
        [authorized, params_ok, data_verified, notes, trade_id],
    )


def suggest_process_defaults(row: dict) -> dict:
    """Conservative default heuristics for a not-yet-checked trade row (F1.2).

    authorized: True ONLY with a positive signal (memo_id present, or
    suggestion_source/order_origin indicates memo/DCA) — never defaults to
    False; "not authorized" must be a human, evidence-based call.
    params_ok / data_verified: ALWAYS None — no existing column is a
    trustworthy proxy for laddering conformance or same-day data re-verification;
    both are always human-entered.
    """
    memo_id = row.get("memo_id")
    suggestion_source = str(row.get("suggestion_source") or "").strip().lower()
    order_origin = str(row.get("order_origin") or "").strip().lower()
    authorized: Optional[bool] = None
    if memo_id or suggestion_source in _MEMO_LIKE_SOURCES or order_origin == "auto_dca":
        authorized = True
    return {"authorized": authorized, "params_ok": None, "data_verified": None}


# ---------------------------------------------------------------------------
# Aggregates (GET /decisions/stats, GET /decisions/quarterly-outcome-report)
# ---------------------------------------------------------------------------


def compute_process_aggregates(
    db: Any,
    display_scope_sql: str,
    cfg: Optional[VerificationConfig] = None,
) -> dict:
    """Bucket-aware process aggregates for GET /decisions/stats (F1.2), replacing
    the emotive verdict breakdown once process_verification is enabled.

    display_scope_sql: caller-supplied WHERE predicate — reuse
    decision_scorer.build_trade_display_scope_sql("tl") so this is scoped
    identically to the rest of the Decision Hub.

    Returns {by_bucket: {<bucket>: {compliant, violation, unreviewed, total}},
    overall: {process_pass, process_fail, unreviewed},
    value_outcome_coverage: {evaluated, maturing, insufficient_data, total_value_trades}}.
    """
    if cfg is None:
        cfg = load_verification_config()

    rows = db.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(LOWER(tl.rule_bucket)), ''), 'value') AS bucket,
            tl.process_authorized, tl.process_params_ok, tl.process_data_verified,
            tl.log_date, tl.outcome_pct
        FROM trade_logs tl
        WHERE {display_scope_sql}
        """
    ).fetchall()

    by_bucket: dict[str, dict[str, int]] = {}
    overall = {"process_pass": 0, "process_fail": 0, "unreviewed": 0}
    outcome_counts = {"evaluated": 0, "maturing": 0, "insufficient_data": 0}
    today = date.today()
    window_days = cfg.process_verification.outcome_window_days

    for bucket, authorized, params_ok, data_verified, log_date, outcome_pct in rows:
        process = evaluate_process(
            {
                "process_authorized": authorized,
                "process_params_ok": params_ok,
                "process_data_verified": data_verified,
            }
        )
        display = bucket_display_state(bucket, process)
        stats = by_bucket.setdefault(
            bucket, {"compliant": 0, "violation": 0, "unreviewed": 0, "total": 0}
        )
        stats["total"] += 1
        stats[display["state"]] += 1

        if process["status"] == PROCESS_PASS:
            overall["process_pass"] += 1
        elif process["status"] == PROCESS_FAIL:
            overall["process_fail"] += 1
        else:
            overall["unreviewed"] += 1

        if bucket == VALUE_BUCKET:
            info = outcome_info(
                {"rule_bucket": bucket, "log_date": log_date, "outcome_pct": outcome_pct},
                today,
                window_days,
            )
            if info["status"] == OUTCOME_EVALUATED:
                outcome_counts["evaluated"] += 1
            elif info["status"] == OUTCOME_MATURING:
                outcome_counts["maturing"] += 1
            else:
                outcome_counts["insufficient_data"] += 1

    return {
        "by_bucket": by_bucket,
        "overall": overall,
        "value_outcome_coverage": {
            **outcome_counts,
            "total_value_trades": by_bucket.get(VALUE_BUCKET, {}).get("total", 0),
        },
    }


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    end_month = start_month + 2
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        next_month_first = date(year, end_month + 1, 1)
        end = date.fromordinal(next_month_first.toordinal() - 1)
    return start, end


def compute_quarterly_outcome_report(
    db: Any,
    year: int,
    quarter: int,
    today: Optional[date] = None,
    cfg: Optional[VerificationConfig] = None,
) -> dict:
    """PRD F1.3 quarterly aggregate: value-bucket entry/exit decisions made in
    the given quarter, grouped by memo_id, vs. subsequent (>= window_days) price
    outcome — tests valuation-judgment hit rate. No per-trade verdicts.

    Per memo: {memo_id, trades, evaluated, avg_outcome_pct, hit_rate}. hit_rate
    = share (%) of evaluated trades with outcome_pct > 0, None when evaluated==0
    (Cross-Cutting Req 3 — never fabricate a rate from zero data). Top-level
    `insufficient_data: True` added when no trade in the quarter has evaluated yet.
    """
    if cfg is None:
        cfg = load_verification_config()
    if today is None:
        today = date.today()
    start, end = _quarter_bounds(year, quarter)
    window_days = cfg.process_verification.outcome_window_days

    rows = db.execute(
        """
        SELECT memo_id, log_date, outcome_pct
        FROM trade_logs
        WHERE log_date BETWEEN ? AND ?
          AND COALESCE(NULLIF(TRIM(LOWER(rule_bucket)), ''), 'value') = 'value'
        """,
        [start, end],
    ).fetchall()

    by_memo: dict[str, dict[str, Any]] = {}
    total_trades = 0
    total_evaluated = 0

    for memo_id, log_date, outcome_pct in rows:
        key = memo_id or "unassigned"
        bucket = by_memo.setdefault(
            key, {"memo_id": key, "trades": 0, "evaluated": 0, "_outcomes": [], "_positive": 0}
        )
        bucket["trades"] += 1
        total_trades += 1
        info = outcome_info(
            {"rule_bucket": VALUE_BUCKET, "log_date": log_date, "outcome_pct": outcome_pct},
            today,
            window_days,
        )
        if info["status"] == OUTCOME_EVALUATED:
            bucket["evaluated"] += 1
            total_evaluated += 1
            bucket["_outcomes"].append(info["outcome_pct"])
            if info["outcome_pct"] > 0:
                bucket["_positive"] += 1

    memos = []
    for bucket in by_memo.values():
        outcomes = bucket.pop("_outcomes")
        positive = bucket.pop("_positive")
        evaluated = bucket["evaluated"]
        avg_outcome_pct = round(sum(outcomes) / len(outcomes), 2) if outcomes else None
        hit_rate = round(positive / evaluated * 100, 1) if evaluated > 0 else None
        memos.append(
            {
                "memo_id": bucket["memo_id"],
                "trades": bucket["trades"],
                "evaluated": evaluated,
                "avg_outcome_pct": avg_outcome_pct,
                "hit_rate": hit_rate,
            }
        )
    memos.sort(key=lambda m: str(m["memo_id"]))

    result: dict[str, Any] = {
        "year": year,
        "quarter": quarter,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_trades": total_trades,
        "total_evaluated": total_evaluated,
        "memos": memos,
    }
    if total_evaluated == 0:
        result["insufficient_data"] = True
    return result
