"""Cash flow trend analysis from income/expense monthly data.

Reads from the income_expense_monthly table, parses JSON payloads,
and computes monthly totals and trend statistics.
"""
import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def parse_monthly_cash_flows(rows: List[Tuple], mapping=None) -> List[Dict[str, Any]]:
    """Parse income_expense_monthly rows into monthly summaries.

    Income and expense are DERIVED from the 月度收支 leaf columns via the
    `ie_column` role mapping (src/services/ie_ledger.py), never read from the
    Excel's own `总收入合计` / `总支出` aggregate columns — owner ruling
    2026-08-01 (所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用). The derived
    figures reproduce those aggregates exactly on live data, and keep doing so
    when the owner inserts a column his SUM range does not reach.

    `total_expense` is `LedgerTotals.total_outflow` = expense leaves PLUS
    investment leaves, because the Excel's 总支出 includes 理财 (verified
    2026-07: 36,149.00 + 535.00 + 0.00 + 37,222.35 = 73,906.35 = 总支出).
    A bare Σ(role='expense') would silently drop investment out of every
    expense/net figure this function has ever produced.

    Args:
        rows: List of (record_key, transaction_date, payload_json) tuples
        mapping: optional pre-loaded ie_column mapping
            (`ie_ledger.load_ie_column_mapping(db)`, so owner overrides apply).
            Defaults to the code-default mapping — this function takes rows,
            not a connection, and must not grow a DB round-trip.

    Returns:
        List of monthly dicts with month, total_income, total_expense, net,
        sorted chronologically
    """
    from src.services.ie_ledger import (  # noqa: PLC0415 — lazy: avoids a services import at module scope
        default_ie_column_mapping,
        payload_dict,
        role_totals,
    )

    if mapping is None:
        mapping = default_ie_column_mapping()

    monthly: Dict[str, Dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})

    for record_key, tx_date, payload_json in rows:
        if isinstance(tx_date, date):
            month_key = tx_date.strftime("%Y-%m")
        else:
            month_key = str(tx_date)[:7]

        payload = payload_dict(payload_json)
        if not payload:
            continue

        totals = role_totals(payload, mapping)
        income = totals.gross_income
        expense = totals.total_outflow

        # Narrow-format fallback (amount/type rows) — unchanged. Only reached
        # when the wide-format 月度收支 columns produced nothing at all.
        if income == 0 and expense == 0:
            if "amount" in payload and "type" in payload:
                amount = float(payload.get("amount", 0.0))
                flow_type = str(payload.get("type", "")).lower()
                if flow_type == "income":
                    income = amount
                elif flow_type == "expense":
                    expense = amount

        # 3. Aggregate
        monthly[month_key]["income"] += income
        monthly[month_key]["expense"] += expense

    result = []
    for month_key in sorted(monthly.keys()):
        inc = monthly[month_key]["income"]
        exp = monthly[month_key]["expense"]
        result.append({
            "month": month_key,
            "total_income": round(inc, 2),
            "total_expense": round(exp, 2),
            "net": round(inc - exp, 2),
        })

    return result


def calculate_trends(monthly: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate trend statistics from monthly cash flow data.

    Args:
        monthly: List of monthly summaries from parse_monthly_cash_flows

    Returns:
        Dict with avg_income, avg_expense, avg_net, savings_rate,
        income_trend, expense_trend
    """
    if not monthly:
        return {
            "avg_income": 0, "avg_expense": 0, "avg_net": 0,
            "savings_rate": 0, "months_analyzed": 0,
        }

    incomes = [m["total_income"] for m in monthly]
    expenses = [m["total_expense"] for m in monthly]
    nets = [m["net"] for m in monthly]

    avg_income = sum(incomes) / len(incomes)
    avg_expense = sum(expenses) / len(expenses)
    avg_net = sum(nets) / len(nets)
    savings_rate = (avg_net / avg_income * 100) if avg_income > 0 else 0

    return {
        "avg_income": round(avg_income, 2),
        "avg_expense": round(avg_expense, 2),
        "avg_net": round(avg_net, 2),
        "savings_rate": round(savings_rate, 1),
        "months_analyzed": len(monthly),
        "latest_month": monthly[-1]["month"] if monthly else None,
        "latest_income": monthly[-1]["total_income"] if monthly else 0,
        "latest_expense": monthly[-1]["total_expense"] if monthly else 0,
    }


def get_cash_flow_analysis(db: Any) -> Dict[str, Any]:
    """Fetch income/expense data from DB and compute analysis.

    Args:
        db: DatabaseConnector or mock

    Returns:
        Dict with monthly (list) and trends (summary)
    """
    try:
        since = (date.today() - timedelta(days=36 * 31)).isoformat()
        rows = db.execute("""
            SELECT record_key, transaction_date, payload
            FROM income_expense_monthly
            WHERE transaction_date >= ?
            ORDER BY transaction_date ASC
        """, [since]).fetchall()

        # Pass the DB-merged mapping so the owner's UI overrides apply (the
        # parse function itself has no connection and falls back to defaults).
        from src.services.ie_ledger import load_ie_column_mapping  # noqa: PLC0415 — lazy

        monthly = parse_monthly_cash_flows(rows, mapping=load_ie_column_mapping(db))
        trends = calculate_trends(monthly)

        return {"monthly": monthly, "trends": trends}
    except Exception as e:
        logger.error(f"Error in cash flow analysis: {e}")
        return {"monthly": [], "trends": calculate_trends([])}
