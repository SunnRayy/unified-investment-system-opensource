"""Seed data for the `reader_mappings` table (ADR-023 / Reader Mapping Management WS-A).

Public-export twin of ``src/database/mapping_seeds.py`` (Program OSR WS-7).

The private module mixes two things: generic/structural definitions (the
``IEColumn`` type, the role/bucket/group vocabulary, the Schwab/CN-fund
vocabulary seeds) and the owner's real Financial-Summary column names, asset
labels, and historical figures. The structural half is safe to publish
verbatim; the data half is not. Rather than excluding the module outright —
several files import it unconditionally at module level, and a fresh
``--init`` seeds the `reader_mappings` table from it via a migration gate, so
excluding it breaks the app on a clean clone — ``export_public.sh`` copies
*this* file over ``src/database/mapping_seeds.py`` in the staging tree only.
The real file never leaves the private repo.

Every owner-specific value below has been swapped for its persona equivalent
from ``tools/demo_data/persona.yaml`` / ``seeds/example/reader_mappings/
financial_summary.yaml`` — the same fictional household used everywhere else
in the public repo. Column *names*, roles, buckets, and groups are unchanged
from the private module (they are product schema, not personal data); only
identifying labels (a property nickname, specific insurance product names)
and narrative comments that quoted the owner's real transaction history have
been replaced.

Two independent layers import from this module:

  1. ``src.database.connector`` (migration V75+) — seeds the `reader_mappings`
     table on a fresh/existing DB, idempotently, keyed on the natural
     (reader_key, mapping_kind, map_key) UNIQUE key.
  2. ``src.sources.reader_hooks`` — uses the same dict as the hardcoded
     fallback (fresh-DB bootstrap default, and the value used when the DB
     table is missing or has no override for a given key).

Import constraint: this module is imported by BOTH ``src.database.connector``
and ``src.sources.reader_hooks`` (the latter enforces "stdlib + pandas only"
at module level as a cycle guard against src.sources.*/src.sync.*/src.api.*).
To stay safe for both importers, this module must import ONLY stdlib — no
pandas, no other ``src.*`` modules.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

# Financial Summary Excel column -> (asset_id, asset_name, currency)
# Verbatim source of truth, formerly hardcoded directly in reader_hooks.py.
#
# Note: the FS Excel stores CNY-converted values in the "USD"/"HKD"-labeled
# columns (owner applies the FX rate in Excel before entry) — currency is
# pinned to "CNY" to match the stored market_value; the asset_id suffix
# (_USD, _HKD) is traceability only. See CLAUDE.md "Known Critical Edge Cases".
FS_ASSET_MAPPING_SEED: "dict[str, tuple[str, str, str]]" = {
    "RMB现金现金": ("CASH_Cash_CNY", "现金 (CNY)", "CNY"),
    "RMB存款_中行": ("CASH_Deposit_BOC_CNY", "中行存款 (CNY)", "CNY"),
    "RMB存款_招行": ("CASH_Deposit_CMB_CNY", "招行存款 (CNY)", "CNY"),
    "RMB存款_北京银行": ("CASH_Deposit_BOB_CNY", "北京银行存款 (CNY)", "CNY"),
    "RMB存款_工行": ("CASH_Deposit_ICBC_CNY", "工行存款 (CNY)", "CNY"),
    "美元存款_中行": ("CASH_Deposit_BOC_USD", "中行存款 (USD)", "CNY"),
    "美元存款_Chase": ("CASH_Deposit_Chase_USD", "Chase存款 (USD)", "CNY"),
    "美元存款_Discover/Citi": ("CASH_Deposit_Discover_USD", "Discover存款 (USD)", "CNY"),
    "HKD存款_HSBC": ("CASH_Deposit_HSBC_HKD", "HSBC存款 (HKD)", "CNY"),
    "美元存款_HSBC": ("CASH_Deposit_HSBC_USD", "HSBC存款 (USD)", "CNY"),
    "固定资产_房产_阳光花园": ("Property_阳光花园", "阳光花园房产", "CNY"),
    "投资资产_银行理财_招行": ("Wealth_CMB", "招行理财", "CNY"),
    "投资资产_存款基金_个人养老金": ("Pension_Personal", "个人养老金", "CNY"),
}

# ADR-023 A4.1 — FS Excel columns seeded with status='ignored' (V76): the
# live-smoke-test unmapped scan (2026-07-18, see
# docs/plans/2026-07-18-reader-mapping-management.md) showed these columns
# are FS's own informational copy of a value another reader already owns
# authoritatively (Schwab/IBKR US equities, RSU_Excel vesting positions, Gold
# Excel paper gold, Insurance Excel policies) — melting them would double-
# count. "投资资产_另类投资_公司股份投资" has no reader coverage today but was
# reviewed and deliberately excluded by the owner; "Amazon Stock" is a legacy
# bare column superseded by "投资资产_公司RSU_Amazon Stock". These are NOT a
# structural heuristic (unlike native/computed/liability in
# src.services.reader_mappings.scan_unmapped_columns) — they are owner
# decisions about specific columns, so they live as data (ignored rows), not
# code pattern-matching.
FS_IGNORED_COLUMNS_SEED: "tuple[str, ...]" = (
    "投资资产_股票基金_美股基金_Schwab",   # Schwab reader is authoritative
    "投资资产_股票基金_美股基金_IBKR",     # IBKR reader is authoritative
    "投资资产_公司RSU_Amazon Stock",       # RSU_Excel reader is authoritative
    "投资资产_公司RSU_Google Stock",       # RSU_Excel reader is authoritative
    "投资资产_黄金_纸黄金(元)",            # Gold Excel reader is authoritative
    "投资资产_黄金_纸黄金(克)",            # Gold Excel reader is authoritative
    "投资资产_黄金_黄金ETF",               # Gold Excel reader is authoritative
    "投资资产_长期保险_安泰人生",          # Insurance Excel reader is authoritative
    "投资资产_另类投资_公司股份投资",       # owner-reviewed, no reader coverage yet
    "Amazon Stock",                        # legacy duplicate, superseded above
)

# ADR-023 WS-B — Gold/Insurance/RSU id_field_map seeds (migration V77).
#
# reader_key -> {"field:label": code}, mirroring config/readers/{gold,
# insurance,rsu}.yaml `id_field_maps` EXACTLY. This flat "field:label" shape
# matches the `reader_mappings.map_key` natural-key convention (mapping_kind=
# 'id_field_map'); src.services.reader_mappings.nest_id_field_map() converts
# it to the {field: {label: code}} nested shape the config-driven engine's
# id_template resolution (SheetConfig.id_field_maps) expects.
#
# A test (tests/services/test_reader_mappings.py::TestIdFieldMapSeedsMatchYaml)
# asserts this dict equals the flattened YAML content for every reader below —
# the YAML stays the code-default source of truth (as it always has been);
# this seed must never silently drift from it.
#
# insurance: {} — insurance.yaml declares `id_field_maps: {}` on both sheets
# (legacy behavior: raw product_name/policy_name used directly, no map at
# all) — an empty seed is the correct mirror of "no defaults exist today".
# The reader_key is still a valid _MANAGED_READERS entry so the owner can add
# a first mapping via the UI without a code change.
ID_FIELD_MAP_SEEDS: "dict[str, dict[str, str]]" = {
    "gold": {
        "asset_name:纸黄金": "PAPER",
        "asset_name:黄金ETF": "ETF",
        "account:招行": "CMB",
        "account:工行": "ICBC",
        "account:中行": "BOC",
        "account:建行": "CCB",
    },
    "insurance": {},
    "rsu": {
        "asset_name:Amazon RSU": "AMZN",
    },
}

# ADR-023 WS-C — Schwab/CN-fund vocabulary seeds (migration V78).
#
# Single source of truth for the four WS-C vocabulary kinds, formerly
# hardcoded directly in reader_hooks.py. src.sources.reader_hooks re-exports
# these exact constants as its private module-level names (_SCHWAB_KNOWN_ETFS,
# _SCHWAB_SYMBOL_NORMALIZATIONS, _SCHWAB_ACTION_MAPPING, _CN_FUND_TYPE_MAP) so
# every existing consumer/test keeps working unchanged — mirrors the
# FS_ASSET_MAPPING_SEED pattern exactly.

# Known Schwab ETFs for transaction-symbol normalization (no security_type in
# the transactions CSV) — mapping_kind='known_etf', map_value={"etf": true}.
SCHWAB_KNOWN_ETFS_SEED: "frozenset[str]" = frozenset({
    'QQQ', 'SPY', 'IVV', 'VTI', 'VOO', 'VEA', 'VWO', 'VNQ', 'VGK',
    'IEFA', 'IEMG', 'EFA', 'EEM', 'AGG', 'BND', 'LQD', 'HYG', 'TLT',
    'IEF', 'SHY', 'TIP', 'VCIT', 'VCSH', 'MUB', 'EMB', 'JNK', 'PFF',
    'DGRO', 'VIG', 'DVY', 'HDV', 'VYM', 'SDY', 'SCHD', 'NOBL',
    'XLF', 'XLE', 'XLV', 'XLK', 'XLI', 'XLP', 'XLY', 'XLU', 'XLB', 'XLRE',
    'ARKK', 'ARKG', 'ARKF', 'ARKW', 'ARKQ',
    'GLD', 'IAU', 'SLV', 'IBIT', 'FBTC', 'ETHA',
    'SGOV', 'SHV', 'SCHO', 'MINT', 'NEAR', 'BIL', 'ICSH',
    'TQQQ', 'SQQQ', 'SPXL', 'SPXS', 'TNA', 'TZA',
    'DIA', 'IWM', 'MDY', 'IJR', 'IJH',
})

# Schwab compound-ticker normalizations (BRK/B -> BRK-B, etc.) — shared by
# Schwab holdings/transactions AND IBKR (co-authority — IBKR's canonical-id
# resolution calls the same Schwab normalizer function) —
# mapping_kind='symbol_norm', map_value={"to": ...}.
SCHWAB_SYMBOL_NORMALIZATIONS_SEED: "dict[str, str]" = {
    'BRK/B': 'BRK-B',
    'BRK/A': 'BRK-A',
    'BRKB':  'BRK-B',
    'BRKA':  'BRK-A',
}

# Schwab action -> Huinsight transaction_type — mapping_kind='action_map',
# map_value={"type": ...}.
SCHWAB_ACTION_MAPPING_SEED: "dict[str, str]" = {
    'Buy': 'buy',
    'Sell': 'sell',
    'Cash Dividend': 'dividend',
    'Qualified Dividend': 'dividend',
    'Non-Qualified Div': 'dividend',
    'Special Qual Div': 'dividend',
    'NRA Tax Adj': 'tax_adjustment',
    'Foreign Tax Paid': 'tax_adjustment',
    'Reinvest Dividend': 'reinvest_dividend',
    'Reinvest Shares': 'reinvest_dividend',
    'Stock Split': 'stock_split',
    'Journal': 'other',
    'Wire Transfer': 'other',
    'ACH': 'other',
    'MoneyLink Transfer': 'other',
    'Credit Interest': 'other',
    # 'transfer' (Attribution & Flows WS-3.1, V79): a pseudo-type, NOT a real
    # transaction_type — 'Security Transfer' is directionally ambiguous (one
    # Schwab action label covers both ACAT legs in and out), so it is resolved
    # to 'transfer_out'/'transfer_in' by quantity sign at the reader hook
    # (src.sources.reader_hooks.schwab_transactions_from_csv, right after
    # _schwab_map_action) — it never lands on a transactions row as literal
    # 'transfer'. See src.services.reader_mappings.ALLOWED_TRANSACTION_TYPES.
    'Security Transfer': 'transfer',
}

# CN Fund raw 操作类型 -> Huinsight transaction_type — mapping_kind='type_map',
# map_value={"type": ...}.
CN_FUND_TYPE_MAP_SEED: "dict[str, str]" = {
    '申购': 'buy',
    '赎回': 'sell',
    '现金分红': 'dividend_cash',
    '红利再投资': 'dividend_reinvest',
    '活期宝即充即用': 'buy',
    '快速取现': 'sell',
    '超级转换-转入': 'transfer_in',
    '超级转换-转出': 'transfer_out',
    '超级转换份额调增': 'transfer_in',
    '超级转换份额调减': 'transfer_out',  # mirror of 份额调增 (share reduction)
    # 卖基金/买基金: the bank's "sell fund"/"buy fund" labels. The raw processor
    # now normalizes these to 赎回/申购, but keep them here so any already-written
    # 基金交易记录 rows (and stale processed tabs) still resolve correctly on read.
    '卖基金': 'sell',
    '买基金': 'buy',
}

# reader_key -> mapping_kind -> {map_key: map_value dict} — the exact shape
# migration V78 inserts (json.dumps'd) and src.services.reader_mappings'
# _DEFAULTS overlays (pre-decoded to the loader's final value shape — see
# that module's _KIND_DECODERS for known_etf/symbol_norm/action_map/type_map).
VOCAB_SEEDS: "dict[str, dict[str, dict[str, dict]]]" = {
    "schwab": {
        "known_etf": {ticker: {"etf": True} for ticker in sorted(SCHWAB_KNOWN_ETFS_SEED)},
        "symbol_norm": {k: {"to": v} for k, v in SCHWAB_SYMBOL_NORMALIZATIONS_SEED.items()},
        "action_map": {k: {"type": v} for k, v in SCHWAB_ACTION_MAPPING_SEED.items()},
    },
    "cn_fund": {
        "type_map": {k: {"type": v} for k, v in CN_FUND_TYPE_MAP_SEED.items()},
    },
}


# =============================================================================
# 月度收支 (income/expense) column semantics — mapping_kind='ie_column'
# (plan docs/plans/2026-08-01-ie-column-mapping-and-ibkr-amounts.md WS-A,
#  migration V82). ADR-023 made the 资产负债 sheet's column→asset mapping DATA;
# this does the same for the 月度收支 sheet's column SEMANTICS, which were
# hardcoded string literals in src/services/investment_contributions.py.
#
# A column the owner adds to the Excel used to be silently dropped from
# gross_invested with no error (the silent-failure / convention-contract class
# in the `uis-failure-classes` memory). Now every column is a row the owner can
# see and edit, and an unseen one surfaces as an actionable `candidate` in the
# unmapped-column scan.
# =============================================================================


class IEColumn(NamedTuple):
    """Decoded map_value for mapping_kind='ie_column'.

    Immutable on purpose (mirrors fs_column's decoded tuple shape): the merged
    dict returned by ``load_reader_mappings`` hands out these values directly
    from the module-level seed below, so a mutable value type would let one
    caller corrupt the defaults for the whole process.

    role     — what the column IS, economically (see IE_ROLES).
    bucket   — role-dependent grouping key (see IE_ROLE_BUCKETS); None for
               columns that need no grouping.
    currency — 'CNY' or 'USD'. **A 'USD' column contributes to NOTHING.**
               The owner keeps native-currency sibling columns next to the
               CNY ones and applies the FX rate himself in Excel, so summing a
               `_USD` column would double-count the same money at a second
               exchange rate (Rule 2 — every stored/derived value is CNY;
               ADR-025 §3 proved `Schawab == Schawab_USD × 参考_美元汇率`
               exactly, every month).
    group    — LEAF columns only: which Excel subtotal this leaf belongs to
               (see IE_GROUPS). Used solely by the aggregate cross-validation
               (src/services/ie_ledger.py) — never by a reported figure. It
               exists because `主动收入合计` vs `被动收入合计` cannot be told
               apart by `role` (both contain income leaves), and matching on
               the `收入_主动收入_` name prefix would be exactly the brittle
               convention-contract this workstream exists to delete: it breaks
               the moment a column is renamed. The tag travels WITH the row, so
               a rename cannot break it.
    validates — role='computed' columns only: what this Excel aggregate should
               equal, as ``{"roles": [...], "groups": [...]}`` (union of both,
               CNY leaves only). Declaring a new aggregate is a data change,
               never a code change — the checker is generic.
    """

    role: str
    bucket: Optional[str]
    currency: str
    group: Optional[str] = None
    validates: Optional[dict] = None


# role vocabulary.
#   invested   — money moved INTO an investment destination this month. Summed
#                into gross_invested, grouped by `bucket`.
#   redemption — money taken back OUT of a destination it had previously been
#                recorded into as `invested`. Subtracted (trailing-window only).
#                The test for this role is: *was this money ever recorded in a
#                投资理财_* column?* If not, it is NOT a redemption — tagging it
#                so would subtract money that was never added (the
#                double-subtract ADR-025 §4b warns about). This is why
#                收入_被动收入_股票卖出收益 (realized gain on shares that entered
#                the ledger as RSU *income*, never as 投资理财) is role='income'.
#   income     — money earned. Every 收入 LEAF column carrying this role is
#                summed into the savings-rate income basis. The Excel's own
#                收入 aggregates (总收入合计 / 主动收入合计 / 被动收入合计) are
#                role='computed' and are NEVER read for calculation — owner
#                ruling, see the `computed` note below.
#   pass_through — money that only passes through: the owner fronts a work
#                expense and is repaid for it. BOTH ends carry this role —
#                `收入_主动收入_报销` (bucket='inflow') and
#                `工作开支_出差/团建（全额报销）` (bucket='outflow') — because
#                they are two ends of the SAME money, offset only by pure
#                timing (a reimbursement can land in a different month than
#                the spend it covers — do NOT "fix" that gap). Neither is real
#                income nor real consumption, so both are excluded from BOTH
#                the income basis and the expense basis. One role rather than
#                two unrelated exclusions is deliberate: it makes the pairing
#                structural, so a future editor cannot silently break the
#                symmetry by reclassifying one side. It is NOT `redemption` —
#                that role drives `net_external = max(invested − redeemed, 0)`,
#                and a repayment must never be subtracted from contributions.
#   expense    — an expense line. Recorded for governance; no consumer today.
#   computed   — an Excel-side subtotal/total of other columns in the same sheet
#                (合计 / 支出 / 理财 columns). **Never read as a calculation
#                input**, by architectural rule: 所有 excel 里的计算/合计值都
#                不应该被 Huinsight 读取使用，Huinsight 应该用自己计算逻辑下的分类汇总保持
#                灵活性和准确性. Huinsight derives every total from the leaves instead
#                (src/services/ie_ledger.py), so correctness no longer depends
#                on whether a spreadsheet SUM range auto-expanded over a newly
#                inserted column or correctly skipped a _USD sibling. These
#                columns are still seeded — so they are classified, visible,
#                and governed — and are read ONLY by the cross-validation that
#                warns when Huinsight's derived total and the owner's aggregate
#                disagree.
#   reference  — a non-money reference figure living in the same sheet (an FX
#                rate, a gold gram price, a share price, a gram QUANTITY).
#   ignored    — reviewed by the owner and deliberately not used.
IE_ROLES: "frozenset[str]" = frozenset({
    "invested", "redemption", "income", "pass_through", "expense", "computed",
    "reference", "ignored",
})

# Investment destinations — the `by_destination` keys of
# src.services.investment_contributions.monthly_investment_flows are derived
# from the buckets actually present on role='invested' rows, so adding a
# destination is a data change (a new mapping row), never a code change.
IE_DESTINATION_BUCKETS: "frozenset[str]" = frozenset({
    "cn_fund", "us_schwab", "us_ibkr", "gold", "bank_wealth",
})

# Buckets only ever describe an investment destination. (A short-lived
# 'total_income' bucket marked 总收入合计 as the income basis; it was retired
# when the owner ruled that no Excel aggregate may be a calculation input —
# the basis is now the sum of the income LEAF columns.)
# A pass_through column's `bucket` names which END of the round trip it is —
# the money arriving back (inflow) or the money going out (outflow). Structural,
# so the two halves can be reported separately and reconciled against each other
# without any column-name matching.
IE_PASS_THROUGH_BUCKETS: "frozenset[str]" = frozenset({"inflow", "outflow"})

IE_BUCKETS: "frozenset[str]" = IE_DESTINATION_BUCKETS | IE_PASS_THROUGH_BUCKETS

IE_CURRENCIES: "frozenset[str]" = frozenset({"CNY", "USD"})

# Leaf `group` tags — the Excel's own subtotal groupings, as DATA. Each leaf
# carries the tag of the subtotal it belongs to; each `computed` aggregate
# declares which groups/roles it should equal (`validates`). This is what lets
# the cross-validation check 主动收入合计 separately from 被动收入合计 without
# pattern-matching column names.
IE_GROUPS: "frozenset[str]" = frozenset({
    "active_income",          # 主动收入合计
    "passive_income",         # 被动收入合计
    "essential_expense",      # 必要支出
    "discretionary_expense",  # 非必要支出
    "work_expense",           # 工作支出
})

# role -> the buckets that role may carry (None is always allowed except for
# 'invested', where a missing bucket would silently drop the money out of
# gross_invested — exactly the failure this workstream exists to remove).
IE_ROLE_BUCKETS: "dict[str, frozenset[str]]" = {
    "invested": IE_DESTINATION_BUCKETS,
    "redemption": IE_DESTINATION_BUCKETS,
    "income": frozenset(),
    "pass_through": IE_PASS_THROUGH_BUCKETS,
    "expense": frozenset(),
    "computed": frozenset(),
    "reference": frozenset(),
    "ignored": frozenset(),
}

# 月度收支 Excel column -> IEColumn(role, bucket, currency).
#
# Mirrors the example household's income/expense sheet
# (tools/demo_data/persona.yaml, seeds/example/reader_mappings/
# financial_summary.yaml) column-for-column. `日期` is the sheet's date column
# and `asset_id`/`source_system` are injected by the sync transform, so
# neither is a mappable column.
#
# NOTE on whitespace: the live payload key for the FX-rate column can carry a
# trailing space (the Excel header has one). Map keys are matched
# strip-normalized on both sides (see src.services.investment_contributions
# and the ie_column unmapped scan), so the tidy form is seeded here and still
# matches the untidy header.
IE_COLUMN_SEED: "dict[str, IEColumn]" = {
    # ── 收入_主动收入 (active income components — inside 总收入合计) ──────────
    "收入_主动收入_工资": IEColumn("income", None, "CNY", "active_income"),
    "收入_主动收入_RSU": IEColumn("income", None, "CNY", "active_income"),
    # Native-currency sibling of 收入_主动收入_RSU. 主动收入合计 correctly
    # EXCLUDES it in the Excel — currency='USD' keeps it out of every sum
    # here too.
    "收入_主动收入_RSU_USD": IEColumn("income", None, "USD"),  # no group: USD never sums
    # NOT income — the repaid half of a work expense the owner fronted. Paired
    # with 工作开支_出差/团建（全额报销） below (role='pass_through', the other
    # bucket); both are excluded from both bases.
    "收入_主动收入_报销": IEColumn("pass_through", "inflow", "CNY", "active_income"),
    "收入_主动收入_福利": IEColumn("income", None, "CNY", "active_income"),
    # Housing-fund withdrawals. Owner decision: these COUNT as income — the
    # housing-fund balance is not an asset this system tracks (只有
    # 个人养老金 is in the 资产负债 mapping), so the money genuinely enters the
    # tracked system from outside. Deliberately NOT split into
    # contribution-vs-withdrawal machinery.
    "收入_主动收入_公积金": IEColumn("income", None, "CNY", "active_income"),
    # Bonus / other one-off income.
    "收入_主动收入_其他偶然": IEColumn("income", None, "CNY", "active_income"),

    # ── 收入_被动收入 ───────────────────────────────────────────────────────
    # These three are redemptions: the money they return to the owner ENTERED
    # the ledger through a 投资理财_* column, so it must be netted back out of
    # the trailing-window contribution figure (ADR-025 §2).
    "收入_被动收入_基金赎回": IEColumn("redemption", "cn_fund", "CNY", "passive_income"),
    "收入_被动收入_黄金卖出": IEColumn("redemption", "gold", "CNY", "passive_income"),
    "收入_被动收入_银行理财": IEColumn("redemption", "bank_wealth", "CNY", "passive_income"),
    # A gram QUANTITY, not money — never summed with anything.
    "收入_被动收入_黄金卖出(克)": IEColumn("reference", None, "CNY"),
    # Realized gain above vest/cost price on US shares sold. role='income',
    # NOT 'redemption': the principal was already booked as income at vest
    # (收入_主动收入_RSU*) and was NEVER recorded in a 投资理财_* column, so
    # subtracting it would remove money that was never added.
    "收入_被动收入_股票卖出收益": IEColumn("income", None, "CNY", "passive_income"),
    "收入_被动收入_股票卖出收益_USD": IEColumn("income", None, "USD"),

    # ── Excel-side income totals — classified, never read for calculation ───
    # Huinsight re-derives 总收入合计 from the leaves above
    # (LedgerTotals.gross_income) and only compares against this column to warn
    # on divergence.
    "主动收入合计": IEColumn("computed", None, "CNY", None, {"groups": ["active_income"]}),
    "被动收入合计": IEColumn("computed", None, "CNY", None, {"groups": ["passive_income"]}),
    "总收入合计": IEColumn(
        "computed", None, "CNY", None,
        # By GROUP, not by role: the income side is exactly
        # 主动收入合计 + 被动收入合计, and matching by group is what keeps the
        # OUTFLOW half of pass_through (工作开支) out of an income check without
        # a second matching concept. The Excel's income total counts everything
        # that arrived, including the repaid 报销 — Huinsight's own income BASIS
        # deliberately does not (see LedgerTotals in src/services/ie_ledger.py).
        {"groups": ["active_income", "passive_income"]},
    ),

    # ── 必要开支 / 非必要开支 / 工作开支 ────────────────────────────────────
    "必要开支_日常支出_餐饮娱乐": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_日常支出_房租水电": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_日常支出_交通": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_保险_安泰人生": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_保险_公司团险": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_保险_互联网保险": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_贷款_房贷": IEColumn("expense", None, "CNY", "essential_expense"),
    "必要开支_家庭及临时支出": IEColumn("expense", None, "CNY", "essential_expense"),
    "非必要开支_旅行出游": IEColumn("expense", None, "CNY", "discretionary_expense"),
    "非必要开支_护肤衣物": IEColumn("expense", None, "CNY", "discretionary_expense"),
    "非必要开支_电子产品": IEColumn("expense", None, "CNY", "discretionary_expense"),
    "非必要开支_运动健身健康": IEColumn("expense", None, "CNY", "discretionary_expense"),
    "非必要开支_其他/娱乐": IEColumn("expense", None, "CNY", "discretionary_expense"),
    # NOT consumption — the fronted half of the same round trip as
    # 收入_主动收入_报销 above (the column name says 全额报销: fully reimbursed).
    "工作开支_出差/团建（全额报销）": IEColumn("pass_through", "outflow", "CNY", "work_expense"),

    # ── 投资理财 (the contribution ledger) ──────────────────────────────────
    "投资理财_股票基金_天天基金": IEColumn("invested", "cn_fund", "CNY"),
    "投资理财_股票基金_Schawab": IEColumn("invested", "us_schwab", "CNY"),
    # ADR-025 §3: the SAME Schwab money as 投资理财_股票基金_Schawab, recorded in
    # USD. currency='USD' is what keeps it out of every sum (summing both ~2x's
    # US investment).
    "投资理财_股票基金_Schawab_USD": IEColumn("invested", "us_schwab", "USD"),
    # CNY wired INTO IBKR is invested money, bucket 'us_ibkr'.
    "投资理财_股票基金_IBKR": IEColumn("invested", "us_ibkr", "CNY"),
    "投资理财_股票基金_IBKR_USD": IEColumn("invested", "us_ibkr", "USD"),
    "投资理财_黄金_招行纸黄金": IEColumn("invested", "gold", "CNY"),
    "投资理财_黄金_黄金ETF": IEColumn("invested", "gold", "CNY"),
    "投资理财_银行理财_招行": IEColumn("invested", "bank_wealth", "CNY"),

    # ── Excel-side expense/investment totals — classified, never read ───────
    # ⚠️ 总支出 INCLUDES 理财 (investment). Huinsight's equivalent is
    # LedgerTotals.total_outflow (expense + invested), NOT a bare
    # Σ(role='expense') — see ie_ledger.py.
    "必要支出": IEColumn("computed", None, "CNY", None, {"groups": ["essential_expense"]}),
    "非必要支出": IEColumn("computed", None, "CNY", None, {"groups": ["discretionary_expense"]}),
    "工作支出": IEColumn("computed", None, "CNY", None, {"groups": ["work_expense"]}),
    "理财": IEColumn("computed", None, "CNY", None, {"roles": ["invested"]}),
    # ⚠️ 总支出 includes 理财 — expense leaves AND invested leaves.
    "总支出": IEColumn(
        "computed", None, "CNY", None,
        # Expense groups (which include the outflow half of pass_through) PLUS
        # the invested leaves — the Excel's 总支出 bundles 理财 in. Huinsight's own
        # expense BASIS does not (investing is not spending).
        {"groups": ["essential_expense", "discretionary_expense", "work_expense"],
         "roles": ["invested"]},
    ),

    # ── 参考 (reference figures, not money flows) ───────────────────────────
    "参考_黄金价格_克价": IEColumn("reference", None, "CNY"),
    "参考_Amazon Stock Price": IEColumn("reference", None, "USD"),
    "参考_美元汇率": IEColumn("reference", None, "CNY"),
}

# The exact shape migration V82 inserts (json.dumps'd) and
# src.services.reader_mappings' _DEFAULTS decodes back to IEColumn.
def _ie_column_json(spec: IEColumn) -> dict:
    """map_value JSON for one ie_column row. `group`/`validates` are omitted
    when unset so a plain leaf stays a three-key object (and so a DB row
    written before those fields existed decodes identically — the loader
    back-fills them from this seed, see
    src.services.ie_ledger.load_ie_column_mapping)."""
    value: dict = {"role": spec.role, "bucket": spec.bucket, "currency": spec.currency}
    if spec.group is not None:
        value["group"] = spec.group
    if spec.validates is not None:
        value["validates"] = spec.validates
    return value


IE_COLUMN_SEED_JSON: "dict[str, dict]" = {
    map_key: _ie_column_json(spec) for map_key, spec in IE_COLUMN_SEED.items()
}
