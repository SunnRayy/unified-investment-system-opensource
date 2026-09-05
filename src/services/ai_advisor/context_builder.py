"""ContextBuilder: assembles context blocks for AI Advisor LLM prompts."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from src.services.portfolio_helpers import fetch_included_asset_ids
from src.database.connector import DatabaseConnector, resolve_db_path
from src.financial_analysis.metrics import calculate_portfolio_metrics
from src.financial_analysis.twr import calculate_portfolio_twr
from src.financial_analysis.xirr import calculate_portfolio_xirr
from src.services.compass_allocation import build_compass_allocation
from src.services.portfolio_semantics import (
    build_portfolio_summary_semantics,
    fetch_wealthos_active_holdings,
)
from src.validation.reader_validator import extract_symbol

logger = logging.getLogger(__name__)


def _escape_like(s: str) -> str:
    """Escape backslash, percent, and underscore for use in SQL LIKE/ILIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _coerce_directives(key_directives) -> list[str]:
    """Best-effort parse of a strategy_memos.key_directives value into a list.

    The column is JSON; DuckDB may hand it back as a Python list or a JSON string.
    Returns [] for None / empty / unparseable input.
    """
    if not key_directives:
        return []
    if isinstance(key_directives, list):
        return [str(d) for d in key_directives if str(d).strip()]
    try:
        parsed = json.loads(key_directives)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(d) for d in parsed if str(d).strip()]
    return []


def _format_analysis_age(created_at) -> str:
    """Return a short age string for recent technical analyses."""
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if getattr(created_at, "tzinfo", None) is not None:
        created_at = created_at.replace(tzinfo=None)

    total_seconds = max(0, (datetime.now() - created_at).total_seconds())
    if total_seconds < 3600:
        return f"{int(total_seconds // 60)}分钟前"
    return f"{int(total_seconds // 3600)}小时前"


# Never use global MAX(snapshot_date) — always per-asset.
_LATEST_PER_ASSET_CTE = """
    latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) AS latest_date
        FROM holdings
        WHERE is_shadow = FALSE
        GROUP BY asset_id
    )
"""

# Fixed token estimates per (tier, detail) for estimate_tokens()
_TOKEN_ESTIMATES = {
    "identity":     {"summary": 400,  "detailed": 900,  "full": 1500},
    "portfolio":    {"summary": 300,  "detailed": 600,  "full": 1000},
    "market":       {"summary": 200,  "detailed": 500,  "full": 700},
    "strategy":     {"30d": 800, "60d": 1500, "90d": 2500},
    "technical":    {"summary": 150,  "detailed": 300,  "full": 500},
    "transactions": {
        "14d": {"summary": 200, "detailed": 300, "full": 450},
        "30d": {"summary": 300, "detailed": 450, "full": 650},
        "6m": {"summary": 600, "detailed": 900, "full": 1400},
        "1y": {"summary": 900, "detailed": 1300, "full": 1900},
        "all": {"summary": 1100, "detailed": 1500, "full": 2200},
    },
}

_AI_INSIGHT_LIMITS = {
    "summary": {"rows": 3, "body_chars": 120},
    "detailed": {"rows": 8, "body_chars": 240},
    "full": {"rows": 20, "body_chars": None},
}

_SUMMARY_PROFILE_CHAR_LIMIT = 500
_SUMMARY_PROFILE_OVERFLOW_GRACE = 80


