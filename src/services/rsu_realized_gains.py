"""RSU realized-gain monthly report (plan 2026-08-01-ie-column-mapping-and-ibkr-amounts §WS-C).

READ-ONLY service — SELECT only, no writes to any table anywhere in this
module (the single DB access is `replay_rsu_lots()`, itself SELECT-only).

WHY GAIN AND NOT GROSS PROCEEDS
-------------------------------
The owner's 月度收支 ledger books an RSU vest as INCOME in
`收入_主动收入_RSU*` at the VEST price — including the shares he retains.
When he later sells those retained shares and repatriates the cash, the
principal has therefore ALREADY been counted as income. Only the
appreciation above the vest price is new, previously-unrecorded income.
This module produces exactly that number for the new
`收入_被动收入_股票卖出收益` column. Reporting gross proceeds instead
would double-count (plan §1; ADR-025 §4b double-subtract warning).

The vest-price cost basis is the established Huinsight convention for RSU —
an intentional divergence from PIS, which uses 0 (CLAUDE.md "Known
Critical Edge Cases"). Do not "fix" it to 0.

SPECIFIC-LOT MATCHING (owner decision, 2026-08-01)
--------------------------------------------------
A sale of the batch that just vested is matched against THAT vest's lot,
not the FIFO head — 「卖的就是刚归属的那批」. This is the project's ONE lot
rule as of 2026-08-01, so this module simply calls the shared replay with
no options; the rule and its 4-calendar-day window are documented on
`rsu_contributions.replay_rsu_lots()`.

One rule covers both real shapes, and both are gain ~zero because the sale
price IS the lot's vest price — neither is appreciation above the price the
ledger already booked as income:

  * PARTIAL match = mandatory sell-to-cover tax withholding (the four AMZN
    vests + the GOOG one). Those shares never reached the owner's account,
    so they are excluded from the reported sale rows and surfaced separately
    as `sell_to_cover_shares`.
  * FULL match = the batch itself being liquidated, e.g. 2026-03-16 selling
    the entire 2026-03-15 vest with ~$5,651 of tax withheld inside the single
    row (that vest has no separate withholding row). This IS a disposal the
    owner made, so it is reported as a normal sale month — with a gain of
    0.00 rather than the -$1,510.73 that FIFO produced by reaching past it
    into the older $232 lot.

Any quantity beyond the matched vest lot falls through to ordinary FIFO and
carries its real gain — the flags are per-MATCH, never per-sale.

`rsu_contributions.rsu_retained_ttm()` now uses the same rule (owner
decision 2026-08-01). It previously stayed on strict FIFO while this module
matched specific lots, and that split was the defect: strict FIFO reported
the surviving 106.8 AMZN shares as the 2026-03-15 vest — the very batch the
owner had ruled was liquidated on 2026-03-16. One batch, two answers.

FX
--
`gain_usd` is the authoritative figure — RSU sales settle in USD and the
owner applies his OWN per-entry rate in Excel (implied rates in the ledger
range 6.83–7.30). `gain_cny` is INDICATIVE ONLY: it applies a single rate
to every month, and unless the caller passes `fx_rate=` explicitly that
rate is today's spot from `get_today_usd_cny_rate()` — not the rate on the
sale date. Every returned payload carries `fx_rate`, `fx_rate_source` and
`fx_rate_is_fallback` so no consumer can mistake an indicative CNY figure
for an authoritative one. This is the project-wide FX limitation recorded
in `docs/known-issues.md` §fx-constant (no per-date rate history exists);
the hard-coded 7.0 is only reached when both live fetchers fail, and is
labelled as such rather than silently presented as authoritative.
"""
from __future__ import annotations

import logging
from typing import Optional

from src.services.currency import get_today_usd_cny_rate
from src.services.rsu_contributions import (
    _LOT_TOL,
    _amount_to_cny,
    _month_str,
    replay_rsu_lots,
)

logger = logging.getLogger(__name__)

# The rate `get_today_usd_cny_rate()` returns when every live fetcher failed
# (CurrencyConverterService.fallback_rates / config `currency.fallback_rates`).
_FX_FALLBACK_RATE = 7.0


