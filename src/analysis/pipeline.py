"""AssetAnalysisPipeline: fetch -> analyze -> enrich -> LLM -> persist."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import duckdb

from src.analysis.models import TechnicalSignals
from src.analysis.prompts import get_analysis_system_prompt
from src.analysis.technical import StockTrendAnalyzer
from src.database.connector import DatabaseConnector, resolve_db_path
from src.market_data.service import MarketDataService
from src.services.ai_advisor.context_builder import ContextBuilder
from src.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

TriggerSource = Literal["user", "brief", "review", "memo"]

REQUIRED_LLM_KEYS = {
    "summary",
    "valuation_judgment",
    "rule_bucket",
    "operation_signal",
    "falsification_conditions",
    "validity_period",
    "confidence",
    "sizing_suggestion",
    "risk_factors",
    "portfolio_alignment",
}
VALID_OPERATION_SIGNALS = {"buy", "hold", "sell", "trim", "wait"}


@dataclass
class AnalysisResult:
    id: Optional[int]
    asset_code: str
    asset_name: Optional[str]
    technical_signals: dict
    llm_analysis: dict
    llm_analysis_markdown: str
    portfolio_context: dict
    model_used: str
    data_source: str
    triggered_by: str
    created_at: str
    usage: dict = field(default_factory=dict)


class AssetAnalysisPipeline:
    """End-to-end single-asset analysis pipeline."""

    def analyze(
        self,
        asset_code: str,
        triggered_by: TriggerSource = "user",
        db_path: str = "data/unified.duckdb",
        days: int = 60,
    ) -> AnalysisResult:
        resolved_db = resolve_db_path(db_path)

        # Step 1: Fetch OHLCV
        market_svc = MarketDataService()
        df = market_svc.get_ohlcv(asset_code, days)

        # Step 2: Technical analysis
        analyzer = StockTrendAnalyzer()
        signals = analyzer.analyze(df, asset_code)

        # Step 3: Portfolio context
        # Always use read_only=True and always close before the LLM call.
        # A writable snapshot held open for 30-90s during the LLM phase causes WAL
        # accumulation that slows reads on other endpoints (the "messing with other
        # reports" symptom in GitHub Issue #5).
        cb = ContextBuilder()
        try:
            # Close whatever connection ContextBuilder.__init__ opened (writable default)
            # and replace with a scoped read-only connection for the context query only.
            existing_db = getattr(cb, "_db", None)
            if existing_db is not None:
                try:
                    existing_db.close()
                except Exception:
                    pass
            # read_only=True is not supported for :memory: databases (test fixtures).
            # Production file-backed DBs always use read_only=True here.
            cb._db = DatabaseConnector(resolved_db, read_only=(resolved_db != ":memory:"))
            portfolio_ctx = cb.build_asset_context(asset_code)
        finally:
            # Always close before the LLM call — never hold a DB connection across it.
            try:
                cb._db.close()
            except Exception:
                pass

        # Step 4: Fetch valuation snapshot + build user prompt
        valuation_snap = _fetch_valuation_snapshot(asset_code, resolved_db)
        user_prompt = _build_analysis_user_prompt(signals, portfolio_ctx, asset_code, valuation_snap)

        # Step 5: LLM call with one retry
        client = LLMClient()
        system_prompt = get_analysis_system_prompt(asset_class=valuation_snap.get("asset_class") if valuation_snap else None)
        response = client.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expect_json=True,
            report_type="analysis",
        )

        llm_json = _normalize_llm_analysis(response.content_json or {})

        # Retry once if required keys are missing
        if _missing_analysis_keys(llm_json):
            missing = _missing_analysis_keys(llm_json)
            retry_prompt = _build_analysis_retry_prompt(user_prompt, response.content, missing)
            retry_response = client.complete(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                expect_json=True,
                report_type="analysis",
            )
            retry_json = _normalize_llm_analysis(retry_response.content_json or {})
            if len(_missing_analysis_keys(retry_json)) < len(missing):
                response = retry_response
                llm_json = retry_json

        # Step 6: Markdown
        llm_markdown = _build_analysis_markdown(llm_json, signals, portfolio_ctx)

        # Step 7: Extract data_source from DataFrame
        data_source = "unknown"
        if "source" in df.columns and len(df) > 0:
            first_source = df["source"].dropna().iloc[0] if df["source"].notna().any() else "unknown"
            data_source = str(first_source)

        # Enrich portfolio_ctx with current valuation signal for future trigger checks.
        # This runs AFTER _build_analysis_user_prompt so the LLM does not receive it —
        # it is stored in the DB for should_trigger_analysis() to detect signal changes.
        if valuation_snap and valuation_snap.get("valuation_signal"):
            portfolio_ctx = dict(portfolio_ctx)
            portfolio_ctx["valuation_signal"] = valuation_snap["valuation_signal"]

        # Step 8: Persist
        record_id = _save_analysis_to_db(
            asset_code=asset_code,
            asset_name=portfolio_ctx.get("asset_name"),
            signals_dict=signals.to_dict(),
            llm_json=llm_json,
            llm_markdown=llm_markdown,
            portfolio_ctx=portfolio_ctx,
            model_used=response.model_used,
            data_source=data_source,
            triggered_by=triggered_by,
            db_path=resolved_db,
        )

        return AnalysisResult(
            id=record_id,
            asset_code=asset_code,
            asset_name=portfolio_ctx.get("asset_name"),
            technical_signals=signals.to_dict(),
            llm_analysis=llm_json,
            llm_analysis_markdown=llm_markdown,
            portfolio_context=portfolio_ctx,
            model_used=response.model_used,
            data_source=data_source,
            triggered_by=triggered_by,
            created_at=datetime.now().isoformat(),
            usage=response.usage or {},
        )


def _fetch_valuation_snapshot(asset_code: str, db_path: str) -> dict | None:
    """Fetch latest valuation snapshot for the given asset, matching ticker or asset_id."""
    try:
        conn = duckdb.connect(db_path, read_only=True)
        try:
            row = conn.execute(
                """
                SELECT ticker, display_name, asset_class, valuation_signal,
                       pe_ttm, pe_forward, pb_ratio, pe_ttm_pct, pe_fwd_pct, pb_pct,
                       sec_yield, signal_basis, pct_years, snapshot_date
                FROM valuation_snapshots
                WHERE (UPPER(TRIM(ticker)) = UPPER(TRIM(?))
                       OR UPPER(TRIM(asset_id)) = UPPER(TRIM(?)))
                  AND is_estimable = TRUE
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                [asset_code, asset_code],
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {
            "ticker": row[0], "display_name": row[1], "asset_class": row[2],
            "valuation_signal": row[3], "pe_ttm": row[4], "pe_forward": row[5],
            "pb_ratio": row[6], "pe_ttm_pct": row[7], "pe_fwd_pct": row[8],
            "pb_pct": row[9], "sec_yield": row[10], "signal_basis": row[11],
            "pct_years": row[12], "snapshot_date": str(row[13]),
        }
    except Exception as e:
        logger.debug("_fetch_valuation_snapshot failed for %s: %s", asset_code, e)
        return None


