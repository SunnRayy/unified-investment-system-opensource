"""Compute verdict and score aggregates for AI advisor trade decisions."""

from __future__ import annotations

import logging
import re
import weakref
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# V5.8.0 Decision Feedback Loop constants
VERIFICATION_STATUSES = ("pending", "pending_window", "verified", "verification_blocked")
VERDICT_GOOD_CALL = "good_call"
VERDICT_REGRET = "regret"
VERDICT_BULLET_DODGED = "bullet_dodged"
VERDICT_MISSED_OPPORTUNITY = "missed_opportunity"
# Neutral verdict: matured trade with a computable outcome that lands within the ±band
# (neither positive nor negative enough to classify directionally). DCA trades and
# on-plan executions typically fall here. Does NOT count in the hit-rate numerator or
# denominator — it is reported separately in the verdict breakdown.
VERDICT_NEUTRAL = "neutral"

# Per-asset-class verdict threshold bands (minimum |outcome_pct| to classify a verdict).
# Keyword classifier is always authoritative; these bands only govern the numeric fallback
# in derive_verdict_suggestion(). Lower = more sensitive (stable assets); higher = less
# sensitive (volatile assets). _DEFAULT_BAND used when asset_class is None or unmapped.
_DEFAULT_BAND: float = 5.0
VERDICT_THRESHOLDS: dict[str, float] = {
    "Cash": 2.0,        # cash instruments move little; even 2% is signal
    "Bond": 2.0,        # bonds / fixed income — low expected volatility
    "CN Equity": 5.0,   # Chinese equities — default band
    "US Equity": 5.0,   # US equities — default band
    "Equity": 5.0,      # generic equity fallback
    "Alts": 8.0,        # alternatives / paper gold — higher expected volatility
    "Insurance": 3.0,   # insurance policies — slow-moving NAV
    "Property": 3.0,    # real estate — slow-moving
}

REGRET_KEYWORDS = ["卖飞", "踏空", "错过", "早卖", "亏了", "后悔", "可惜", "失误"]
GOOD_CALL_KEYWORDS = [
    "止损成功",
    "规避",
    "正确",
    "成功",
    "符合预期",
    "卖对了",
    "买对了",
    "验证通过",
    "明智",
    "及时",
    "判断准确",
]
MISSED_OPPORTUNITY_KEYWORDS = ["未买入", "观望", "错过买点", "没有行动"]
BULLET_DODGED_KEYWORDS = ["躲过", "避开", "幸亏没买", "躲开了"]
GENERIC_SUGGESTION_SOURCES = {"", "unknown", "other", "system", "strategy_memo", "imported"}
_STRATEGY_MEMOS_HAS_CONTENT_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

# F1 process-verification guard (PRD 2026-07-07, Batch B): buckets that must never
# receive a price-derived verdict once the flag is on.
_PROCESS_NON_VALUE_BUCKETS = ("compliance", "ratio", "liquidity")


def _process_verification_flag_enabled() -> bool:
    """Best-effort read of cfg.process_verification.enabled; defaults to False on
    any error (missing config module/file) so the scorer never breaks in an
    environment where the process-verification feature isn't wired up."""
    try:
        from src.services.verification_config import load_verification_config
        return load_verification_config().process_verification.enabled
    except Exception:
        return False


def build_trade_display_scope_sql(alias: str = "trade_logs") -> str:
    """Return a reusable SQL predicate for trade rows shown in Decision Hub display surfaces."""
    p = f"{alias}." if alias else ""
    return f"""
    (
      {p}suggestion_source IS NOT NULL
      OR ({p}ai_suggestion IS NOT NULL AND TRIM({p}ai_suggestion) != '')
      OR ({p}decision_reason IS NOT NULL AND TRIM({p}decision_reason) != '')
      OR ({p}verification_result IS NOT NULL AND TRIM({p}verification_result) != '')
      OR {p}linked_transaction_id IS NOT NULL
    )
    """


def build_ai_attribution_scope_sql(alias: str = "trade_logs", include_linked_memo: bool = False) -> str:
    """Return a reusable SQL predicate for trades with confirmed memo/insight attribution."""
    p = f"{alias}." if alias else ""
    source_expr = f"LOWER(TRIM(COALESCE({p}suggestion_source, '')))"
    ticker_expr = f"UPPER(regexp_extract(COALESCE({p}asset_id, ''), '([^_\\\\.]+)$', 1))"
    manual_or_generic = "', '".join(
        sorted(
            {
                "manual",
                "human",
                "user",
                *GENERIC_SUGGESTION_SOURCES,
            }
        )
    )

    conditions = [
        f"""
        EXISTS (
          SELECT 1
          FROM strategy_memos sm
          WHERE {source_expr} IN ('memo', 'strategy_memo')
            AND sm.memo_date BETWEEN {p}log_date - INTERVAL 90 DAY AND {p}log_date
            AND {ticker_expr} != ''
            AND (
              UPPER(COALESCE(sm.title, '')) LIKE '%' || {ticker_expr} || '%'
              OR UPPER(COALESCE(sm.key_directives, '')) LIKE '%' || {ticker_expr} || '%'
            )
        )
        """,
        f"""
        EXISTS (
          SELECT 1
          FROM insights i
          WHERE COALESCE(i.category, '') != 'lesson'
            AND i.insight_date BETWEEN {p}log_date - INTERVAL 3 DAY AND {p}log_date + INTERVAL 3 DAY
            AND (
              (
                {source_expr} NOT IN ('{manual_or_generic}')
                AND LOWER(TRIM(COALESCE(i.ai_model, ''))) = {source_expr}
              )
              OR (
                {ticker_expr} != ''
                AND UPPER(COALESCE(i.content, '')) LIKE '%' || {ticker_expr} || '%'
              )
            )
        )
        """,
    ]
    if include_linked_memo:
        conditions.insert(0, f"{p}linked_memo_id IS NOT NULL")

    joined = "\n      OR ".join(cond.strip() for cond in conditions)
    return f"""
    (
      {joined}
    )
    """