def _fx_source(fx_rate: float, explicit: bool) -> tuple[str, bool]:
    """Describe where `fx_rate` came from. Returns (label, is_fallback)."""
    if explicit:
        return "caller-supplied (--fx-rate)", False
    if abs(fx_rate - _FX_FALLBACK_RATE) < 1e-9:
        return (
            "hard-coded fallback 7.0 — live FX fetch failed or config default "
            "(docs/known-issues.md §fx-constant); NOT an authoritative rate",
            True,
        )
    return (
        "today's spot via CurrencyConverterService (yfinance -> Google Finance); "
        "applied to ALL months — not the rate on the sale date",
        False,
    )


def rsu_realized_gains_by_month(
    db,
    *,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    fx_rate: Optional[float] = None,
) -> dict:
    """Per-month realized gain on discretionary RSU share sales.

    `Σ (sale_price − vest_price) × qty`, FIFO across vest lots, full-history
    replay (a lot vested years ago may be the one a sale consumes, so the
    window may never be used to scope the replay itself — only its output).

    Read-only: delegates every DB read to
    `rsu_contributions.replay_rsu_lots()` (SELECT only).

    Args:
        start_month / end_month: inclusive 'YYYY-MM' bounds on the SALE date.
            None means unbounded on that side.
        fx_rate: explicit USD->CNY rate. None uses today's spot (see module
            docstring). Always echoed back in the payload.

    Returns:
        {
          "months": [                       # ascending by month
            {"month": "YYYY-MM",
             "gain_usd": float, "gain_cny": float,
             "proceeds_usd": float,         # discretionary sales only
             "cost_basis_usd": float,       # vest-price basis of matched lots
             "shares_sold": float,          # discretionary shares
             "sell_to_cover_shares": float, # withholding, excluded from gain
             "unmatched_shares": float,     # over-sold, gain UNKNOWN not zero
             "by_asset": {asset_id: {"gain_usd", "gain_cny", "shares_sold"}},
             "sales": [                     # explainability detail
               {"date": "YYYY-MM-DD", "asset_id": str, "quantity": float,
                "price_unit": float, "gain_usd": float,
                "lots": [{"vest_date": "YYYY-MM-DD", "qty": float,
                          "price_unit": float, "gain_usd": float}, ...]},
             ]},
            ...
          ],
          "total_gain_usd": float, "total_gain_cny": float,
          "total_proceeds_usd": float, "total_shares_sold": float,
          "fx_rate": float, "fx_rate_source": str, "fx_rate_is_fallback": bool,
          "oversold_shares": float,   # full-history data-health signal
          "window": {"start_month": str|None, "end_month": str|None},
        }

    Empty / no-data is a first-class result: `months` is `[]` and every total
    is 0.0 — never an exception, never a fabricated row (Rule 12).

    `unmatched_shares` > 0 means the FIFO replay ran out of open lots for a
    sale (a data error, e.g. a mis-dated transaction). Those shares produce
    NO gain event — the month's gain is UNDERSTATED, not zero-basis inflated.
    A caller must surface this, never silently drop it.
    """
    explicit_fx = fx_rate is not None
    rate = float(fx_rate) if explicit_fx else get_today_usd_cny_rate()
    fx_label, fx_is_fallback = _fx_source(rate, explicit_fx)

    months: dict[str, dict] = {}

    def _month_bucket(month: str) -> dict:
        return months.setdefault(month, {
            "month": month,
            "gain_usd": 0.0,
            "gain_cny": 0.0,
            "proceeds_usd": 0.0,
            "cost_basis_usd": 0.0,
            "shares_sold": 0.0,
            "sell_to_cover_shares": 0.0,
            "unmatched_shares": 0.0,
            "by_asset": {},
            "_sales": {},
        })

    def on_match(sale, lot, qty) -> None:
        if qty <= _LOT_TOL:
            return
        month = _month_str(sale["date"])
        bucket = _month_bucket(month)

        if sale["is_sell_to_cover"]:
            # Mandatory withholding (a PARTIAL vest-lot match) — those shares
            # never reached the owner's account. Gain is zero by construction;
            # counted separately, never summed into a sale row. A FULL vest-lot
            # match is a real disposal and deliberately falls through below,
            # so its month is reported (at ~0.00 gain).
            bucket["sell_to_cover_shares"] += qty
            return

        if lot is None:
            # Over-sold excess: no cost basis exists, so the gain is UNKNOWN.
            # Deliberately not treated as (sale_price - 0) * qty.
            bucket["unmatched_shares"] += qty
            return

        currency = sale["currency"] or lot["currency"]
        is_usd = (currency or "CNY").upper() == "USD"
        gain_native = qty * (sale["price_unit"] - lot["price_unit"])
        gain_cny = _amount_to_cny(gain_native, currency, rate)
        gain_usd = gain_native if is_usd else 0.0

        bucket["gain_usd"] += gain_usd
        bucket["gain_cny"] += gain_cny
        # Proceeds/basis are reported in USD only — a non-USD RSU row (none
        # exist in production; RSU_Excel is USD-only) contributes to gain_cny
        # and shares_sold but not to these two columns, rather than silently
        # mixing currencies in one total (Rule 2).
        if is_usd:
            bucket["proceeds_usd"] += qty * sale["price_unit"]
            bucket["cost_basis_usd"] += qty * lot["price_unit"]
        bucket["shares_sold"] += qty

        asset = bucket["by_asset"].setdefault(
            sale["asset_id"], {"gain_usd": 0.0, "gain_cny": 0.0, "shares_sold": 0.0},
        )
        asset["gain_usd"] += gain_usd
        asset["gain_cny"] += gain_cny
        asset["shares_sold"] += qty

        # Explainability detail, keyed so multiple lots roll into one sale row.
        key = (str(sale["date"]), sale["asset_id"], sale["price_unit"], sale["quantity"])
        detail = bucket["_sales"].setdefault(key, {
            "date": str(sale["date"]),
            "asset_id": sale["asset_id"],
            "quantity": sale["quantity"],
            "price_unit": sale["price_unit"],
            "gain_usd": 0.0,
            "lots": [],
        })
        detail["gain_usd"] += gain_usd
        detail["lots"].append({
            "vest_date": str(lot["vest_date"]),
            "qty": round(qty, 6),
            "price_unit": lot["price_unit"],
            "gain_usd": round(gain_usd, 2),
        })

    _surviving, oversold_by_asset = replay_rsu_lots(db, on_match=on_match)

    def _in_window(month: str) -> bool:
        if start_month is not None and month < start_month:
            return False
        if end_month is not None and month > end_month:
            return False
        return True

    in_window_months = sorted(m for m in months if _in_window(m))
    # Sell-to-cover is counted across every in-window month, including months
    # whose ONLY activity was withholding — those months are then dropped from
    # `months` (a row of zeroes is noise in a ledger the owner copies from),
    # but their share count must not vanish with them.
    sell_to_cover_shares = sum(months[m]["sell_to_cover_shares"] for m in in_window_months)

    out_months = []
    for month in in_window_months:
        bucket = months[month]
        if bucket["shares_sold"] <= _LOT_TOL and bucket["unmatched_shares"] <= _LOT_TOL:
            continue  # sell-to-cover only — no discretionary sale to report
        sales = sorted(bucket.pop("_sales").values(), key=lambda s: (s["date"], s["asset_id"]))
        for sale in sales:
            sale["gain_usd"] = round(sale["gain_usd"], 2)
        out_months.append({
            "month": bucket["month"],
            "gain_usd": round(bucket["gain_usd"], 2),
            "gain_cny": round(bucket["gain_cny"], 2),
            "proceeds_usd": round(bucket["proceeds_usd"], 2),
            "cost_basis_usd": round(bucket["cost_basis_usd"], 2),
            "shares_sold": round(bucket["shares_sold"], 4),
            "sell_to_cover_shares": round(bucket["sell_to_cover_shares"], 4),
            "unmatched_shares": round(bucket["unmatched_shares"], 4),
            "by_asset": {
                asset_id: {
                    "gain_usd": round(vals["gain_usd"], 2),
                    "gain_cny": round(vals["gain_cny"], 2),
                    "shares_sold": round(vals["shares_sold"], 4),
                }
                for asset_id, vals in sorted(bucket["by_asset"].items())
            },
            "sales": sales,
        })

    return {
        "months": out_months,
        "total_gain_usd": round(sum(m["gain_usd"] for m in out_months), 2),
        "total_gain_cny": round(sum(m["gain_cny"] for m in out_months), 2),
        "total_proceeds_usd": round(sum(m["proceeds_usd"] for m in out_months), 2),
        "total_cost_basis_usd": round(sum(m["cost_basis_usd"] for m in out_months), 2),
        "total_shares_sold": round(sum(m["shares_sold"] for m in out_months), 4),
        "sell_to_cover_shares": round(sell_to_cover_shares, 4),
        "fx_rate": round(rate, 6),
        "fx_rate_source": fx_label,
        "fx_rate_is_fallback": fx_is_fallback,
        "oversold_shares": round(sum(oversold_by_asset.values()), 4),
        "window": {"start_month": start_month, "end_month": end_month},
    }