def _build_analysis_user_prompt(
    signals: TechnicalSignals,
    portfolio_ctx: dict,
    asset_code: str,
    valuation_snap: dict | None = None,
) -> str:
    """Render user prompt leading with valuation data, then portfolio context, then technical reference."""
    lines = ["## 单资产价值投资分析请求"]

    asset_name = portfolio_ctx.get("asset_name") or asset_code
    lines.append(f"\n**资产**: {asset_code} ({asset_name})")

    # --- VALUATION DATA (primary decision input) ---
    lines.append("\n### 估值数据 (主要决策依据)")
    if valuation_snap:
        cls = valuation_snap.get("asset_class", "")
        signal = valuation_snap.get("valuation_signal") or "N/A"
        basis = valuation_snap.get("signal_basis") or ""
        snap_date = valuation_snap.get("snapshot_date", "")
        pct_years = valuation_snap.get("pct_years")

        if cls in ("US_STOCK", "US_ETF"):
            pe_fwd = valuation_snap.get("pe_forward")
            pe_fwd_pct = valuation_snap.get("pe_fwd_pct")
            val_str = f"Fwd PE {pe_fwd:.1f}x" if pe_fwd else "N/A"
            pct_str = f" | 历史{pct_years}年 {pe_fwd_pct:.0f}th分位" if pe_fwd_pct and pct_years else ""
        elif cls == "US_BOND_ETF":
            sec_yld = valuation_snap.get("sec_yield")
            val_str = f"收益率 {sec_yld:.2f}%" if sec_yld else "N/A"
            pct_str = ""
        else:
            pe_ttm = valuation_snap.get("pe_ttm")
            pe_ttm_pct = valuation_snap.get("pe_ttm_pct")
            pb = valuation_snap.get("pb_ratio")
            pb_pct = valuation_snap.get("pb_pct")
            val_str = f"PE-TTM {pe_ttm:.1f}x" if pe_ttm else "N/A"
            if pb:
                val_str += f" | PB {pb:.2f}x"
            pct_str = f" | 历史{pct_years}年 {pe_ttm_pct:.0f}th分位" if pe_ttm_pct and pct_years else ""
            if pb_pct and not pe_ttm_pct:
                pct_str = f" | PB历史{pct_years}年 {pb_pct:.0f}th分位" if pct_years else ""

        lines.append(f"- 估值信号: **{signal}**{pct_str}")
        lines.append(f"- 当前估值: {val_str}")
        if basis:
            lines.append(f"- 信号依据: {basis}")
        lines.append(f"- 数据日期: {snap_date}")
    else:
        lines.append('- 无可用估值数据（valuation_judgment填"无估值数据"，rule_bucket填"不适用"）')

    # --- POSITION ---
    position = portfolio_ctx.get("position")
    allocation = portfolio_ctx.get("allocation")
    if position:
        qty = float(position.get("quantity", 0) or 0)
        cost = float(position.get("cost_price_unit", 0) or 0)
        mv = float(position.get("market_value_cny", 0) or 0)
        price = float(position.get("market_price_unit", 0) or 0)
        lines.append("\n### 当前持仓 (市值单位: CNY)")
        lines.append(f"- 持有数量: {qty:,.0f} 份/股")
        lines.append(f"- 成本价: {cost:,.2f} | 现价: {price:,.2f} | 市值: ¥{mv:,.0f}")
        if allocation:
            cur_pct = allocation.get("current_pct")
            tgt_pct = allocation.get("target_pct")
            if cur_pct is not None:
                pct_str = f"占组合 {float(cur_pct):.1f}%"
                if tgt_pct is not None:
                    drift = float(cur_pct) - float(tgt_pct)
                    pct_str += f" (目标 {float(tgt_pct):.1f}%, 偏差 {drift:+.1f}%)"
                lines.append(f"- 配置比例: {pct_str}")
    else:
        lines.append("\n### 持仓状态")
        lines.append("- 当前组合中无该资产持仓")

    # --- RECENT TRADES ---
    recent_trades = portfolio_ctx.get("recent_trades", [])
    if recent_trades:
        lines.append("\n### 近期交易记录 (最近5笔)")
        for t in recent_trades[:5]:
            d = t.get("date", "")
            act = t.get("action", "")
            p = float(t.get("price", 0) or 0)
            q = float(t.get("quantity", 0) or 0)
            amt = float(t.get("amount", 0) or 0)
            lines.append(f"- {d}: {act} {q:,.0f} 份/股 @ {p:,.2f} (金额: {amt:,.0f})")

    # --- STRATEGY MEMOS ---
    related_memos = portfolio_ctx.get("related_memos", [])
    if related_memos:
        lines.append("\n### 相关策略备忘")
        for m in related_memos[:3]:
            d = m.get("date", "")
            title = m.get("title", "")
            content = str(m.get("content", ""))[:300]
            lines.append(f"- {d} [{title}]: {content}...")

    # --- PHILOSOPHY ---
    philosophy = portfolio_ctx.get("philosophy_excerpt", "")
    if philosophy:
        lines.append("\n### 投资哲学摘要")
        lines.append(philosophy[:500])

    # --- TECHNICAL SIGNALS (reference only) ---
    lines.append("\n### 技术指标 (参考信息，非决策依据)")
    lines.append(f"- 综合评分: {signals.to_compact_str()}")
    lines.append(f"- MA5: {signals.ma5} | MA10: {signals.ma10} | MA20: {signals.ma20}")
    lines.append(
        f"- RSI(14): {round(signals.rsi_value, 1) if signals.rsi_value is not None else 'N/A'} | "
        f"MACD hist: {round(signals.macd_hist, 4) if signals.macd_hist is not None else 'N/A'}"
    )
    if signals.support_levels:
        lines.append(f"- 支撑位: {[round(x, 2) for x in signals.support_levels]}")
    if signals.resistance_levels:
        lines.append(f"- 阻力位: {[round(x, 2) for x in signals.resistance_levels]}")

    lines.append("\n请基于以上估值数据和持仓状态，给出价值投资视角的分析。技术指标仅供参考。")
    return "\n".join(lines)