def derive_verdict_suggestion(
    action: str,
    outcome_pct: float | None,
    asset_class: str | None = None,
) -> str | None:
    """Suggest a verdict label from action direction and numeric outcome; UI hint only, not authoritative.

    Uses per-asset-class threshold bands from VERDICT_THRESHOLDS; falls back to _DEFAULT_BAND.
    The keyword classifier (classify_verdict_from_text) always takes precedence over this function.
    """
    if outcome_pct is None:
        return None
    band = VERDICT_THRESHOLDS.get(asset_class or "", _DEFAULT_BAND) if asset_class else _DEFAULT_BAND
    if abs(outcome_pct) < band:
        return None
    action_lower = (action or "").lower()
    is_buy = "buy" in action_lower or "买" in action_lower
    is_sell = "sell" in action_lower or "卖" in action_lower
    if is_buy:
        return VERDICT_GOOD_CALL if outcome_pct >= band else VERDICT_REGRET
    if is_sell:
        # For sells: positive outcome_pct means price dropped after sell → dodged a bullet;
        # negative outcome_pct means price rose after sell → missed the upside.
        return VERDICT_BULLET_DODGED if outcome_pct >= band else VERDICT_MISSED_OPPORTUNITY
    return None


def _count_keyword_hits(text: str, keywords) -> int:
    """Count DISTINCT semantic keyword hits in text.

    A keyword that is a strict substring of another MATCHED keyword from the
    same set is not counted separately: '止损成功' must count once, not twice
    via its substring '成功' — otherwise the mixed-narrative count tie-break
    is corrupted by lexical overlap instead of reflecting semantic signals.
    """
    matched = [kw for kw in keywords if kw in text]
    return sum(
        1
        for kw in matched
        if not any(kw != other and kw in other for other in matched)
    )


def classify_verdict_from_text(
    action: str,
    verification_result: str,
    *,
    outcome_pct: "float | None" = None,
    asset_class: "str | None" = None,
) -> "str | None":
    """Classify executed trade outcome from verification text.

    Evaluates BOTH REGRET_KEYWORDS and GOOD_CALL_KEYWORDS for all action types
    (previously GOOD_CALL was unreachable for Sell trades).

    Resolution order:
    1. Exactly one set matches → that verdict.
    2. Both sets match (mixed narrative) → ``derive_verdict_suggestion()`` as
       numeric tie-break when outcome_pct is available and resolves to
       VERDICT_REGRET or VERDICT_GOOD_CALL; otherwise higher match count wins;
       if tied → None (unscored rather than guessing).
    3. Neither matches → check MISSED_OPPORTUNITY / BULLET_DODGED (unchanged).
    """
    if not verification_result:
        return None

    text = verification_result.strip()

    regret_count = _count_keyword_hits(text, REGRET_KEYWORDS)
    good_call_count = _count_keyword_hits(text, GOOD_CALL_KEYWORDS)

    regret_hit = regret_count > 0
    good_call_hit = good_call_count > 0

    if regret_hit and not good_call_hit:
        # For buy actions, MISSED_OPPORTUNITY keywords are more semantically specific
        # than the generic "错过" in REGRET_KEYWORDS ("错过" is a substring of
        # "错过买点"). Prefer missed_opportunity when both co-occur on a buy.
        action_lower = (action or "").lower()
        is_buy = "buy" in action_lower or "买" in action_lower
        if is_buy and any(kw in text for kw in MISSED_OPPORTUNITY_KEYWORDS):
            return VERDICT_MISSED_OPPORTUNITY
        return VERDICT_REGRET

    if good_call_hit and not regret_hit:
        return VERDICT_GOOD_CALL

    if regret_hit and good_call_hit:
        # Mixed narrative: prefer the numeric signal when it resolves to one of
        # these two verdicts (only meaningful for Buy actions; Sell actions get
        # bullet_dodged/missed_opportunity from derive_verdict_suggestion, which
        # do not override here).
        suggested = derive_verdict_suggestion(action, outcome_pct, asset_class)
        if suggested in (VERDICT_REGRET, VERDICT_GOOD_CALL):
            return suggested
        # Numeric unavailable or outside band: use match count.
        if regret_count > good_call_count:
            return VERDICT_REGRET
        if good_call_count > regret_count:
            return VERDICT_GOOD_CALL
        # Tied counts — do not guess.
        return None

    # Neither regret nor good_call matched: check the remaining verdict types.
    for kw in MISSED_OPPORTUNITY_KEYWORDS:
        if kw in text:
            return VERDICT_MISSED_OPPORTUNITY

    for kw in BULLET_DODGED_KEYWORDS:
        if kw in text:
            return VERDICT_BULLET_DODGED

    return None


