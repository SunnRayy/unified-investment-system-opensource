"""Role-driven summation over the 月度收支 ledger — the ONE implementation.

**Huinsight never reads an Excel-computed aggregate column as a calculation input.**
(Owner ruling 2026-08-01: 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用，Huinsight
应该用自己计算逻辑下的分类汇总保持灵活性和准确性.)

Every total this system reports is derived here, from the LEAF columns of
`income_expense_monthly.payload`, classified by the `ie_column` reader mapping
(`role` / `bucket` / `currency` — see `src.database.mapping_seeds`). The
Excel's own `总收入合计` / `总支出` / `*合计` columns are seeded as
`role='computed'` so they are visible and governed, and they are read ONLY by
`aggregate_divergences()` below, as a cross-check that warns — never as an
input to a number this system reports. The owner asked for that cross-check
explicitly: 「excel 里的计算数字拿来验证 ok，需要确保这点」.

Why the rule matters: reading `总收入合计` made every downstream figure depend
on whether the owner's Excel SUM ranges had auto-expanded over newly inserted
columns and correctly excluded the `_USD` siblings. That is an invisible,
untestable dependency living in a spreadsheet. Deriving from leaves makes the
classification the only thing that can be wrong — and the classification is
data the owner can see and edit (ADR-023), backed by an unmapped-column scan.

Four callers, one implementation (a second one would recreate the
`two-sources-signature-bug` class this whole plan exists to kill):

  - src/services/investment_contributions.py   (contributions + savings rate)
  - src/services/north_star_flows.py           (per-row net for flow tagging)
  - src/financial_analysis/cash_flow.py        (Cash Flow tab monthly series)
  - src/services/north_star_glide.py           (run-rate sanity guard basis)

READ-ONLY: SELECT only, no writes anywhere in this module.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, NamedTuple, Optional

from src.database.mapping_seeds import IEColumn
from src.services.reader_mappings import load_reader_mappings

logger = logging.getLogger(__name__)

IE_READER_KEY = "financial_summary"
IE_MAPPING_KIND = "ie_column"

# Only CNY columns ever reach a total. `_USD` siblings are the same money the
# owner already converted in Excel (ADR-025 §3) — Rule 2 at the ledger layer.
LEDGER_CURRENCY = "CNY"

# Roles that legitimately contribute to no total (so an unknown role can be
# told apart from a deliberately-inert one and warned about).
_NON_SUMMED_ROLES = frozenset({"computed", "reference", "ignored"})

# Money below this is float noise, not a real divergence (values are CNY, and
# the ledger's own cells carry 2dp; the largest pure-float residue observed
# summing 40+ columns is ~1e-9).
AGGREGATE_TOLERANCE = 0.01


class LedgerTotals(NamedTuple):
    """One month's 月度收支 leaf columns, summed by role. All CNY.

    The three composite properties are the figures that used to be read
    straight out of the Excel's own aggregate columns.
    """

    by_bucket: Dict[str, float]   # role='invested', keyed by destination bucket
    invested: float               # Σ role='invested'  (== sum(by_bucket.values()))
    redemption: float             # Σ role='redemption' — money coming back OUT of 投资理财
    pass_through_in: float        # Σ role='pass_through', bucket='inflow'  — 报销 repaid
    pass_through_out: float       # Σ role='pass_through', bucket='outflow' — the fronted spend
    income: float                 # Σ role='income' — the INCOME BASIS (see below)
    expense: float                # Σ role='expense' — the EXPENSE BASIS (see below)

    # `income` and `expense` are the two BASES the rates are built on, and both
    # are deliberately narrower than the Excel's own totals:
    #   - income excludes redemptions (converting an asset to cash is not
    #     earning) and both halves of pass_through.
    #   - expense excludes investment (investing is not spending — the same 理财
    #     trap as 总支出 below) and both halves of pass_through.
    # pass_through is excluded from BOTH bases by construction: the owner fronts
    # a work expense and is repaid, so neither end is real income or real
    # consumption, and dropping only one side would skew a rate in that
    # direction. See ADR-025 Amendment 2026-08-01.

    @property
    def gross_income(self) -> float:
        """The ledger's own 总收入合计, derived: every income-side leaf.

        `收入` in this Excel is "money that arrived", which includes fund
        redemptions (被动收入合计 is entirely redemption columns) and the repaid
        报销. Both are real arrivals and belong in the Excel-equivalent income
        figure the Cash Flow tab has always shown — but neither belongs in the
        `income` BASIS above.
        """
        return self.income + self.redemption + self.pass_through_in

    @property
    def total_outflow(self) -> float:
        """The ledger's own 总支出, derived.

        ⚠️ The Excel's 总支出 INCLUDES 理财 (investment). Verified 2026-07:
        必要支出 36,149.00 + 非必要支出 535.00 + 工作支出 0.00 + 理财 37,222.35
        = 73,906.35 = 总支出. So the Excel-equivalent outflow is
        `expense + invested + pass_through_out`, and a naive Σ(role='expense')
        would silently drop investment out of every "total expense" and "net
        cash flow" figure the Cash Flow tab has ever shown. Use `.expense` when
        you mean consumption; use this when you mean "everything that left the
        current account", which is what 总支出 means.
        """
        return self.expense + self.invested + self.pass_through_out

    @property
    def net(self) -> float:
        """gross_income − total_outflow — the Excel-equivalent monthly net."""
        return self.gross_income - self.total_outflow


def payload_dict(payload_json: Any) -> dict:
    """Parse an income_expense_monthly.payload value into a dict.

    payload may already be a dict (DuckDB JSON column decoded by the driver)
    or a JSON string; anything else (None, malformed JSON, non-dict JSON)
    yields an empty dict rather than raising, so a single bad row doesn't
    break a whole series. This is a parsing guard only — it must not swallow
    real computation errors (those are allowed to propagate).
    """
    try:
        payload = json.loads(payload_json) if isinstance(payload_json, str) else (payload_json or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _num(value: Any) -> float:
    """A payload cell as a float; None or '' -> 0.0.

    Deliberately NOT fail-soft beyond empty cells: a non-numeric value in a
    column the mapping says is money is a real defect — either the owner typed
    text into a numeric column or the column is mis-mapped — and it must
    surface, not silently count as zero. Rule 12.
    """
    if value is None or value == "":
        return 0.0
    return float(value)


def load_ie_column_mapping(db) -> Dict[str, IEColumn]:
    """Merged (code defaults + DB overrides) 月度收支 column mapping, keyed by
    the STRIP-NORMALIZED column name.

    Strip-normalizing both this dict's keys and the payload keys it is looked
    up with is deliberate: the live payload key for the FX-rate column is
    '参考_美元汇率 ' with a trailing space (the Excel header has one), and a
    hand-edited header can gain or lose whitespace at any time. Matching on
    the trimmed name means such a change can never silently drop a column out
    of a total. Read-only — `load_reader_mappings` never writes and returns
    the code defaults unchanged if the table is missing.
    """
    from src.database.mapping_seeds import IE_COLUMN_SEED  # noqa: PLC0415 — cheap, stdlib-only module

    merged = load_reader_mappings(db, IE_READER_KEY, IE_MAPPING_KIND)
    normalized: Dict[str, IEColumn] = {}
    for map_key, spec in merged.items():
        if not isinstance(spec, IEColumn):
            # Defensive: a decode failure already logged in load_reader_mappings
            # would leave the raw string here. Skip rather than crash the series.
            continue
        key = str(map_key).strip()
        # Back-fill the two cross-validation fields from the code seed when the
        # DB row omits them — a row written before `group`/`validates` existed
        # (migration V82) decodes with both None, and the owner editing a
        # column's role in the UI must not silently disable its subtotal check.
        # The DB still wins wherever it actually carries a value.
        if spec.group is None or spec.validates is None:
            default = IE_COLUMN_SEED.get(key)
            if default is not None:
                spec = spec._replace(
                    group=spec.group if spec.group is not None else default.group,
                    validates=spec.validates if spec.validates is not None else default.validates,
                )
        normalized[key] = spec
    return normalized


def default_ie_column_mapping() -> Dict[str, IEColumn]:
    """The code-default mapping, for callers with no DB handle.

    `parse_monthly_cash_flows` and `_income_expense_net` take rows/payloads,
    not a connection (their callers already did the SELECT). They fall back to
    this rather than growing a second DB round-trip; a DB-holding caller should
    pass `load_ie_column_mapping(db)` so owner overrides apply.
    """
    from src.database.mapping_seeds import IE_COLUMN_SEED  # noqa: PLC0415 — cheap, avoids re-export churn

    return {str(k).strip(): v for k, v in IE_COLUMN_SEED.items()}


def destination_buckets(mapping: Dict[str, IEColumn]) -> List[str]:
    """The investment-destination bucket keys, derived from the mapping.

    Every bucket carried by a CNY `invested` column, sorted for a stable key
    order. Adding a destination is therefore a data change (one new mapping
    row), never a code change.
    """
    buckets = {
        spec.bucket
        for spec in mapping.values()
        if spec.role == "invested" and spec.currency == LEDGER_CURRENCY and spec.bucket
    }
    return sorted(buckets)


def role_totals(
    payload: dict,
    mapping: Dict[str, IEColumn],
    buckets: Optional[List[str]] = None,
) -> LedgerTotals:
    """Sum one payload's LEAF columns by ie_column role. All CNY.

    Args:
        payload: one `income_expense_monthly.payload`, already parsed
            (`payload_dict`).
        mapping: strip-normalized ie_column mapping (`load_ie_column_mapping`
            or `default_ie_column_mapping`).
        buckets: destination keys to pre-seed `by_bucket` with, so a series
            keeps a stable key set across months even when a destination has
            no money that month. Defaults to the mapping's own buckets.

    Columns that are unmapped (surfaced as an actionable `candidate` by the
    ie_column unmapped scan), `currency='USD'` (native-currency siblings), or
    role `computed`/`reference`/`ignored` contribute to nothing — `computed`
    most of all: those are the Excel's own aggregates, and summing one
    alongside its own leaves is the double count this design removes.
    """
    bucket_keys = buckets if buckets is not None else destination_buckets(mapping)
    by_bucket = {bucket: 0.0 for bucket in bucket_keys}
    invested = redemption = income = expense = 0.0
    pass_through_in = pass_through_out = 0.0

    for raw_key, raw_value in payload.items():
        spec = mapping.get(str(raw_key).strip())
        if spec is None or spec.currency != LEDGER_CURRENCY:
            continue
        role = spec.role
        if role == "invested":
            amount = _num(raw_value)
            invested += amount
            if spec.bucket in by_bucket:
                by_bucket[spec.bucket] += amount
            elif spec.bucket:
                by_bucket[spec.bucket] = amount
        elif role == "redemption":
            redemption += _num(raw_value)
        elif role == "pass_through":
            # bucket says which end of the round trip this is. A pass_through
            # with no bucket is a mapping defect (the API 422s on create/patch);
            # counting it as neither end is the safe fail — it must never
            # silently land in a basis.
            if spec.bucket == "outflow":
                pass_through_out += _num(raw_value)
            elif spec.bucket == "inflow":
                pass_through_in += _num(raw_value)
            else:
                logger.warning(
                    "ie_column %r is role='pass_through' with no inflow/outflow bucket — "
                    "counted in neither direction", raw_key,
                )
        elif role == "income":
            income += _num(raw_value)
        elif role == "expense":
            expense += _num(raw_value)
        elif role not in _NON_SUMMED_ROLES:
            # An unrecognised role contributes to nothing — which is the safe
            # direction, but never silently: a role that was renamed in code
            # while a DB override still carries the old name would otherwise
            # drop a whole column out of every total with no signal at all.
            logger.warning(
                "ie_column %r carries unknown role %r — contributing to no total. "
                "A DB override may predate a role rename; re-map it in Data Sources.",
                raw_key, role,
            )
        # computed / reference / ignored: never summed. See module docstring.

    return LedgerTotals(
        by_bucket=by_bucket,
        invested=invested,
        redemption=redemption,
        pass_through_in=pass_through_in,
        pass_through_out=pass_through_out,
        income=income,
        expense=expense,
    )


# ---------------------------------------------------------------------------
# Cross-validation: derived totals vs the Excel's own aggregate columns
# ---------------------------------------------------------------------------


def _leaf_sum(payload: dict, mapping: Dict[str, IEColumn], target: dict) -> float:
    """Σ of the CNY LEAF columns matching a `validates` target.

    target: ``{"roles": [...], "groups": [...]}`` — a leaf matches if its role
    is in `roles` OR its group is in `groups` (union, counted once). Leaves
    only: a `computed` column can never be a member of another aggregate, which
    is what stops an aggregate from validating against itself.
    """
    roles = set(target.get("roles") or ())
    groups = set(target.get("groups") or ())
    total = 0.0
    for raw_key, raw_value in payload.items():
        spec = mapping.get(str(raw_key).strip())
        if spec is None or spec.currency != LEDGER_CURRENCY or spec.role == "computed":
            continue
        if spec.role in roles or (spec.group is not None and spec.group in groups):
            total += _num(raw_value)
    return total


def aggregate_divergences(
    payload: dict,
    mapping: Dict[str, IEColumn],
    tolerance: float = AGGREGATE_TOLERANCE,
) -> "List[Dict[str, Any]]":
    """Check every Excel aggregate in this payload against Huinsight's own leaf sum.

    This is the guard the "derive, never read" design needs, and the owner
    asked for it explicitly (2026-08-01: 「excel 里的计算数字拿来验证 ok，需要确保
    这点」). Reading an aggregate to CHECK our arithmetic is consistent with the
    rule; reading it to PRODUCE a number is not — nothing returned here reaches
    a reported figure.

    Which aggregate covers which leaves is DATA, not code: each `computed`
    mapping row carries `validates` (`{"roles": [...], "groups": [...]}`) and
    each leaf carries its `group` tag, so declaring a new aggregate is a
    mapping edit and renaming a column cannot break a check (the tag travels
    with the row). No column-name prefix matching anywhere.

    It catches both failure directions:
      (a) a leaf column the owner added is unmapped -> Huinsight's sum is SHORT of
          his aggregate (negative delta);
      (b) his SUM range is broken, or reaches a `_USD` sibling -> the Excel
          value is wrong and Huinsight's sum is higher/lower (either sign).

    Returns one dict per diverging aggregate:
    ``{"column", "excel_value", "derived_value", "delta", "validates"}``.
    Empty when everything reconciles, when the aggregate column is absent or
    blank (an absent aggregate is not a divergence), or when a `computed`
    column declares no `validates` target.
    """
    out: "List[Dict[str, Any]]" = []
    for raw_key, raw_value in payload.items():
        spec = mapping.get(str(raw_key).strip())
        if spec is None or spec.role != "computed" or not spec.validates:
            continue
        if raw_value is None or raw_value == "":
            continue
        try:
            excel_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        derived_value = _leaf_sum(payload, mapping, spec.validates)
        delta = derived_value - excel_value
        if abs(delta) > tolerance:
            out.append({
                "column": str(raw_key).strip(),
                "excel_value": round(excel_value, 2),
                "derived_value": round(derived_value, 2),
                "delta": round(delta, 2),
                "validates": spec.validates,
            })
    return out


def validate_ie_totals(db, months: int = 12, mapping: Optional[Dict[str, IEColumn]] = None) -> "List[Dict[str, Any]]":
    """Run `aggregate_divergences` over the most recent `months` ledger rows.

    Read-only. Never raises (a missing table / unreadable row yields []) — this
    is a health signal surfaced next to the unmapped-column scan
    (`GET /settings/sources/financial_summary/mappings?kind=ie_column`), not a
    gate. Each divergence is also logged at WARNING once per call, so it is
    visible in the sync/server log even with no one looking at the UI.

    Returns one dict per diverging (month, aggregate column), newest first:
    ``{"month", "column", "derived_field", "excel_value", "derived_value", "delta"}``.
    """
    try:
        rows = db.execute(
            "SELECT transaction_date, payload FROM income_expense_monthly "
            "ORDER BY transaction_date DESC LIMIT ?",
            [months],
        ).fetchall()
    except Exception as e:  # noqa: BLE001 — health signal, never a hard dependency
        logger.debug("validate_ie_totals: income_expense_monthly unavailable (%s)", e)
        return []

    mapping = mapping if mapping is not None else load_ie_column_mapping(db)
    out: "List[Dict[str, Any]]" = []
    for transaction_date, payload_json in rows:
        payload = payload_dict(payload_json)
        if not payload:
            continue
        month = (
            transaction_date.strftime("%Y-%m")
            if hasattr(transaction_date, "strftime")
            else str(transaction_date)[:7]
        )
        for divergence in aggregate_divergences(payload, mapping):
            logger.warning(
                "月度收支 %s: Huinsight derives %.2f for the leaves behind '%s', but that Excel "
                "column says %.2f (delta %+.2f) — a leaf column is unmapped, or the "
                "workbook's SUM range is broken/reaches a _USD sibling",
                month, divergence["derived_value"], divergence["column"],
                divergence["excel_value"], divergence["delta"],
            )
            out.append({"month": month, **divergence})
    return out