def _normalize_llm_analysis(raw: dict) -> dict:
    """Ensure required keys present, normalize types."""
    result = dict(raw) if isinstance(raw, dict) else {}

    # Backward compat: lift old timing_signal into operation_signal if missing
    if "operation_signal" not in result and "timing_signal" in result:
        result["operation_signal"] = result["timing_signal"]

    defaults = {
        "summary": "分析数据不足，无法生成摘要。",
        "valuation_judgment": "无估值数据。",
        "rule_bucket": "不适用",
        "operation_signal": "wait",
        "falsification_conditions": [],
        "validity_period": "待补充",
        "confidence": 0.5,
        "sizing_suggestion": "建议等待更多数据后决策。",
        "risk_factors": [],
        "portfolio_alignment": "无持仓数据，无法评估组合一致性。",
    }
    for key, value in defaults.items():
        if key not in result:
            result[key] = value

    # Normalize operation_signal
    op_signal = str(result.get("operation_signal", "wait")).lower().strip()
    result["operation_signal"] = op_signal if op_signal in VALID_OPERATION_SIGNALS else "wait"

    # Clamp confidence 0..1
    try:
        confidence = float(result.get("confidence", 0.5))
        result["confidence"] = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        result["confidence"] = 0.5

    # Ensure list fields
    for list_key in ("falsification_conditions", "risk_factors"):
        if not isinstance(result.get(list_key), list):
            val = result.get(list_key)
            result[list_key] = [str(val)] if val else []

    return result