def compute_outcome_pct_from_text(verification_result: str) -> float | None:
    """Extract percentage value like +3.11% or -8.99% from text."""
    if not verification_result:
        return None
    match = re.search(r"([+-]?\d+\.?\d*)%", verification_result)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _to_date(value: Any) -> date | None:
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


def _extract_market_code(asset_id: str) -> str | None:
    """Extract primary market code from a canonical asset_id.

    Uses the same two-level regex as dsa_sync:
      - 3-part ID  CN_FUND_161725  → '161725'
      - 2-part ID  RSU_AMZN        → 'AMZN'
      - Fallback   last _-segment  (legacy / unexpected patterns)
    Strips any trailing '.'-suffixed exchange qualifier (e.g. 'BABA.HK' → 'BABA').
    """
    if not asset_id:
        return None
    # 3-part canonical: PREFIX_TYPE_CODE
    m3 = re.match(r'^[^_]+_[^_]+_(.+)$', asset_id)
    if m3:
        code = m3.group(1).split(".")[0]
        return code or None
    # 2-part canonical: PREFIX_CODE
    m2 = re.match(r'^[^_]+_(.+)$', asset_id)
    if m2:
        code = m2.group(1).split(".")[0]
        return code or None
    # Single token
    return asset_id.split(".")[0] or None


def _resolve_market_codes(db: Any, asset_id: str) -> list[str]:
    """Return candidate market codes for *asset_id*, most-specific first.

    Primary: code derived by regex from the canonical_id (matches dsa_sync logic).
    Secondary (via asset_registry / asset_source_mappings): any source_id associated
    with the same canonical_id — these are the raw broker codes used in market_daily.

    All lookups are defensive: missing tables/columns/rows return an empty list.
    """
    primary = _extract_market_code(asset_id)
    candidates: list[str] = []
    if primary:
        candidates.append(primary)

    # Fallback: look up alternative codes from asset_source_mappings
    try:
        rows = db.execute(
            """
            SELECT DISTINCT source_id
            FROM asset_source_mappings
            WHERE canonical_id = ?
              AND source_id IS NOT NULL
              AND TRIM(source_id) != ''
            """,
            [asset_id],
        ).fetchall()
        for (src_id,) in rows:
            code = (src_id or "").split(".")[0].strip()
            if code and code not in candidates:
                candidates.append(code)
    except Exception as exc:
        logger.debug("_resolve_market_codes: asset_source_mappings lookup failed for %s: %s", asset_id, exc)

    return candidates