def format_report(payload: dict) -> str:
    """Human-readable rendering of `rsu_realized_gains_by_month()`.

    Pure formatting — no DB access, no writes.
    """
    lines: list[str] = []
    lines.append("=== RSU Realized Gains by Month ===")
    lines.append("Gain = (sale price - vest price) x qty.")
    lines.append("Vest-price basis: the retained shares were already booked as income")
    lines.append("at vest (收入_主动收入_RSU*), so only the appreciation is new income.")
    lines.append("Lot matching: a sale of the batch that just vested matches THAT vest's")
    lines.append("lot (卖的就是刚归属的那批, same price within 4 days); everything else FIFO.")
    lines.append("")
    lines.append(f"FX USD->CNY: {payload['fx_rate']}")
    lines.append(f"  source: {payload['fx_rate_source']}")
    if payload["fx_rate_is_fallback"]:
        lines.append("  ⚠️  CNY column is NOT authoritative — pass --fx-rate with your own rate.")
    else:
        lines.append("  CNY column is INDICATIVE — apply your own per-entry rate in Excel.")
    lines.append("")

    if not payload["months"]:
        lines.append("No RSU share sales found in the requested window.")
        return "\n".join(lines)

    header = (
        f"{'Month':<9}{'Shares':>10}{'Proceeds USD':>15}{'Basis USD':>14}"
        f"{'Gain USD':>13}{'Gain CNY':>15}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for m in payload["months"]:
        lines.append(
            f"{m['month']:<9}{m['shares_sold']:>10,.2f}{m['proceeds_usd']:>15,.2f}"
            f"{m['cost_basis_usd']:>14,.2f}{m['gain_usd']:>13,.2f}{m['gain_cny']:>15,.2f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<9}{payload['total_shares_sold']:>10,.2f}"
        f"{payload['total_proceeds_usd']:>15,.2f}{payload['total_cost_basis_usd']:>14,.2f}"
        f"{payload['total_gain_usd']:>13,.2f}{payload['total_gain_cny']:>15,.2f}"
    )
    lines.append("")
    lines.append("Copy the Gain column into 收入_被动收入_股票卖出收益 (CNY) /")
    lines.append("收入_被动收入_股票卖出收益_USD (USD) for the matching month.")

    if any(m["gain_usd"] == 0.0 and m["shares_sold"] > 0 for m in payload["months"]):
        lines.append("")
        lines.append(
            "A 0.00 gain row is a same-batch disposal: the shares were sold at the "
            "price they vested at, so there is no appreciation to book — the "
            "principal was already income in 收入_主动收入_RSU*. Enter 0."
        )

    stc = payload["sell_to_cover_shares"]
    if stc > 0:
        lines.append("")
        lines.append(
            f"Sell-to-cover (tax withholding): {stc:,.2f} shares excluded — a partial "
            "match against its own vest lot, so those shares never reached your "
            "account and carry zero gain by construction."
        )
    unmatched = sum(m["unmatched_shares"] for m in payload["months"])
    if unmatched > 0 or payload["oversold_shares"] > 0:
        lines.append("")
        lines.append(
            f"⚠️  Over-sold shares: {unmatched:,.2f} in window / "
            f"{payload['oversold_shares']:,.2f} full history. These sales had no "
            "open vest lot to match — gain is UNKNOWN, not zero, so the figures "
            "above are UNDERSTATED. Check RSU_transactions.xlsx for a bad date/qty."
        )
    return "\n".join(lines)