def _missing_analysis_keys(llm_json: dict) -> list[str]:
    return [key for key in REQUIRED_LLM_KEYS if key not in llm_json]


def _build_analysis_retry_prompt(original_prompt: str, partial_response: str, missing_keys: list[str]) -> str:
    missing_str = ", ".join(missing_keys)
    return (
        "你上一次的分析JSON输出缺少必要的key。\n"
        f"缺失的key：{missing_str}\n"
        "请基于相同context重新生成完整JSON，必须包含全部10个key。\n"
        "只返回JSON，不要markdown代码块。\n\n"
        "=== ORIGINAL CONTEXT ===\n"
        f"{original_prompt}\n\n"
        "=== PARTIAL OUTPUT TO REPAIR ===\n"
        f"{partial_response}"
    )


def _build_analysis_markdown(llm_json: dict, signals: TechnicalSignals, portfolio_ctx: dict) -> str:
    """Convert LLM JSON to human-readable markdown."""
    lines = []

    asset_code = portfolio_ctx.get("asset_code", "")
    asset_name = portfolio_ctx.get("asset_name") or asset_code
    lines.append(f"# {asset_code} ({asset_name}) 价值投资分析")
    lines.append("")

    lines.append(f"## 执行摘要\n{llm_json.get('summary', '')}")
    lines.append("")

    op_signal = llm_json.get("operation_signal", "wait").upper()
    rule_bucket = llm_json.get("rule_bucket", "不适用")
    confidence = float(llm_json.get("confidence", 0.5) or 0.5)
    validity = llm_json.get("validity_period", "")
    lines.append(f"## 操作信号\n**{op_signal}** · {rule_bucket} · 置信度 {confidence:.0%}")
    if validity:
        lines.append(f"**有效期**: {validity}")
    lines.append("")

    lines.append(f"## 估值判断\n{llm_json.get('valuation_judgment', '')}")
    lines.append("")

    falsification = llm_json.get("falsification_conditions", [])
    if falsification:
        lines.append("## 证伪条件")
        for cond in falsification:
            lines.append(f"- {cond}")
        lines.append("")

    lines.append(f"## 仓位建议\n{llm_json.get('sizing_suggestion', '')}")
    lines.append("")

    risk_factors = llm_json.get("risk_factors", [])
    if risk_factors:
        lines.append("## 风险因素")
        for risk in risk_factors:
            lines.append(f"- {risk}")
        lines.append("")

    lines.append(f"## 组合一致性\n{llm_json.get('portfolio_alignment', '')}")
    lines.append("")

    lines.append(f"## 技术指标 (参考)\n**信号评分**: {signals.signal_score}/100 · MA5/10/20: {signals.ma5}/{signals.ma10}/{signals.ma20} · RSI: {round(signals.rsi_value, 1) if signals.rsi_value is not None else 'N/A'}")

    return "\n".join(lines).strip()