def _lookup_close_price(db: Any, code: str, target_date: date, lookback_days: int = 3) -> float | None:
    try:
        row = db.execute(
            """
            SELECT close
            FROM market_daily
            WHERE code = ?
              AND date <= ?
              AND date >= ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (code, target_date, target_date - timedelta(days=lookback_days)),
        ).fetchone()
    except Exception as exc:
        logger.debug("_lookup_close_price: query failed for code=%s date=%s: %s", code, target_date, exc)
        return None
    if not row or row[0] is None:
        return None
    return float(row[0])


def compute_outcome_to_date(
    db: Any,
    asset_id: str,
    action: str,
    log_date: Any,
) -> "tuple[float, date] | None":
    """Compute interim 'outcome so far' from trade date to the most recent available price.

    baseline = close at or near log_date (7-day lookback, same window as score path).
    latest   = most recent close in [log_date, today]; requires latest_date > log_date.
    pct      = (latest − baseline) / baseline × 100; sign-flipped for sells (same
               convention as compute_outcome_pct_from_prices).

    Returns (pct_rounded_to_4dp, asof_date) or None if any required price is unavailable.
    Never raises.
    """
    start_date = _to_date(log_date)
    if not start_date:
        return None
    today = date.today()
    if start_date >= today:
        return None

    candidates = _resolve_market_codes(db, asset_id)
    if not candidates:
        return None

    for code in candidates:
        baseline = _lookup_close_price(db, code, start_date, lookback_days=7)
        if baseline is None or baseline <= 0:
            continue
        # Fetch most recent close in [start_date, today].
        try:
            row = db.execute(
                """
                SELECT close, date
                FROM market_daily
                WHERE code = ?
                  AND date >= ?
                  AND date <= ?
                ORDER BY date DESC
                LIMIT 1
                """,
                (code, start_date, today),
            ).fetchone()
        except Exception as exc:
            logger.debug("compute_outcome_to_date: query failed for code=%s: %s", code, exc)
            continue
        if not row or row[0] is None:
            continue
        latest_price = float(row[0])
        latest_date = _to_date(row[1])
        # Require a price strictly newer than log_date — no movement yet is not an outcome.
        if latest_date is None or latest_date <= start_date:
            continue
        raw = (latest_price - baseline) / baseline * 100
        action_lower = (action or "").lower()
        if "sell" in action_lower or "卖" in action_lower:
            raw = -raw
        logger.debug(
            "compute_outcome_to_date: asset_id=%s code=%s baseline=%.4f latest=%.4f asof=%s → %.4f%%",
            asset_id, code, baseline, latest_price, latest_date, raw,
        )
        return (round(raw, 4), latest_date)

    logger.debug(
        "compute_outcome_to_date: no price data for asset_id=%s (tried: %s)",
        asset_id, candidates,
    )
    return None


def compute_outcome_pct_from_prices(
    db: Any,
    row_id: int,
    asset_id: str,
    action: str,
    log_date: Any,
) -> float | None:
    """Estimate outcome_pct from market prices around trade date and +30 days.

    Tries candidate market codes in order (primary extracted code first, then any
    alternative codes from asset_source_mappings) and returns the first match found.
    Returns None honestly when no candidate has price data — never raises.
    """
    _ = row_id
    start_date = _to_date(log_date)
    if not start_date:
        return None

    candidates = _resolve_market_codes(db, asset_id)
    if not candidates:
        return None

    end_date = start_date + timedelta(days=30)
    for code in candidates:
        # lookback_days=7 for BOTH lookups — parity with compute_outcome_to_date.
        # A 3-day lookback here would show a to-date preview for 30 days and then
        # block the trade at maturity when the baseline sits 4-7 days before log_date.
        start_px = _lookup_close_price(db, code, start_date, lookback_days=7)
        end_px = _lookup_close_price(db, code, end_date, lookback_days=7)
        if start_px is None or end_px is None or start_px <= 0:
            continue
        # Found a candidate with prices
        raw = (end_px - start_px) / start_px * 100
        action_lower = (action or "").lower()
        if "sell" in action_lower or "卖" in action_lower:
            raw = -raw
        logger.debug(
            "compute_outcome_pct_from_prices: asset_id=%s resolved via code=%s → %.4f%%",
            asset_id, code, raw,
        )
        return round(raw, 4)

    logger.debug(
        "compute_outcome_pct_from_prices: no price data for asset_id=%s (tried: %s)",
        asset_id, candidates,
    )
    return None


def _extract_trade_ticker(asset_id: str) -> str:
    return (_extract_market_code(asset_id) or "").upper()


def _insights_has_title_column(db: Any) -> bool:
    cols = db.execute("PRAGMA table_info('insights')").fetchall()
    # DuckDB returns (cid, name, type, notnull, dflt_value, pk)
    return any(str(c[1]).lower() == "title" for c in cols)


def _table_has_column(db: Any, table_name: str, column_name: str) -> bool:
    cols = db.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(str(c[1]).lower() == column_name.lower() for c in cols)


def _strategy_memos_has_content_col(db: Any) -> bool:
    try:
        cached = _STRATEGY_MEMOS_HAS_CONTENT_CACHE.get(db)
    except TypeError:
        cached = None
    if cached is not None:
        return cached
    cols = db.execute("PRAGMA table_info('strategy_memos')").fetchall()
    has_content = any(str(c[1]).lower() == "content" for c in cols)
    if has_content:
        try:
            _STRATEGY_MEMOS_HAS_CONTENT_CACHE[db] = True
        except TypeError:
            pass
    return has_content


def match_trades_to_insights(db: Any, trade_id: int | None = None) -> int:
    """Backfill trade_logs.suggestion_source by matching nearby adopted insights."""
    display_scope = build_trade_display_scope_sql("tl")
    trade_filter = "AND tl.id = ?" if trade_id is not None else ""
    params: list[Any] = [trade_id] if trade_id is not None else []
    rows = db.execute(
        f"""
        SELECT id, asset_id, log_date
        FROM trade_logs tl
        WHERE (
            tl.suggestion_source IS NULL
            OR LOWER(TRIM(COALESCE(tl.suggestion_source, ''))) IN (
                '', 'unknown', 'other', 'system', 'strategy_memo', 'imported'
            )
        )
          -- Backfill generic-source rows from display scope; this keeps explicit
          -- manual labels intact while allowing imported/system rows to be attributed.
          AND {display_scope}
          {trade_filter}
        """,
        params,
    ).fetchall()

    updated = 0
    has_title = _insights_has_title_column(db)
    for row_id, asset_id, log_date in rows:
        trade_date = _to_date(log_date)
        ticker = _extract_trade_ticker(asset_id or "")
        if not trade_date or not ticker:
            continue

        if has_title:
            candidates = db.execute(
                """
                SELECT ai_model, title, content
                FROM insights
                WHERE adopted = 1
                  AND insight_date BETWEEN ? AND ?
                ORDER BY insight_date DESC
                """,
                (trade_date - timedelta(days=3), trade_date + timedelta(days=3)),
            ).fetchall()
        else:
            candidates = db.execute(
                """
                SELECT ai_model, '' AS title, content
                FROM insights
                WHERE adopted = 1
                  AND insight_date BETWEEN ? AND ?
                ORDER BY insight_date DESC
                """,
                (trade_date - timedelta(days=3), trade_date + timedelta(days=3)),
            ).fetchall()

        matched = False
        for ai_model, title, content in candidates:
            haystack = f"{title or ''} {content or ''}".upper()
            if ticker not in haystack:
                continue
            source = (ai_model or "unknown").strip().lower()
            if source == "strategy_memo":
                source = "memo"
            db.execute("UPDATE trade_logs SET suggestion_source = ? WHERE id = ?", (source, row_id))
            updated += 1
            matched = True
            break

        if not matched:
            # Fallback: check if a strategy memo directed this trade
            has_content = _strategy_memos_has_content_col(db)
            select_content = ", content" if has_content else ", NULL AS content"
            memo_rows = db.execute(
                f"""
                SELECT id, title, key_directives{select_content}
                FROM strategy_memos
                WHERE memo_date BETWEEN ? AND ?
                ORDER BY memo_date DESC
                """,
                (trade_date - timedelta(days=90), trade_date),
            ).fetchall()
            for memo_id, memo_title, key_directives, content in memo_rows:
                haystack = f"{memo_title or ''} {key_directives or ''} {content or ''}".upper()
                if ticker in haystack:
                    db.execute(
                        "UPDATE trade_logs SET suggestion_source = ? WHERE id = ?",
                        ("memo", row_id),
                    )
                    updated += 1
                    break

    return updated


def _log_verdict_audit(
    db: Any,
    trade_id: int,
    action: str,
    outcome_pct: "float | None",
    verification_result: "str | None",
    final_verdict: "str | None",
) -> None:
    """Insert one row into verdict_audit after a scoring decision.

    ``both_matched`` is set when BOTH REGRET_KEYWORDS and GOOD_CALL_KEYWORDS
    match the text, indicating the fallback resolution path was taken.
    ``mismatch`` strictly means the threshold suggestion and the keyword-derived
    verdict disagree; mixed narratives are recorded separately in both_matched
    so monitoring on classifier disagreement is not inflated.
    """
    suggested = derive_verdict_suggestion(action, outcome_pct)
    keyword = classify_verdict_from_text(
        action or "", verification_result or "", outcome_pct=outcome_pct
    )

    # Detect mixed-narrative case regardless of how it resolved.
    text = (verification_result or "").strip()
    both_matched = any(kw in text for kw in REGRET_KEYWORDS) and any(
        kw in text for kw in GOOD_CALL_KEYWORDS
    )

    mismatch = (
        suggested is not None and keyword is not None and suggested != keyword
    )

    has_both_matched_col = _table_has_column(db, "verdict_audit", "both_matched")
    try:
        if has_both_matched_col:
            db.execute(
                """
                INSERT INTO verdict_audit
                    (trade_id, suggested_from_threshold, keyword_derived,
                     final_verdict, mismatch, both_matched)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trade_id, suggested, keyword, final_verdict, mismatch, both_matched),
            )
        else:
            db.execute(
                """
                INSERT INTO verdict_audit
                    (trade_id, suggested_from_threshold, keyword_derived,
                     final_verdict, mismatch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trade_id, suggested, keyword, final_verdict, mismatch),
            )
    except Exception as exc:
        logger.warning("verdict_audit insert failed for trade_id=%s: %s", trade_id, exc)


def score_single_trade(db: Any, trade_id: int) -> int:
    """Populate verdict and outcome_pct for a single trade_logs row with 30-day maturity gate."""
    matched = match_trades_to_insights(db, trade_id=trade_id)
    # Keep insight_trade_links in sync whenever a trade is scored
    try:
        from src.services.decision_links import recompute_auto_links
        recompute_auto_links(db, trade_id=trade_id)
    except Exception as _e:
        logger.debug("recompute_auto_links skipped for trade_id=%s: %s", trade_id, _e)
    display_scope = build_trade_display_scope_sql("tl")

    # F1 process-verification guard: once the flag is on, compliance/ratio/liquidity
    # trades must never get a price verdict (PRD F1 category-error fix). Column
    # existence is re-checked defensively so flag-off callers and any DB/test fixture
    # predating migration 010 are completely unaffected (extra_col stays "").
    flag_on = _process_verification_flag_enabled()
    has_rule_bucket = flag_on and _table_has_column(db, "trade_logs", "rule_bucket")
    extra_col = ", rule_bucket" if has_rule_bucket else ""

    rows = db.execute(
        f"""
        SELECT id, asset_id, action, log_date, verification_result, verdict{extra_col}
        FROM trade_logs tl
        WHERE tl.id = ?
          AND tl.verification_result IS NOT NULL
          AND tl.verification_result != ''
          AND (tl.verdict IS NULL OR tl.outcome_pct IS NULL)
          AND {display_scope}
        """,
        [trade_id],
    ).fetchall()

    scored = 0
    for row in rows:
        if has_rule_bucket:
            row_id, asset_id, action, log_date, verification_result, existing_verdict, rule_bucket = row
        else:
            row_id, asset_id, action, log_date, verification_result, existing_verdict = row
            rule_bucket = None
        if flag_on and (rule_bucket or "value").strip().lower() in _PROCESS_NON_VALUE_BUCKETS:
            # Compliance/ratio/liquidity trade with the flag on: never derive a price
            # verdict for it. outcome_pct/verdict stay whatever they already are.
            continue
        # Maturity gate: only score trades that are at least 30 days old
        trade_date = _to_date(log_date)
        if trade_date is None or (date.today() - trade_date).days < 30:
            logger.debug(
                "score_single_trade: trade_id=%s not yet matured (log_date=%s) — skipping",
                trade_id,
                trade_date,
            )
            continue

        # Try to compute outcome from market prices (primary) or text (fallback)
        outcome_pct = compute_outcome_pct_from_prices(db, row_id, asset_id, action, log_date)

        if outcome_pct is None:
            # Matured but no market price at +30d.
            # Only auto-block when the user has provided ZERO human input — no explicit
            # verdict AND no narrative. If the user wrote notes (narrative present) but
            # chose "Let backend decide", leave the trade in pending_window so they can
            # return and select a verdict manually. Auto-blocking a narrative-submitted
            # trade is the root cause of Issue #12.
            has_narrative = bool((verification_result or "").strip())
            if existing_verdict is None and not has_narrative:
                db.execute(
                    """
                    UPDATE trade_logs
                    SET verification_status = 'verification_blocked',
                        verification_block_reason = ?
                    WHERE id = ?
                    """,
                    ("no market price at log_date+30d", row_id),
                )
            _log_verdict_audit(db, row_id, action or "", None, verification_result, None)
            scored += 1
            continue

        # Matured with prices — derive verdict only when user hasn't already set one.
        # Resolution order: keyword classifier → threshold suggestion → neutral fallback.
        verdict = classify_verdict_from_text(action or "", verification_result, outcome_pct=outcome_pct)
        if verdict is None:
            verdict = derive_verdict_suggestion(action or "", outcome_pct)
        # Neutral fallback: within-band outcome with no keyword/threshold classification.
        # Supersedes the old "no verdict → leave pending_window for manual selection" path for
        # matured rows that have a computable outcome. The loop now always closes at maturity.
        # Never set when existing_verdict is present (overwrite guard is applied below).
        # outcome_pct is not None is guaranteed here (early continue above) — kept explicit
        # for symmetry with score_all_trades and safety against future reordering.
        if verdict is None and existing_verdict is None and outcome_pct is not None:
            verdict = VERDICT_NEUTRAL

        # Only mark verified when we have a verdict — avoids Rule B violation in check #19
        # Never overwrite a verdict the user explicitly set — only fill when still NULL.
        updates: list[str] = ["outcome_pct = ?"]
        params: list[Any] = [outcome_pct]
        if verdict and existing_verdict is None:
            updates.extend(["verdict = ?", "verification_status = 'verified'"])
            params.append(verdict)
        params.append(row_id)
        db.execute(f"UPDATE trade_logs SET {', '.join(updates)} WHERE id = ?", params)
        _log_verdict_audit(db, row_id, action or "", outcome_pct, verification_result, verdict)
        scored += 1

    if matched == 0 and scored == 0:
        logger.debug(
            "score_single_trade: trade_id=%s is manual/no-verification — no-op",
            trade_id,
        )
    logger.info("Decision scorer: scored %s trade_logs rows for trade_id=%s", scored, trade_id)
    return scored


def score_all_trades(db: Any) -> int:
    """Populate verdict and outcome_pct for matured trade_logs rows.

    Narrative-optional: rows without a user verification narrative are eligible for
    auto-scoring via price-based verdict derivation — but ONLY while their verification
    lifecycle is still open (status pending/pending_window/verification_blocked).
    Narrative-less rows at any other status (e.g. thousands of reader-imported ledger
    rows sitting at 'verified' with NULL verdict) are excluded at the SQL level:
    mass-verdicting them would corrupt Review Center KPIs, which count verdicts
    unscoped.  Rows WITH a narrative keep the previous semantics (any status).
    The maturity gate (≥30 days), display-scope filter, and 'never overwrite existing
    verdict' rule are unchanged.

    Per-row decisions:
    - Matured + prices + no existing verdict → auto-verdict + status 'verified'.
      Verdict resolution: keyword classifier → threshold suggestion → VERDICT_NEUTRAL
      (within-band outcome). When narrative is empty, sets verification_result=
      'auto: price-based verdict at +30d'. Every matured row with a computable outcome
      now leaves scope after the first scoring run (neutral closes the loop for DCA trades).
    - Matured + no prices + no narrative + no existing verdict → verification_blocked
      (pending rows only; blocked rows recover when prices arrive via P9 a0, which
      re-fetches both pending AND verification_blocked assets).
    - Matured + no prices + narrative → text-based outcome / keyword verdict (unchanged).
    - Existing verdict present → outcome_pct filled if missing, verdict never overwritten.
    """
    match_trades_to_insights(db)
    display_scope = build_trade_display_scope_sql("tl")

    # F1 process-verification guard (see score_single_trade for full rationale):
    # column existence re-checked defensively so flag-off callers and any DB/test
    # fixture predating migration 010 are completely unaffected (extra_col stays "").
    flag_on = _process_verification_flag_enabled()
    has_rule_bucket = flag_on and _table_has_column(db, "trade_logs", "rule_bucket")
    extra_col = ", rule_bucket" if has_rule_bucket else ""

    rows = db.execute(
        f"""
        SELECT id, asset_id, action, log_date, verification_result, verdict,
               verification_status, outcome_pct{extra_col}
        FROM trade_logs tl
        WHERE (tl.verdict IS NULL OR tl.outcome_pct IS NULL)
          -- 30-day maturity gate (mirrors score_single_trade): a day-0
          -- text-derived verdict would permanently pre-empt the matured
          -- +30d market-price scoring, since scored rows leave this scope.
          -- Matters more now that P9 runs this on every sync.
          AND tl.log_date <= CURRENT_DATE - INTERVAL '30' DAY
          -- KPI protection: narrative-less rows may only be auto-processed while
          -- their verification lifecycle is still open. Reader-imported ledger rows
          -- (status='verified', verdict NULL, no narrative) pass the display scope
          -- but must NEVER be mass-verdicted by the narrative-optional path.
          AND (
              COALESCE(tl.verification_result, '') != ''
              OR tl.verification_status IN ('pending', 'pending_window', 'verification_blocked')
          )
          AND {display_scope}
        """
    ).fetchall()

    scored = 0
    for row in rows:
        if has_rule_bucket:
            (
                row_id, asset_id, action, log_date, verification_result,
                existing_verdict, verification_status, stored_outcome_pct, rule_bucket,
            ) = row
        else:
            (
                row_id, asset_id, action, log_date, verification_result,
                existing_verdict, verification_status, stored_outcome_pct,
            ) = row
            rule_bucket = None
        if flag_on and (rule_bucket or "value").strip().lower() in _PROCESS_NON_VALUE_BUCKETS:
            # Compliance/ratio/liquidity trade with the flag on: never derive a price
            # verdict for it. Leave the row exactly as it is.
            continue
        has_narrative = bool((verification_result or "").strip())

        # Compute numeric outcome — price primary; text fallback only when narrative exists.
        outcome_pct = compute_outcome_pct_from_prices(db, row_id, asset_id, action, log_date)
        if outcome_pct is None and has_narrative:
            outcome_pct = compute_outcome_pct_from_text(verification_result)

        # No price + no narrative + no existing verdict → blocked (mirrors score_single_trade).
        # Guarded to pending rows only: a human-'verified' row must never be demoted, and an
        # already-blocked row must not be re-blocked + re-audited on every sync (it stays in
        # scope because verdict/outcome remain NULL — re-processing would grow verdict_audit
        # unboundedly). Blocked rows re-enter the scoring path below when prices arrive later
        # (P9 a0 price continuity), which is their recovery route.
        if outcome_pct is None and not has_narrative and existing_verdict is None:
            if verification_status in ("pending", "pending_window"):
                db.execute(
                    """
                    UPDATE trade_logs
                    SET verification_status = 'verification_blocked',
                        verification_block_reason = ?
                    WHERE id = ?
                    """,
                    ("no market price at log_date+30d", row_id),
                )
                _log_verdict_audit(db, row_id, action or "", None, verification_result, None)
                scored += 1
            continue

        # Derive verdict: keyword classifier (when narrative) → threshold suggestion → neutral.
        # outcome_pct is guaranteed non-None here (None branch → early continue above).
        verdict = classify_verdict_from_text(action or "", verification_result or "", outcome_pct=outcome_pct)
        if verdict is None:
            verdict = derive_verdict_suggestion(action or "", outcome_pct)
        # Neutral fallback: within-band matured outcome with no keyword/threshold classification.
        # Supersedes the old below-band no-op path: these rows now get a verdict on the first
        # scoring run and leave scope (verdict IS NOT NULL → excluded by the outer WHERE).
        # Never set when existing_verdict is present (overwrite guard applied below).
        # Requires a computed outcome: a narrative row with NO price data must stay
        # pending_window (verified+neutral with outcome NULL would violate check #19 Rule B
        # and assert an outcome we never measured).
        if verdict is None and existing_verdict is None and outcome_pct is not None:
            verdict = VERDICT_NEUTRAL

        updates: list[str] = []
        params: list[Any] = []

        # No-op idempotence: only write outcome_pct when it actually changes.
        # With the neutral fallback, rows that previously stayed in scope forever
        # (below-band, verdict NULL) now get a verdict on the first run and leave scope.
        # This guard still protects rows with an existing verdict and changing price data.
        if outcome_pct is not None and (
            stored_outcome_pct is None
            or abs(float(stored_outcome_pct) - outcome_pct) > 1e-9
        ):
            updates.append("outcome_pct = ?")
            params.append(outcome_pct)

        # Never overwrite a verdict the user explicitly set — only fill when still NULL.
        new_verdict: str | None = None
        if verdict and existing_verdict is None:
            new_verdict = verdict
            updates.append("verdict = ?")
            params.append(verdict)
            # Mark verified when status is pending — or blocked-recovering after prices
            # arrived via P9 a0 price continuity. Rule B: verified ⇒ verdict present.
            if verification_status in ("pending", "pending_window", "verification_blocked"):
                updates.append("verification_status = 'verified'")
                updates.append("verification_date = CURRENT_DATE")
                if verification_status == "verification_blocked":
                    updates.append("verification_block_reason = NULL")
                # Provenance marker: auto-scored rows with no human narrative are labelled so
                # the UI can render an "自动" chip and distinguish them from human-verified rows.
                if not has_narrative:
                    updates.append("verification_result = 'auto: price-based verdict at +30d'")

        if not updates:
            # Nothing changed — do not audit, or a row stuck in scope (e.g. existing verdict,
            # no price for outcome; or below-band with outcome already filled) would insert
            # a duplicate verdict_audit row on every sync.
            continue

        params.append(row_id)
        db.execute(f"UPDATE trade_logs SET {', '.join(updates)} WHERE id = ?", params)
        # Audit AFTER the UPDATE (mirrors score_single_trade ordering — no orphaned
        # audit row if the UPDATE raises). final_verdict is what the row actually
        # carries after this decision: the human verdict when present, else the one
        # just assigned, else None.
        audit_verdict = existing_verdict if existing_verdict is not None else new_verdict
        _log_verdict_audit(db, row_id, action or "", outcome_pct, verification_result, audit_verdict)
        scored += 1

    logger.info("Decision scorer: scored %s trade_logs rows", scored)
    return scored


def compute_insight_adoption_metrics(db: Any) -> dict:
    """Single source of truth for the shared insight adoption KPIs.

    Both GET /decisions/stats (live computation) and
    compute_verification_report() (snapshot persistence) call this function so
    that the Decision Hub and the Review Center always report the same numbers.

    Filter: ``insights WHERE COALESCE(category, '') != 'lesson'``

    Returns a dict with keys:
        total_insights  – int, count of non-lesson insights
        adopted_count   – int, count where adopted = 1
        pending_count   – int, count where adopted IS NULL
        adoption_rate   – float, adopted_count / total_insights * 100 rounded to 1 dp
    """
    row = db.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN adopted IS NULL THEN 1 ELSE 0 END)
        FROM insights
        WHERE COALESCE(category, '') != 'lesson'
        """
    ).fetchone()
    total_insights = int(row[0]) if row else 0
    adopted_count = int(row[1] or 0) if row else 0
    pending_count = int(row[2] or 0) if row else 0
    adoption_rate = round(adopted_count / total_insights * 100, 1) if total_insights > 0 else 0.0
    return {
        "total_insights": total_insights,
        "adopted_count": adopted_count,
        "pending_count": pending_count,
        "adoption_rate": adoption_rate,
    }


