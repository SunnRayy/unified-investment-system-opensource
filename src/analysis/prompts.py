"""Analysis-specific LLM system prompt for single-asset portfolio-contextualized analysis."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from src.services.ai_advisor.prompts import (
    _DEFAULT_SHARED_PERSONA_EDITABLE,
    _SHARED_PERSONA_GUARDRAILS,
    _JSON_OUTPUT_RULES,
)

_ANALYSIS_INSTRUCTIONS_US = """当前任务是对单个美股/美ETF资产进行价值投资视角的分析。

分析框架（格雷厄姆/霍华德·马克斯）：
- 首先评估估值：当前远期PE（Fwd PE）处于历史几分位？是便宜、合理还是贵？
- 对于ETF，参考其跟踪指数的PE/PB分位（如S&P500、Nasdaq100）
- 安全边际优先：只有估值合理或偏低时才考虑加仓，估值偏高时减仓
- 技术指标仅供参考，不得作为唯一买卖依据
- 必须明确证伪条件：什么情况下此判断会失效？
- portfolio_alignment必须引用提供的持仓比例和策略备忘内容
- 注意美元计价资产的汇率风险（USD/CNY波动对CNY市值的影响）

rule_bucket说明（只能选其一）：
- 机械执行：合规型操作，如定期定额、税损收割、再平衡指令
- 价值估算：基于Fwd PE/历史分位的主动判断
- 不适用：无可用估值数据，无法做出有估值依据的判断

注意事项：
- 若context缺少估值数据，valuation_judgment填"无估值数据"，rule_bucket填"不适用"
- operation_signal与sizing_suggestion必须方向一致
- 市值以CNY计价，但需注明原始美元计价"""

_ANALYSIS_INSTRUCTIONS_CN_HK = """当前任务是对单个A股指数基金/港股ETF资产进行价值投资视角的分析。

分析框架（格雷厄姆/霍华德·马克斯）：
- 首先评估估值：当前PE-TTM和PB处于历史几分位？是便宜、合理还是贵？
- A股指数：关注PE-TTM分位（通常剔除亏损股）；港股关注PB分位
- 安全边际优先：只有估值合理或偏低时才考虑加仓，估值偏高时减仓
- 技术指标仅供参考，不得作为唯一买卖依据
- 必须明确证伪条件：什么情况下此判断会失效？
- portfolio_alignment必须引用提供的持仓比例和策略备忘内容
- 注意政策风险、流动性风险等A股/港股特有因素

rule_bucket说明（只能选其一）：
- 机械执行：合规型操作，如定期定额、税损收割、再平衡指令
- 价值估算：基于PE-TTM/PB/历史分位的主动判断
- 不适用：无可用估值数据，无法做出有估值依据的判断

注意事项：
- 若context缺少估值数据，valuation_judgment填"无估值数据"，rule_bucket填"不适用"
- operation_signal与sizing_suggestion必须方向一致
- 对CNY计价的持仓数据，请在分析中标注货币单位"""

_US_ASSET_CLASSES = frozenset({"US_STOCK", "US_ETF", "US_BOND_ETF", "US_INDEX"})

ANALYSIS_JSON_SCHEMA = """{
  "summary": "2-3句执行摘要，核心判断和行动方向",
  "valuation_judgment": "当前估值信号解读：便宜/合理/贵，历史分位，为什么",
  "rule_bucket": "机械执行 | 价值估算 | 不适用",
  "operation_signal": "buy | hold | sell | trim | wait",
  "falsification_conditions": ["证伪条件1：若X发生，此判断失效", "证伪条件2"],
  "validity_period": "此判断有效期，如：3-6个月，直到下季度财报",
  "confidence": 0.0,
  "sizing_suggestion": "具体仓位操作建议（金额或比例）",
  "risk_factors": ["风险因素1", "风险因素2"],
  "portfolio_alignment": "与当前持仓和策略备忘的一致性说明"
}"""

_ANALYSIS_JSON_RULES = """输出要求：
- 必须返回合法JSON，不要输出JSON之外的任何内容。
- 必须包含全部10个key：summary, valuation_judgment, rule_bucket, operation_signal, falsification_conditions, validity_period, confidence, sizing_suggestion, risk_factors, portfolio_alignment。
- operation_signal必须是以下之一：buy, hold, sell, trim, wait。
- rule_bucket必须是以下之一：机械执行, 价值估算, 不适用。
- confidence必须是0.0到1.0之间的浮点数。
- falsification_conditions必须是字符串列表（可以为空列表[]）。
- risk_factors必须是字符串列表。"""


def get_analysis_system_prompt(asset_class: str | None = None) -> str:
    """Returns system prompt for single-asset analysis, tailored by asset class."""
    try:
        import yaml

        config_path = Path(__file__).parents[2] / "config" / "settings.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        persona = cfg.get("prompts", {}).get("shared_persona", {}).get("text", _DEFAULT_SHARED_PERSONA_EDITABLE)
    except Exception:
        persona = _DEFAULT_SHARED_PERSONA_EDITABLE

    instructions = _ANALYSIS_INSTRUCTIONS_US if asset_class in _US_ASSET_CLASSES else _ANALYSIS_INSTRUCTIONS_CN_HK

    return (
        f"{persona}\n\n"
        f"{_SHARED_PERSONA_GUARDRAILS}\n\n"
        f"{instructions}\n\n"
        f"JSON输出格式：\n{ANALYSIS_JSON_SCHEMA}\n\n"
        f"{_ANALYSIS_JSON_RULES}\n\n"
        f"{_JSON_OUTPUT_RULES}"
    )