def _save_analysis_to_db(
    asset_code: str,
    asset_name: Optional[str],
    signals_dict: dict,
    llm_json: dict,
    llm_markdown: str,
    portfolio_ctx: dict,
    model_used: str,
    data_source: str,
    triggered_by: str,
    db_path: str,
) -> Optional[int]:
    """Persist analysis to asset_analyses table. Returns inserted id or None."""
    record_id: Optional[int] = None
    try:
        conn = duckdb.connect(db_path)
        try:
            result = conn.execute(
                """
                INSERT INTO asset_analyses (
                    asset_code, asset_name, analysis_type,
                    technical_signals, llm_analysis, llm_analysis_markdown,
                    portfolio_context, model_used, data_source, triggered_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                [
                    asset_code,
                    asset_name,
                    "full",
                    json.dumps(signals_dict, ensure_ascii=False),
                    json.dumps(llm_json, ensure_ascii=False),
                    llm_markdown,
                    json.dumps(portfolio_ctx, ensure_ascii=False, default=str),
                    model_used,
                    data_source,
                    triggered_by,
                ],
            ).fetchone()
            record_id = result[0] if result else None
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to save analysis to asset_analyses: %s", e)
        return None

    if record_id is not None:
        # Notify GCS flush manager so the result is uploaded to Cloud Run's persistent
        # storage. Without mark_dirty(), the background thread's write is invisible to
        # the flush manager — on container restart, GCS (old version) is re-downloaded
        # and the analysis disappears (GitHub Issue #5, "record disappears" symptom).
        try:
            from src.storage.gcs_flush import mark_dirty
            mark_dirty()
        except Exception:
            pass

    return record_id