def compute_adoption_funnel(db: Any) -> dict[str, int]:
    """Aggregate adoption funnel and verdict totals."""
    insight_stats = db.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END) AS adopted,
            SUM(CASE WHEN adopted = 0 THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN adopted IS NULL THEN 1 ELSE 0 END) AS pending
        FROM insights
        WHERE COALESCE(category, '') != 'lesson'
        """
    ).fetchone()

    ai_scope = build_ai_attribution_scope_sql(
        "tl",
        include_linked_memo=_table_has_column(db, "trade_logs", "linked_memo_id"),
    )
    verdict_stats = db.execute(
        f"""
        SELECT
            COUNT(*) AS total_scored,
            SUM(CASE WHEN verdict = 'good_call' THEN 1 ELSE 0 END) AS good_call,
            SUM(CASE WHEN verdict = 'regret' THEN 1 ELSE 0 END) AS regret,
            SUM(CASE WHEN verdict = 'missed_opportunity' THEN 1 ELSE 0 END) AS missed_opportunity,
            SUM(CASE WHEN verdict = 'bullet_dodged' THEN 1 ELSE 0 END) AS bullet_dodged
        FROM trade_logs tl
        WHERE tl.verdict IS NOT NULL
          AND {ai_scope}
        """
    ).fetchone()

    return {
        "total": insight_stats[0] or 0,
        "adopted": insight_stats[1] or 0,
        "rejected": insight_stats[2] or 0,
        "pending": insight_stats[3] or 0,
        "good_call": verdict_stats[1] or 0,
        "regret": verdict_stats[2] or 0,
        "missed_opportunity": verdict_stats[3] or 0,
        "bullet_dodged": verdict_stats[4] or 0,
    }


def compute_leaderboard(db: Any) -> list[dict[str, Any]]:
    """Compute per-source decision hit rates and average outcome."""
    ai_scope = build_ai_attribution_scope_sql(
        "tl",
        include_linked_memo=_table_has_column(db, "trade_logs", "linked_memo_id"),
    )
    rows = db.execute(
        f"""
        SELECT
            tl.log_date, tl.asset_id, tl.suggestion_source,
            tl.ai_suggestion, tl.decision_reason,
            tl.verdict, tl.outcome_pct
        FROM trade_logs tl
        WHERE {ai_scope}
        """
    ).fetchall()

    from src.services.decision_intelligence import normalize_source, resolve_trade_linkage

    grouped: dict[str, dict[str, Any]] = {}
    for log_date, asset_id, suggestion_source, ai_suggestion, decision_reason, verdict, outcome_pct in rows:
        linkage = resolve_trade_linkage(
            db,
            asset_id,
            log_date,
            suggestion_source=suggestion_source,
            ai_suggestion=ai_suggestion,
            decision_reason=decision_reason,
        )
        source = normalize_source(linkage["effective_source"])
        if source in (GENERIC_SUGGESTION_SOURCES | {"manual", "human", "user"}):
            continue
        bucket = grouped.setdefault(
            source,
            {"source": source, "total": 0, "scored": 0, "good_call": 0, "outcomes": []},
        )
        bucket["total"] += 1
        if verdict is not None:
            bucket["scored"] += 1
            if verdict == "good_call":
                bucket["good_call"] += 1
        if outcome_pct is not None:
            bucket["outcomes"].append(float(outcome_pct))

    result: list[dict[str, Any]] = []
    for source, stats in grouped.items():
        scored = stats["scored"]
        outcomes = stats["outcomes"]
        avg_outcome_pct = (sum(outcomes) / len(outcomes)) if outcomes else None
        result.append(
            {
                "source": source,
                "total": stats["total"],
                "scored": scored,
                "good_call": stats["good_call"],
                "hit_rate": round(stats["good_call"] / scored * 100, 1) if scored > 0 else 0.0,
                "avg_outcome_pct": round(avg_outcome_pct, 2) if avg_outcome_pct is not None else None,
            }
        )
    result.sort(key=lambda item: (item["good_call"], item["scored"], item["source"]), reverse=True)
    return result