class ContextBuilder:
    """Assembles context for LLM prompts from DuckDB."""

    def __init__(self):
        self._db = DatabaseConnector()
        self._aia: dict = {}

    # ------------------------------------------------------------------
    # Identity context
    # ------------------------------------------------------------------

    def build_identity_context(self, detail: str = "summary") -> str:
        """Build investor profile + insights context block.

        detail: 'summary' | 'detailed' | 'full'
        """
        sections: list[str] = []

        # --- Investor profile + active risk profile ---
        try:
            profile_block = self._build_investor_profile_section(detail=detail)
            if profile_block:
                sections.append(profile_block)
        except Exception as e:
            logger.warning("build_identity_context profile query failed: %s", e)

        # --- AI insight sediment ---
        try:
            ai_insights_sql = self._build_ai_insights_query(detail)
            ai_rows = self._db.execute(ai_insights_sql).fetchall()
            if ai_rows:
                sections.append(self._format_ai_insights_section(ai_rows, detail))
        except Exception as e:
            logger.warning("build_identity_context ai_insights query failed: %s", e)

        return "\n\n".join(sections)

    def _build_investor_profile_section(self, detail: str = "summary") -> str:
        """Build ## 投资者画像 block from user_profile + active risk profile.

        detail controls depth:
          summary  — goal + risk_tolerance + profile label + top-4 allocations
          detailed — all 4 philosophy bullets + profile label + all allocations
          full     — same as detailed + portfolio structure narrative
        """
        import json as _json

        lines: list[str] = ["## 投资者画像"]
        philosophy: dict = {}

        # User display name + investment philosophy
        try:
            row = self._db.execute(
                "SELECT display_name, philosophy FROM user_profile LIMIT 1"
            ).fetchone()
            if row:
                if row[0]:
                    lines.append(f"- 投资者：{row[0]}")
                if row[1]:
                    try:
                        philosophy = _json.loads(row[1])
                    except Exception:
                        pass
        except Exception:
            pass

        # Philosophy bullets — detail-gated
        goal = philosophy.get("goal", "")
        horizon = philosophy.get("horizon", "")
        risk_tolerance = philosophy.get("risk_tolerance", "")
        core_weakness = philosophy.get("core_weakness", "")
        portfolio_structure = philosophy.get("portfolio_structure", "")

        if detail == "summary":
            if goal:
                lines.append(f"- 目标：{goal}")
            if risk_tolerance:
                lines.append(f"- 风险承受：{risk_tolerance}")
        else:  # detailed or full
            if goal:
                lines.append(f"- 目标：{goal}")
            if horizon:
                lines.append(f"- 期限：{horizon}")
            if risk_tolerance:
                lines.append(f"- 风险承受：{risk_tolerance}")
            if core_weakness:
                lines.append(f"- 核心弱点：{core_weakness}")

        # Active risk profile + allocations
        try:
            profile_row = self._db.execute(
                "SELECT id, name, name_en, description "
                "FROM risk_profiles WHERE is_active = TRUE LIMIT 1"
            ).fetchone()
            if profile_row:
                pid, pname, pname_en, pdesc = profile_row
                label = f"{pname} / {pname_en}" if pname_en else pname
                lines.append(f"- 风险画像：{label}")

                alloc_rows = self._db.execute(
                    """
                    SELECT COALESCE(tc.name_cn, tc.name), rpa.target_pct
                    FROM risk_profile_allocations rpa
                    JOIN taxonomy_classes tc ON rpa.class_id = tc.id
                    WHERE rpa.profile_id = ? AND rpa.target_pct > 0
                    ORDER BY rpa.target_pct DESC
                    """,
                    [pid],
                ).fetchall()
                if alloc_rows:
                    # Summary: show top 4 allocations; detailed/full: show all
                    display_rows = alloc_rows[:4] if detail == "summary" else alloc_rows
                    alloc_str = "，".join(
                        f"{name} {float(pct):.0f}%" for name, pct in display_rows if pct is not None
                    )
                    lines.append(f"- 目标配置：{alloc_str}")
        except Exception:
            pass

        # Full only: portfolio structure narrative
        if detail == "full" and portfolio_structure:
            lines.append(f"- 配置逻辑：{portfolio_structure}")

        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def _build_ai_insights_query(self, detail: str) -> str:
        limit = _AI_INSIGHT_LIMITS.get(detail, _AI_INSIGHT_LIMITS["summary"])["rows"]
        return f"""
            SELECT title, body, status, updated_at
            FROM ai_insights
            WHERE status IN ('validated', 'principle')
            ORDER BY updated_at DESC, id DESC
            LIMIT {limit}
        """

    def _format_ai_insights_section(self, rows: list[tuple[Any, ...]], detail: str) -> str:
        limits = _AI_INSIGHT_LIMITS.get(detail, _AI_INSIGHT_LIMITS["summary"])
        body_chars = limits["body_chars"]
        rows = rows[: limits["rows"]]
        lines = ["## AI洞见沉淀", ""]
        for title, body, status, updated_at in rows:
            body_text = str(body or "").strip()
            if body_chars is not None and len(body_text) > body_chars:
                body_text = body_text[:body_chars].rstrip() + "..."
            lines.append(f"- [{status}] {title} ({updated_at}): {body_text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Portfolio context
    # ------------------------------------------------------------------

    def build_portfolio_context(
        self,
        detail: str = "summary",
        include_non_rebalanceable: bool = True,
    ) -> str:
        """Build portfolio allocation context block.

        detail: 'summary' | 'detailed' | 'full'
        """
        lines: list[str] = []

        try:
            top_level_rows = [
                row for row in build_compass_allocation(
                    self._db,
                    include_non_rebalanceable=include_non_rebalanceable,
                )
                if row.get("is_top_level")
            ]
            lines.append("## 当前资产配置")
            if top_level_rows:
                for row in top_level_rows:
                    class_name = str(row.get("asset_class", "")).split(" (")[0]
                    total_value = float(row.get("current_value") or 0.0)
                    pct = float(row.get("current_pct") or 0.0)
                    lines.append(f"- {class_name}: {pct:.1f}% (¥{total_value:,.0f})")
            else:
                lines.append("- 暂无持仓数据")

            if top_level_rows:
                drift_rows = sorted(
                    top_level_rows,
                    key=lambda row: abs(float(row.get("drift_pct") or 0.0)),
                    reverse=True,
                )[:3]
                if drift_rows:
                    lines.append("\n### 偏离最大的配置")
                    for row in drift_rows:
                        class_name = str(row.get("asset_class", "")).split(" (")[0]
                        current_pct = float(row.get("current_pct") or 0.0)
                        target_pct = float(row.get("target_pct") or 0.0)
                        drift_pct = abs(float(row.get("drift_pct") or 0.0))
                        lines.append(
                            f"- {class_name}: 当前{current_pct:.1f}% vs 目标{target_pct:.1f}% (偏离{drift_pct:.1f}%)"
                        )

        except Exception as e:
            logger.warning("build_portfolio_context allocation query failed: %s", e)
            lines.append("## 当前资产配置\n\n- 数据暂不可用")

        try:
            performance_section = self._build_performance_context(include_non_rebalanceable)
            if performance_section:
                lines.append("\n" + performance_section)
        except Exception as e:
            logger.warning("build_portfolio_context performance query failed: %s", e)

        # Per-asset table for detailed/full
        if detail in ("detailed", "full"):
            try:
                asset_rows = fetch_wealthos_active_holdings(
                    self._db,
                    include_non_rebalanceable=include_non_rebalanceable,
                )
                if asset_rows:
                    lines.append("\n### 持仓明细")
                    total_value = sum(float(row.get("market_value") or 0.0) for row in asset_rows) or 1.0
                    lines.append("| 资产 | 市值(CNY) | Lifetime P&L | Return % | 权重% |")
                    lines.append("|------|-----------|--------------|----------|-------|")
                    for row in asset_rows:
                        asset_id = str(row.get("asset_id") or "")
                        label = _format_trade_label(asset_id, str(row.get("name") or ""))
                        market_value = float(row.get("market_value") or 0.0)
                        weight_pct = market_value / total_value * 100.0
                        # lifetime_pl / return_pct are None for balance-only assets
                        # (cost unknown) — render "—" rather than a fabricated ¥0.
                        raw_pl = row.get("lifetime_pl")
                        raw_ret = row.get("return_pct")
                        pl_cell = f"¥{float(raw_pl):,.0f}" if raw_pl is not None else "—"
                        ret_cell = f"{float(raw_ret):.1f}%" if raw_ret is not None else "—"
                        lines.append(
                            f"| {label} | ¥{market_value:,.0f} | {pl_cell} | {ret_cell} | {weight_pct:.1f}% |"
                        )
            except Exception as e:
                logger.warning("build_portfolio_context per-asset query failed: %s", e)

        # For full detail, inline recent transactions
        if detail == "full":
            tx_context = self.build_transactions_context("30d", detail="full")
            if tx_context:
                lines.append("\n" + tx_context)

        return "\n".join(lines)

    def build_asset_context(self, asset_code: str) -> dict:
        """Build per-asset context payload for single-asset analysis."""
        context = {
            "asset_code": asset_code,
            "asset_name": None,
            "position": None,
            "allocation": None,
            "recent_trades": [],
            "related_memos": [],
            "philosophy_excerpt": "",
        }

        try:
            symbol = extract_symbol(asset_code)
            escaped_symbol = _escape_like(symbol) if symbol else _escape_like(asset_code)
            asset_pattern = f"%\\_{escaped_symbol}" if symbol else _escape_like(asset_code)
            resolved_db = resolve_db_path(getattr(self._db, "db_path", "data/unified.duckdb"))
            logger.debug("build_asset_context db=%s asset=%s symbol=%s", resolved_db, asset_code, symbol)

            # Latest held position for exact id or same symbol family.
            row = self._db.execute(
                f"""
                WITH {_LATEST_PER_ASSET_CTE}
                SELECT h.asset_id, h.asset_name, h.quantity, h.cost_price_unit,
                       h.market_value, h.market_price_unit, h.currency
                FROM holdings h
                JOIN latest_per_asset lpa
                  ON h.asset_id = lpa.asset_id
                 AND h.snapshot_date = lpa.latest_date
                WHERE h.is_shadow = FALSE
                  AND (h.asset_id = ? OR h.asset_id ILIKE ? ESCAPE '\\')
                LIMIT 1
                """,
                (asset_code, asset_pattern),
            ).fetchone()

            position_market_value = None
            if row:
                _, asset_name, quantity, cost_price_unit, market_value, market_price_unit, currency = row
                context["asset_name"] = asset_name
                position_market_value = float(market_value or 0)
                position = {
                    "quantity": float(quantity or 0),
                    "cost_price_unit": float(cost_price_unit or 0),
                    "market_value_cny": position_market_value,
                    "market_price_unit": float(market_price_unit or 0),
                    "currency": currency,
                }
                if str(currency or "").upper() != "CNY":
                    position["market_value_note"] = "market_value is normalized to CNY"
                context["position"] = position

            # Portfolio total uses non-shadow latest-per-asset values.
            total_row = self._db.execute(
                f"""
                WITH {_LATEST_PER_ASSET_CTE}
                SELECT SUM(h.market_value)
                FROM holdings h
                JOIN latest_per_asset lpa
                  ON h.asset_id = lpa.asset_id
                 AND h.snapshot_date = lpa.latest_date
                WHERE h.is_shadow = FALSE
                """
            ).fetchone()
            total_value = float(total_row[0] or 0) if total_row else 0.0

            if position_market_value is not None and total_value > 0:
                current_pct = (position_market_value / total_value) * 100
                context["allocation"] = {
                    "current_pct": round(current_pct, 2),
                    "target_pct": None,
                    "drift_pct": None,
                }

            # Recent trades are optional.
            try:
                trade_rows = self._db.execute(
                    """
                    SELECT log_date, action, price, quantity, amount, currency
                    FROM trade_logs
                    WHERE asset_id = ? OR asset_id ILIKE ? ESCAPE '\\'
                    ORDER BY log_date DESC
                    LIMIT 5
                    """,
                    (asset_code, asset_pattern),
                ).fetchall()
                context["recent_trades"] = [
                    {
                        "date": str(log_date),
                        "action": action,
                        "price": float(price or 0),
                        "quantity": float(quantity or 0),
                        "amount": float(amount or 0),
                        "currency": currency,
                    }
                    for log_date, action, price, quantity, amount, currency in trade_rows
                ]
            except Exception as e:
                logger.debug("build_asset_context trade_logs unavailable: %s", e)

            # Related strategy memos by symbol mention.
            try:
                memo_rows = self._db.execute(
                    """
                    SELECT memo_date, title, content
                    FROM strategy_memos
                    WHERE title ILIKE ? ESCAPE '\\' OR content ILIKE ? ESCAPE '\\'
                    ORDER BY memo_date DESC
                    LIMIT 3
                    """,
                    (f"%{escaped_symbol}%", f"%{escaped_symbol}%"),
                ).fetchall()
                context["related_memos"] = [
                    {
                        "date": str(memo_date),
                        "title": title,
                        "content": str(content or "")[:500],
                    }
                    for memo_date, title, content in memo_rows
                ]
            except Exception as e:
                logger.debug("build_asset_context strategy_memos unavailable: %s", e)


        except Exception as e:
            logger.warning("build_asset_context failed for %s: %s", asset_code, e)

        return context

    def _build_performance_context(self, include_non_rebalanceable: bool) -> str:
        """Build performance and risk metrics using shared report calculators."""
        exclude_non_balanceable = not include_non_rebalanceable

        try:
            include_asset_ids = (
                fetch_included_asset_ids(self._db) if exclude_non_balanceable else None
            )
            perf_summary = build_portfolio_summary_semantics(
                self._db,
                include_non_rebalanceable=include_non_rebalanceable,
            )
            twr_total = calculate_portfolio_twr(
                self._db,
                include_asset_ids=include_asset_ids,
                exclude_non_balanceable=exclude_non_balanceable,
            )
            xirr_total = calculate_portfolio_xirr(
                self._db,
                include_asset_ids=include_asset_ids,
            )
            risk_total = calculate_portfolio_metrics(
                self._db,
                include_asset_ids=include_asset_ids,
                exclude_non_balanceable=exclude_non_balanceable,
            )
        except Exception as e:
            logger.debug("performance context failed: %s", e)
            return ""

        lines = ["## 绩效与风险指标"]
        lines.append(
            f"- Net Worth: ¥{float(perf_summary.get('net_worth') or 0.0):,.0f}"
        )
        lines.append(
            f"- Cost Basis: ¥{float(perf_summary.get('total_cost_basis') or 0.0):,.0f}"
        )
        lines.append(
            f"- Unrealized P&L: ¥{float(perf_summary.get('total_unrealized_pl') or 0.0):,.0f} ({float(perf_summary.get('unrealized_pl_pct') or 0.0):.2f}%)"
        )
        lines.append(
            f"- Realized P&L: ¥{float(perf_summary.get('total_realized_pl') or 0.0):,.0f}"
        )
        lines.append(
            f"- Lifetime P&L: ¥{float(perf_summary.get('total_lifetime_pl') or 0.0):,.0f}"
        )
        lines.append(
            f"- TWR (Cumulative): {((twr_total or {}).get('cumulative', 0.0) * 100):.2f}%"
            if twr_total else "- TWR (Cumulative): N/A"
        )
        lines.append(
            f"- TWR (Annualized): {((twr_total or {}).get('annualized', 0.0) * 100):.2f}%"
            if twr_total and twr_total.get("annualized") is not None else "- TWR (Annualized): N/A"
        )
        lines.append(
            f"- MWR (XIRR): {xirr_total * 100:.2f}%"
            if xirr_total is not None else "- MWR (XIRR): N/A"
        )
        lines.append(
            f"- Total Return (Historical): {float((risk_total or {}).get('total_return')):.2f}%"
            if (risk_total or {}).get("total_return") is not None else "- Total Return (Historical): N/A"
        )
        lines.append(
            f"- Volatility (Annualized): {float((risk_total or {}).get('volatility_annual')):.2f}%"
            if (risk_total or {}).get("volatility_annual") is not None else "- Volatility (Annualized): N/A"
        )
        lines.append(
            f"- Max Drawdown: {float((risk_total or {}).get('max_drawdown')):.2f}%"
            if (risk_total or {}).get("max_drawdown") is not None else "- Max Drawdown: N/A"
        )
        lines.append(
            f"- Sharpe Ratio: {float((risk_total or {}).get('sharpe_ratio')):.2f}"
            if (risk_total or {}).get("sharpe_ratio") is not None else "- Sharpe Ratio: N/A"
        )
        lines.append(
            f"- Sortino Ratio: {float((risk_total or {}).get('sortino_ratio')):.2f}"
            if (risk_total or {}).get("sortino_ratio") is not None else "- Sortino Ratio: N/A"
        )
        lines.append(
            f"- Calmar Ratio: {float((risk_total or {}).get('calmar_ratio')):.2f}"
            if (risk_total or {}).get("calmar_ratio") is not None else "- Calmar Ratio: N/A"
        )
        lines.append(f"- Data Points: {int((risk_total or {}).get('data_points') or 0)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Market context
    # ------------------------------------------------------------------

    def build_market_context(self, detail: str = "summary") -> str:
        """Build market sentiment context block."""
        lines: list[str] = ["## 市场情绪"]
        try:
            if detail == "summary":
                row = self._db.execute(
                    """
                    SELECT section, indicator_name, display_value, zone
                    FROM market_sentiment_cache
                    ORDER BY section, indicator_key
                    """
                ).fetchall()
                if row:
                    current_section = None
                    for section, indicator_name, display_value, zone in row:
                        if section != current_section:
                            current_section = section
                            lines.append(f"\n### {section}")
                        lines.append(f"- {indicator_name}: {display_value} ({zone})")
                else:
                    lines.append("Market data unavailable")
            elif detail == "detailed":
                rows = self._db.execute(
                    """
                    SELECT section, indicator_key, indicator_name, display_value, zone, updated_at
                    FROM market_sentiment_cache
                    ORDER BY section, indicator_key
                    """
                ).fetchall()
                if rows:
                    current_section = None
                    for section, indicator_key, indicator_name, display_value, zone, updated_at in rows:
                        if section != current_section:
                            current_section = section
                            lines.append(f"\n### {section}")
                        lines.append(
                            f"- {indicator_name} [{indicator_key}]: {display_value} ({zone})"
                            f" · {updated_at}"
                        )
                else:
                    lines.append("Market data unavailable")
            else:
                rows = self._db.execute(
                    "SELECT * FROM market_sentiment_cache ORDER BY updated_at DESC"
                ).fetchall()
                if rows:
                    lines.append("\n### 原始市场缓存")
                    lines.append("| Raw Row |")
                    lines.append("|---------|")
                    for r in rows:
                        lines.append(f"| {r} |")
                else:
                    lines.append("Market data unavailable")
        except Exception as e:
            logger.debug("market_sentiment_cache not available: %s", e)
            lines.append("Market data not yet synced.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Strategy context
    # ------------------------------------------------------------------

    def build_strategy_context(self, timeframe: str = "30d") -> str:
        """Build strategy memo context block.

        timeframe: '30d' | '60d' | '90d' (ignored when reading from filesystem)
        All memo content is included in full — no truncation.

        Priority:
          1. If _aia["strategy_path"] is set and contains .md files, read them
             from the filesystem (sorted by filename descending) and return.
          2. Otherwise fall back to the strategy_memos DB table.
        """
        # --- Filesystem path (primary when configured) ---
        strategy_path = getattr(self, "_aia", {}).get("strategy_path")
        if strategy_path:
            import glob as _glob  # noqa: PLC0415

            md_files = sorted(
                _glob.glob(os.path.join(strategy_path, "*.md")), reverse=True
            )
            if md_files:
                sections: list[str] = ["## 策略备忘"]
                for fpath in md_files:
                    fname = os.path.basename(fpath)
                    try:
                        with open(fpath, encoding="utf-8") as fh:
                            body = fh.read().strip()
                    except Exception as e:
                        logger.debug("Could not read strategy file %s: %s", fpath, e)
                        continue
                    if body:
                        sections.append(f"\n### {fname}\n\n{body}")
                if len(sections) > 1:
                    return "\n".join(sections)
                return "No strategy memos found."

        # --- DB fallback ---
        days_map = {"30d": 30, "60d": 60, "90d": 90}
        days = days_map.get(timeframe, 30)
        cutoff = str(date.today() - timedelta(days=days))

        try:
            memo_rows = self._db.execute(
                """
                SELECT memo_date, title, strategic_bias, key_directives, content
                FROM strategy_memos
                WHERE CAST(memo_date AS VARCHAR) >= ?
                ORDER BY memo_date DESC
                """,
                [cutoff],
            ).fetchall()
        except Exception as e:
            logger.debug("strategy_memos query unavailable: %s", e)
            memo_rows = []

        if memo_rows:
            sections_db: list[str] = ["## 策略备忘"]
            for memo_date, title, strategic_bias, key_directives, content in memo_rows:
                heading = f"{memo_date} · {title or 'Untitled Memo'}"
                if strategic_bias:
                    heading += f" [{strategic_bias}]"
                # The full memo text is authoritative (docstring: include in full,
                # no truncation). key_directives is only an extracted bullet subset
                # that is empty for memos whose body doesn't use 1./-/* bullets
                # (GitHub #25) — so render `content`, and only fall back to the
                # directives when content is somehow empty. Never emit a bare "[]".
                body = (content or "").strip()
                if not body:
                    directives = _coerce_directives(key_directives)
                    body = (
                        "\n".join(f"- {d}" for d in directives)
                        if directives else "(无正文)"
                    )
                sections_db.append(f"\n### {heading}\n\n{body}")
            return "\n".join(sections_db)

        return "No strategy memos found."

    # ------------------------------------------------------------------
    # Transactions context
    # ------------------------------------------------------------------

    def build_transactions_context(self, timeframe: str = "14d", detail: str = "summary") -> str:
        """Build recent trades context block.

        timeframe: '14d' | '30d' | '6m' | '1y' | 'all'
        detail: 'summary' | 'detailed' | 'full'
        """
        lines: list[str] = ["## 近期交易"]
        try:
            timeframe_sql, params = _transaction_timeframe_sql(timeframe)
            if detail == "summary":
                sql = f"""
                SELECT tl.log_date,
                       tl.asset_id,
                       COALESCE(NULLIF(TRIM(tl.asset_name), ''), ar.display_name) AS display_name,
                       COALESCE(NULLIF(TRIM(ar.base_currency), ''), CASE
                           WHEN tl.asset_id LIKE 'US\\_%' ESCAPE '\\' THEN 'USD'
                           WHEN tl.asset_id LIKE 'HK\\_%' ESCAPE '\\' THEN 'HKD'
                           ELSE 'CNY'
                       END) AS currency,
                       tl.action,
                       tl.quantity,
                       tl.price,
                       tl.decision_grade
                FROM trade_logs tl
                LEFT JOIN asset_registry ar ON tl.asset_id = ar.canonical_id
                {timeframe_sql}
                ORDER BY tl.log_date DESC, tl.id DESC
                """
            elif detail == "detailed":
                sql = f"""
                SELECT tl.log_date,
                       tl.asset_id,
                       COALESCE(NULLIF(TRIM(tl.asset_name), ''), ar.display_name) AS display_name,
                       COALESCE(NULLIF(TRIM(ar.base_currency), ''), CASE
                           WHEN tl.asset_id LIKE 'US\\_%' ESCAPE '\\' THEN 'USD'
                           WHEN tl.asset_id LIKE 'HK\\_%' ESCAPE '\\' THEN 'HKD'
                           ELSE 'CNY'
                       END) AS currency,
                       tl.action,
                       tl.quantity,
                       tl.price,
                       tl.amount,
                       tl.decision_grade
                FROM trade_logs tl
                LEFT JOIN asset_registry ar ON tl.asset_id = ar.canonical_id
                {timeframe_sql}
                ORDER BY tl.log_date DESC, tl.id DESC
                """
            else:
                sql = f"""
                SELECT tl.log_date,
                       tl.asset_id,
                       COALESCE(NULLIF(TRIM(tl.asset_name), ''), ar.display_name) AS display_name,
                       COALESCE(NULLIF(TRIM(ar.base_currency), ''), CASE
                           WHEN tl.asset_id LIKE 'US\\_%' ESCAPE '\\' THEN 'USD'
                           WHEN tl.asset_id LIKE 'HK\\_%' ESCAPE '\\' THEN 'HKD'
                           ELSE 'CNY'
                       END) AS currency,
                       tl.action,
                       tl.quantity,
                       tl.price,
                       tl.amount,
                       tl.decision_grade,
                       tl.decision_reason,
                       tl.ai_suggestion,
                       tl.suggestion_source,
                       tl.verification_result
                FROM trade_logs tl
                LEFT JOIN asset_registry ar ON tl.asset_id = ar.canonical_id
                {timeframe_sql}
                ORDER BY tl.log_date DESC, tl.id DESC
                """

            rows = self._db.execute(sql, params).fetchall()
            if not rows:
                return "No recent trades."

            if detail == "summary":
                lines.append("| 日期 | 资产 | 操作 | 数量 | 价格 | 评级 |")
                lines.append("|------|------|------|------|------|------|")
                for date, asset_id, display_name, currency, action, qty, price, grade in rows:
                    label = _format_trade_label(asset_id, display_name)
                    currency = _infer_trade_currency(asset_id, currency)
                    price_text = _format_currency_value(currency, price)
                    lines.append(
                        f"| {date} | {label} | {action} | {qty} | {price_text} | {grade or ''} |"
                    )
            elif detail == "detailed":
                lines.append("| 日期 | 资产 | 操作 | 数量 | 价格 | 金额 | 评级 |")
                lines.append("|------|------|------|------|------|------|------|")
                for date, asset_id, display_name, currency, action, qty, price, amount, grade in rows:
                    label = _format_trade_label(asset_id, display_name)
                    currency = _infer_trade_currency(asset_id, currency)
                    price_text = _format_currency_value(currency, price)
                    amount_value = _format_numeric_value(amount)
                    lines.append(
                        f"| {date} | {label} | {action} | {qty} | {price_text} | {amount_value} | {grade or ''} |"
                    )
            else:
                lines.append("| 日期 | 资产 | 操作 | 数量 | 价格 | 金额 | 评级 | 原因 | AI建议 | 验证 |")
                lines.append("|------|------|------|------|------|------|------|------|------|------|")
                for date, asset_id, display_name, currency, action, qty, price, amount, grade, reason, ai_suggestion, suggestion_source, verification_result in rows:
                    label = _format_trade_label(asset_id, display_name)
                    currency = _infer_trade_currency(asset_id, currency)
                    price_text = _format_currency_value(currency, price)
                    amount_value = _format_numeric_value(amount)
                    lines.append(
                        f"| {date} | {label} | {action} | {qty} | {price_text} | {amount_value} | {grade or ''} | {reason or ''} | {ai_suggestion or suggestion_source or ''} | {verification_result or ''} |"
                    )
        except Exception as e:
            logger.debug("build_transactions_context failed: %s", e)
            return "No recent trades."

        return "\n".join(lines)

    def build_review_trade_summary(self, period_start: str, period_end: str) -> str | None:
        """Build the trade summary block for review prompts using the shared DB connection."""
        try:
            rows = self._db.execute(
                """
                SELECT
                    tl.log_date,
                    tl.asset_id,
                    COALESCE(NULLIF(TRIM(tl.asset_name), ''), ar.display_name) AS display_name,
                    tl.action,
                    tl.quantity,
                    tl.price,
                    tl.decision_grade
                FROM trade_logs tl
                LEFT JOIN asset_registry ar ON tl.asset_id = ar.canonical_id
                WHERE tl.log_date >= ? AND tl.log_date <= ?
                ORDER BY tl.log_date
                """,
                [period_start, period_end],
            ).fetchall()
        except Exception as e:
            logger.warning("build_review_trade_summary failed: %s", e)
            return None

        if not rows:
            return None

        lines = []
        for log_date, asset_id, display_name, action, quantity, price, grade in rows:
            label = _format_trade_label(asset_id, display_name)
            currency = _infer_trade_currency(asset_id)
            price_text = _format_currency_value(currency, price)
            lines.append(
                f"{log_date} | {label} | {action} | qty={quantity} | price={price_text} | grade={grade}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Realtime context (slow, optional)
    # ------------------------------------------------------------------

    def build_realtime_context(self) -> str:
        """Fetch live market indicators. Slow (3-8s). Optional — caller decides."""
        try:
            from src.financial_analysis.macro_analyzer import MacroAnalyzer
            results = MacroAnalyzer().fetch_all()
            if not results:
                return ""
            lines = ["## 实时市场指标"]
            for item in results:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    value = item.get("value", "N/A")
                    signal = item.get("signal", "")
                    lines.append(f"- {name}: {value}" + (f" [{signal}]" if signal else ""))
            return "\n".join(lines)
        except Exception as e:
            logger.debug("build_realtime_context failed: %s", e)
            return ""

    def build_valuation_context(self) -> str:
        """Build valuation dashboard context block from valuation_snapshots."""
        try:
            count = self._db.execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()
            if not count or count[0] == 0:
                return ""
        except Exception:
            return ""
        try:
            rows = self._db.execute("""
                WITH latest AS (
                    SELECT ticker, MAX(snapshot_date) AS max_date
                    FROM valuation_snapshots
                    GROUP BY ticker
                )
                SELECT vs.ticker, vs.asset_class, vs.valuation_signal,
                       vs.pe_ttm, vs.pe_forward, vs.pe_ttm_pct, vs.pe_fwd_pct,
                       vs.sec_yield, vs.signal_basis, vs.snapshot_date
                FROM valuation_snapshots vs
                JOIN latest l ON vs.ticker = l.ticker AND vs.snapshot_date = l.max_date
                WHERE vs.is_estimable = TRUE
                ORDER BY vs.asset_class, vs.ticker
            """).fetchall()
        except Exception as e:
            logger.debug("build_valuation_context query failed: %s", e)
            return ""
        if not rows:
            return ""
        lines = ["## 估值仪表盘\n"]
        lines.append(
            "> ⚠️ 数据说明：以下估值数据来自系统快照，**仅供参考，非实时数据**。"
            "请务必结合最新市场信息交叉验证后再做决策。"
        )
        lines.append("")
        lines.append("| 资产 | 类型 | 当前估值 | 历史%位 | 信号 | 更新日期 |")
        lines.append("|------|------|---------|---------|------|---------|")
        for r in rows:
            ticker, cls, signal, pe_ttm, pe_fwd, ttm_pct, fwd_pct, sec_yld, basis, snap_date = r
            if cls in ("US_STOCK", "US_ETF"):
                val = f"Fwd PE {pe_fwd:.1f}" if pe_fwd else "n/a"
                pct = f"{fwd_pct:.0f}%" if fwd_pct else "n/a"
            elif cls == "US_BOND_ETF":
                val = f"Yield {sec_yld:.2f}%" if sec_yld else "n/a"
                pct = "n/a"
            else:
                val = f"PE-TTM {pe_ttm:.1f}" if pe_ttm else "n/a"
                pct = f"{ttm_pct:.0f}%" if ttm_pct else "n/a"
            lines.append(f"| {ticker} | {cls} | {val} | {pct} | {signal or 'N/A'} | {snap_date} |")
        return "\n".join(lines)

    def build_technical_context(self, detail: str = "summary") -> str:
        """Build recent technical-analysis context for currently held assets."""
        try:
            held_rows = self._db.execute(
                f"""
                WITH {_LATEST_PER_ASSET_CTE}
                SELECT h.asset_id
                FROM holdings h
                JOIN latest_per_asset lpa
                  ON h.asset_id = lpa.asset_id
                 AND h.snapshot_date = lpa.latest_date
                WHERE h.is_shadow = FALSE
                  AND h.market_value > 0
                ORDER BY h.market_value DESC, h.asset_id
                """
            ).fetchall()
        except Exception as e:
            logger.warning("build_technical_context holdings query failed: %s", e)
            return ""

        held_codes: set[str] = set()
        for row in held_rows:
            asset_id = str((row[0] if row else "") or "").strip()
            if not asset_id:
                continue
            held_codes.add(asset_id.upper())
            symbol = str(extract_symbol(asset_id) or "").strip().upper()
            if symbol:
                held_codes.add(symbol)

        if not held_codes:
            return ""

        try:
            analysis_rows = self._db.execute(
                """
                SELECT asset_code, asset_name, technical_signals, created_at
                FROM (
                    SELECT asset_code, asset_name, technical_signals, created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY created_at DESC, id DESC
                           ) AS rn
                    FROM asset_analyses
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                      AND analysis_type = 'full'
                      AND UPPER(TRIM(asset_code)) = ANY(?)
                ) sub
                WHERE rn = 1
                ORDER BY created_at DESC
                LIMIT 10
                """,
                [sorted(held_codes)],
            ).fetchall()
        except Exception as e:
            logger.warning("build_technical_context analyses query failed: %s", e)
            return ""

        lines: list[str] = []
        for asset_code, asset_name, technical_signals, created_at in analysis_rows:
            try:
                if isinstance(technical_signals, dict):
                    signals = technical_signals
                elif isinstance(technical_signals, str):
                    signals = json.loads(technical_signals)
                else:
                    raise TypeError(
                        f"Unsupported technical_signals type: {type(technical_signals)!r}"
                    )

                trend_status = signals.get("trend_status", "N/A")
                rsi_value = float(signals.get("rsi_value") or 0.0)
                rsi_status = signals.get("rsi_status", "N/A")
                macd_status = signals.get("macd_status", "N/A")
                volume_status = signals.get("volume_status", "N/A")
                signal_score = signals.get("signal_score", 0)
                support_levels = signals.get("support_levels") or []
                resistance_levels = signals.get("resistance_levels") or []

                summary_line = (
                    f"- {asset_code}({asset_name or asset_code}) {_format_analysis_age(created_at)}: "
                    f"{trend_status} | RSI={rsi_value:.1f}({rsi_status}) | 评分{signal_score}/100"
                )
                if detail == "summary":
                    lines.append(summary_line)
                    continue

                detailed_line = f"{summary_line} | MACD={macd_status} | 量能={volume_status}"
                if detail == "detailed":
                    lines.append(detailed_line)
                    continue

                full_line = detailed_line
                if support_levels:
                    full_line += f" | 支撑={', '.join(str(level) for level in support_levels)}"
                if resistance_levels:
                    full_line += f" | 阻力={', '.join(str(level) for level in resistance_levels)}"
                lines.append(full_line)
            except Exception as e:
                logger.warning(
                    "build_technical_context could not process row for %s: %s",
                    asset_code,
                    e,
                )
                continue

        if not lines:
            return ""

        return "## 近期技术分析\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    def estimate_tokens(self, config: dict) -> dict:
        """Estimate token counts for a given context configuration.

        config example:
        {
            "identity":     {"enabled": True, "detail": "summary"},
            "portfolio":    {"enabled": True, "detail": "detailed"},
            "market":       {"enabled": False, "detail": "summary"},
            "strategy":     {"enabled": True, "detail": "summary"},
            "transactions": {"enabled": True, "detail": "14d"},
            "timeframe":    "14d",  # optional top-level shorthand
        }

        Returns dict with per-tier estimates plus "total".
        """
        result: dict[str, Any] = {}
        total = 0

        tiers = ["identity", "portfolio", "market", "strategy", "transactions", "technical"]
        for tier in tiers:
            tier_cfg = config.get(tier, {})
            enabled = tier_cfg.get("enabled", False)
            detail = tier_cfg.get("detail", "summary")

            tier_estimates = _TOKEN_ESTIMATES.get(tier, {})
            if tier == "transactions":
                timeframe = tier_cfg.get("timeframe", config.get("timeframe", "14d"))
                estimated = tier_estimates.get(timeframe, {}).get(detail, 0)
            elif tier == "strategy":
                timeframe = tier_cfg.get("timeframe", "30d")
                estimated = tier_estimates.get(timeframe, 0)
            else:
                estimated = tier_estimates.get(detail, 0)

            result[tier] = {
                "enabled": enabled,
                "detail": detail,
                "estimated_tokens": estimated if enabled else 0,
            }
            if enabled:
                total += estimated

        result["total"] = total
        return result


def render_context(context_builder: ContextBuilder, context_config: dict) -> str:
    """Render full prompt context text from a context config."""
    tiers_cfg: dict = context_config.get("tiers", {})
    include_non_rebalanceable = context_config.get("include_non_rebalanceable", True)
    context_blocks: list[str] = []

    for tier_name, tier_cfg in tiers_cfg.items():
        if not tier_cfg.get("enabled", False):
            continue

        detail = tier_cfg.get("detail", "summary")
        try:
            if tier_name == "identity":
                block = context_builder.build_identity_context(detail=detail)
            elif tier_name == "portfolio":
                block = context_builder.build_portfolio_context(
                    detail=detail,
                    include_non_rebalanceable=include_non_rebalanceable,
                )
            elif tier_name == "market":
                block = context_builder.build_market_context(detail=detail)
            elif tier_name == "strategy":
                timeframe = tier_cfg.get("timeframe", "30d")
                block = context_builder.build_strategy_context(timeframe=timeframe)
            elif tier_name == "transactions":
                timeframe = tier_cfg.get("timeframe", detail)
                block = context_builder.build_transactions_context(timeframe=timeframe, detail=detail)
            else:
                logger.debug("Unknown tier '%s', skipping", tier_name)
                continue
            if block:
                context_blocks.append(block)
        except Exception as e:
            logger.warning("Failed to build %s context: %s", tier_name, e)

    if context_config.get("include_realtime", False):
        try:
            realtime_block = context_builder.build_realtime_context()
            if realtime_block:
                context_blocks.append(realtime_block)
        except Exception as e:
            logger.warning("Failed to build realtime context: %s", e)

    if context_config.get("include_valuation_context", True):
        try:
            valuation_block = context_builder.build_valuation_context()
            if valuation_block:
                context_blocks.append(valuation_block)
        except Exception as e:
            logger.warning("Failed to build valuation context: %s", e)

    if context_config.get("include_technical_context", True):
        try:
            technical_block = context_builder.build_technical_context(detail="summary")
            if technical_block:
                context_blocks.append(technical_block)
        except Exception as e:
            logger.warning("Failed to build technical context: %s", e)

    if context_blocks:
        return "\n\n".join(context_blocks)
    return "（持仓和市场数据暂不可用。请仅说明数据不足，不要编造分析。）"


def _transaction_timeframe_sql(timeframe: str) -> tuple[str, list[Any]]:
    mapping = {
        "14d": "CURRENT_DATE - INTERVAL '14' DAY",
        "30d": "CURRENT_DATE - INTERVAL '30' DAY",
        "6m": "CURRENT_DATE - INTERVAL '6' MONTH",
        "1y": "CURRENT_DATE - INTERVAL '1' YEAR",
    }
    if timeframe == "all":
        return "", []
    start_expr = mapping.get(timeframe, mapping["14d"])
    return f"WHERE tl.log_date >= {start_expr}", []


def _format_trade_label(asset_id: str, display_name: str | None) -> str:
    symbol = extract_symbol(asset_id)
    name = (display_name or "").strip()
    if not name or name.lower() == asset_id.lower():
        return symbol or asset_id
    if symbol and name.lower() != symbol.lower():
        return f"{name} ({symbol})"
    return name


def _format_numeric_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_currency_value(currency: str, value: Any) -> str:
    numeric = _format_numeric_value(value)
    if not numeric:
        return f"{currency} N/A"
    return f"{currency} {numeric}"




def _infer_trade_currency(asset_id: str, currency_hint: str | None = None) -> str:
    if asset_id.startswith("US_"):
        return "USD"
    if asset_id.startswith("HK_"):
        return "HKD"
    if asset_id.startswith("CN_"):
        return "CNY"
    if currency_hint and str(currency_hint).strip():
        hint = str(currency_hint).strip().upper()
        if hint != "CNY":
            return hint
    return "CNY"


# ---------------------------------------------------------------------------
# V5.8.0 Cross-Check Context Builder (Step 6)
# ---------------------------------------------------------------------------

_MAX_PERIOD_DAYS = 90
_MAX_INSIGHTS = 50
_MAX_TRADES = 100


def build_cross_check_context(
    db: "DatabaseConnector",
    period_start: date,
    period_end: date,
) -> dict:
    """Build insight↔trade cross-check context for the LLM audit prompt.

    Returns a data dict on success or an error dict (HTTP 422-compatible) if caps exceeded.
    Caps: window ≤ 90 days, insights ≤ 50, linked trades ≤ 100.
    Uses a deterministic dedup CTE to avoid double-counting insight↔trade pairs.
    """
    # --- Cap 1: window size ---
    current_days = (period_end - period_start).days
    if current_days > _MAX_PERIOD_DAYS:
        return {
            "error": "period_too_large",
            "max_days": _MAX_PERIOD_DAYS,
            "current_days": current_days,
        }

    period_start_str = str(period_start)
    period_end_str = str(period_end)

    # --- Cap 2: insights count (fail-fast) ---
    count_row = db.execute(
        "SELECT COUNT(*) FROM insights WHERE insight_date BETWEEN ? AND ?",
        [period_start_str, period_end_str],
    ).fetchone()
    insight_count = count_row[0] if count_row else 0
    if insight_count > _MAX_INSIGHTS:
        return {
            "error": "too_many_insights",
            "max_insights": _MAX_INSIGHTS,
            "current": insight_count,
        }

    # --- Fetch insights ---
    insight_rows = db.execute(
        """
        SELECT id, insight_date, content, ai_model, adopted, category
        FROM insights
        WHERE insight_date BETWEEN ? AND ?
        ORDER BY insight_date, id
        """,
        [period_start_str, period_end_str],
    ).fetchall()

    if not insight_rows:
        return {
            "period": {
                "start": period_start_str,
                "end": period_end_str,
                "days": current_days,
            },
            "insights": [],
            "summary": {
                "total_insights": 0,
                "adopted": 0,
                "rejected": 0,
                "pending": 0,
                "total_linked_trades": 0,
                "total_verified": 0,
                "good_calls": 0,
                "regrets": 0,
            },
        }

    # --- Per-insight aggregation: read from persisted link table if available ---
    # Falls back to the runtime source-match join on pre-V5.10.0 DBs (table absent).
    _use_link_table = False
    try:
        db.execute("SELECT 1 FROM insight_trade_links LIMIT 0")
        _use_link_table = True
    except Exception:
        pass

    if _use_link_table:
        dedup_sql = """
            SELECT
                i.id AS insight_id,
                itl.trade_id AS trade_id,
                0 AS temporal_distance,
                tl.log_date,
                tl.asset_id,
                tl.action,
                tl.verdict,
                tl.outcome_pct,
                tl.verification_result,
                tl.verification_status,
                tl.verification_date
            FROM insights i
            LEFT JOIN insight_trade_links itl ON itl.insight_id = i.id
            LEFT JOIN trade_logs tl ON tl.id = itl.trade_id
            WHERE i.insight_date BETWEEN ? AND ?
            ORDER BY i.id
        """
    else:
        dedup_sql = """
            SELECT
                i.id AS insight_id,
                tl.id AS trade_id,
                ABS(date_diff('day', i.insight_date, tl.log_date)) AS temporal_distance,
                tl.log_date,
                tl.asset_id,
                tl.action,
                tl.verdict,
                tl.outcome_pct,
                tl.verification_result,
                tl.verification_status,
                tl.verification_date
            FROM insights i
            LEFT JOIN trade_logs tl
                ON tl.suggestion_source IS NOT NULL
                AND LOWER(tl.suggestion_source) = LOWER(i.ai_model)
                AND ABS(date_diff('day', i.insight_date, tl.log_date)) <= 3
            WHERE i.insight_date BETWEEN ? AND ?
            ORDER BY i.id, ABS(date_diff('day', i.insight_date, tl.log_date))
        """
    link_rows = db.execute(dedup_sql, [period_start_str, period_end_str]).fetchall()

    # --- Cap 3: total linked trades ---
    non_null_trade_ids = [r[1] for r in link_rows if r[1] is not None]
    total_linked_trades = len(set(non_null_trade_ids))
    if total_linked_trades > _MAX_TRADES:
        return {
            "error": "too_many_trades",
            "max_trades": _MAX_TRADES,
            "current": total_linked_trades,
        }

    # --- Group links by insight_id ---
    from collections import defaultdict
    trades_by_insight: dict[int, list[dict]] = defaultdict(list)
    for row in link_rows:
        insight_id, trade_id = row[0], row[1]
        if trade_id is None:
            # LEFT JOIN produced no match — insight has no linked trades
            continue
        trade_dict = {
            "id": trade_id,
            "log_date": str(row[3]) if row[3] is not None else None,
            "asset_id": row[4],
            "action": row[5],
            "verdict": row[6],
            "outcome_pct": float(row[7]) if row[7] is not None else None,
            "verification_result": row[8],
            "verification_status": row[9],
            "verification_date": str(row[10]) if row[10] is not None else None,
        }
        trades_by_insight[insight_id].append(trade_dict)

    # --- Build per-insight list + aggregate ---
    VERDICT_COUNTS_KEYS = ("good_call", "regret", "bullet_dodged", "missed_opportunity")
    insights_out: list[dict] = []
    total_verified = 0
    total_good_calls = 0
    total_regrets = 0
    total_adopted = 0
    total_rejected = 0
    total_pending_adoption = 0

    for row in insight_rows:
        iid, insight_date, content, ai_model, adopted, category = row
        linked = trades_by_insight.get(iid, [])

        # Per-insight aggregation (only over verified trades)
        counts: dict[str, int] = {k: 0 for k in VERDICT_COUNTS_KEYS}
        outcome_pcts: list[float] = []
        pending_count = 0
        for t in linked:
            if t["verdict"] is not None:
                v = t["verdict"]
                if v in counts:
                    counts[v] += 1
                if t["outcome_pct"] is not None:
                    outcome_pcts.append(t["outcome_pct"])
            else:
                pending_count += 1

        avg_outcome = (sum(outcome_pcts) / len(outcome_pcts)) if outcome_pcts else None
        total_verified += sum(counts.values())
        total_good_calls += counts["good_call"]
        total_regrets += counts["regret"]

        if adopted is True:
            total_adopted += 1
        elif adopted is False:
            total_rejected += 1
        else:
            total_pending_adoption += 1

        insights_out.append({
            "id": iid,
            "insight_date": str(insight_date),
            "content": content,
            "ai_model": ai_model,
            "adopted": adopted,
            "category": category,
            "linked_trades": linked,
            "summary": {
                "good_calls": counts["good_call"],
                "regrets": counts["regret"],
                "bullet_dodged": counts["bullet_dodged"],
                "missed": counts["missed_opportunity"],
                "pending": pending_count,
                "avg_outcome_pct": avg_outcome,
            },
        })

    # Include all trades with verdicts in the period so the LLM has complete
    # outcome data even when the link table doesn't have entries for every insight.
    try:
        trade_verdict_rows = db.execute(
            """
            SELECT id, log_date, asset_id, action, verdict, outcome_pct,
                   suggestion_source, verification_status, verification_result
            FROM trade_logs
            WHERE log_date BETWEEN ? AND ?
              AND verdict IS NOT NULL
            ORDER BY log_date
            """,
            [period_start_str, period_end_str],
        ).fetchall()
        trade_verdicts = [
            {
                "id": r[0],
                "log_date": str(r[1]),
                "asset_id": r[2],
                "action": r[3],
                "verdict": r[4],
                "outcome_pct": float(r[5]) if r[5] is not None else None,
                "suggestion_source": r[6],
                "verification_status": r[7],
                "verification_result": r[8],
            }
            for r in trade_verdict_rows
        ]
    except Exception:
        trade_verdicts = []

    return {
        "period": {
            "start": period_start_str,
            "end": period_end_str,
            "days": current_days,
        },
        "insights": insights_out,
        "trade_verdicts": trade_verdicts,
        "summary": {
            "total_insights": len(insights_out),
            "adopted": total_adopted,
            "rejected": total_rejected,
            "pending": total_pending_adoption,
            "total_linked_trades": total_linked_trades,
            "total_verified": total_verified,
            "good_calls": total_good_calls,
            "regrets": total_regrets,
        },
    }
