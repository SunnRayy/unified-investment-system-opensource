"""Default asset-class taxonomy, seeded on every install.

Why this file exists
--------------------
Until 2026-08-30 `taxonomy_classes` had **no seed at all**. The table was
populated only by the taxonomy-management UI, one row at a time, by hand. The
owner's database accumulated 23 rows that way over months — and that database is
never exported, so nobody else ever got them.

The effect on a fresh install was severe and completely invisible to the owner:
allocation and attribution both resolve a holding's class with

    COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')

so with an empty `taxonomy_classes` the join finds nothing and almost everything
lands in 'Unclassified'. A first-run user saw the Allocation report at ~70%
"Unclassified" and an empty performance breakdown — two of the strongest
features looking broken, on the one run that forms their whole impression.

This is the project's recurring failure shape: *it worked because one machine's
database held state no other machine could obtain.*

What is in here
---------------
Nothing personal. This is the standard asset-class tree — Equity, Fixed Income,
Cash, and so on — plus the Chinese display names the UI already expects in
`name_cn`. It describes the shape of a portfolio, not anyone's portfolio.

Note for a future refactor (deliberately not done here, mid-launch): this same
tree is already spelled out twice more, as `DISPLAY_MAP` in
`src/services/portfolio_helpers.py` and `src/services/compass_allocation.py`,
where each entry is `"<name> (<name_cn>)"`. Three copies of one table that must
agree is exactly the failure this codebase keeps rediscovering. Collapsing
`DISPLAY_MAP` onto this definition is the right move the next time someone is
in those files.
"""

from __future__ import annotations

# (name, name_cn, sort_order) — level 0, ordered as the UI presents them.
TOP_LEVEL_CLASSES: list[tuple[str, str, int]] = [
    ("Equity", "股票", 1),
    ("Fixed Income", "固定收益", 2),
    ("Real Estate", "房地产", 3),
    ("Commodity", "商品", 4),
    ("Cash", "现金", 5),
    ("Alternative", "另类投资", 6),
    ("Insurance", "保险", 7),
]

# parent name -> [(name, name_cn), ...] — level 1.
SUB_CLASSES: dict[str, list[tuple[str, str]]] = {
    "Equity": [
        ("CN Equity", "A股"),
        ("HK ETF", "港股"),
        ("US Equity", "美股"),
    ],
    "Fixed Income": [
        ("CN Bonds", "国债"),
        ("US Bonds", "美债"),
        ("Money Market", "货币市场"),
        ("Bank Wealth", "银行理财"),
    ],
    "Real Estate": [
        ("Property", "住宅地产"),
    ],
    "Commodity": [
        ("Gold", "黄金"),
        ("Other Commodity", "其他贵金属"),
        ("Energy", "能源"),
    ],
    "Cash": [
        ("Cash Checking", "活期存款"),
        ("Cash Deposit", "定期存款"),
    ],
    "Alternative": [
        ("SMB", "创业投资"),
        ("Crypto", "加密货币"),
    ],
    "Insurance": [
        ("Insurance Products", "保险"),
    ],
}

# Investment tiers. Generic strategy buckets, not anyone's allocation.
# (id, English name, Chinese name) — `asset_registry.tier` stores the *name*.
ASSET_TIERS: list[tuple[str, str]] = [
    ("tier_1_core", "第一梯队 (底仓/价值型)"),
    ("tier_2_diversification", "第二梯队 (辅助/分散)"),
    ("tier_3_trading", "第三梯队 (交易/择时)"),
]

# Asset-ID prefix → class name, as `id_regex` classification rules.
#
# These are the last thing AutoTagger tries, after every curated exact-ID,
# exact-name and name-regex rule has missed. They exist so a database nobody has
# curated yet still classifies its holdings instead of showing an allocation
# report that is almost entirely "Unclassified".
#
# **A prefix is a defensible default, not a correct answer.** It says what kind
# of instrument something is and which market it trades in — not its asset
# class. `CN_FUND_` covers equity funds, bond funds and money-market funds
# alike, and this maps all of them to CN Equity. That is wrong for some
# holdings, and it is still much better than the alternative: an unclassified
# holding is invisible to allocation, drift and attribution, whereas a
# misclassified one is visible and one edit away from correct in the taxonomy
# UI. Curated rules always win, so correcting a holding once makes it stay
# correct.
#
# Order matters — first match wins, so the specific prefixes precede the general
# ones (CASH_Deposit_ before CASH_).
ID_PREFIX_RULES: list[tuple[int, str, str]] = [
    (500, r"^CASH_Deposit_", "Cash Deposit"),
    (501, r"^CASH_", "Cash Checking"),
    (510, r"^US_STK_", "US Equity"),
    (511, r"^US_ETF_", "US Equity"),
    (512, r"^RSU_", "US Equity"),
    (520, r"^CN_FUND_", "CN Equity"),
    (521, r"^HK_ETF_", "HK ETF"),
    (530, r"^(ALTS_Paper_Gold|GOLD_)", "Gold"),
    (540, r"^INS_", "Insurance Products"),
    (550, r"^Property_", "Property"),
    (560, r"^Wealth_", "Bank Wealth"),
    (570, r"^Crypto_", "Crypto"),
]

# Deliberately absent: `Pension_`. The default taxonomy has no Pension class,
# and filing a pension under Cash or Fixed Income would be a guess presented as
# a fact. An unclassified pension is honest and takes one click to place.


# Classes a rebalancing engine must never propose trading. Property and
# insurance are not positions you can trim on a drift signal, and treating them
# as tradeable is what produced "sell your apartment" style guidance before
# `taxonomy_classes.is_rebalanceable` became the authority over the unreliable
# `asset_registry.is_rebalanceable`.
NON_REBALANCEABLE: frozenset[str] = frozenset(
    {"Real Estate", "Property", "Insurance", "Insurance Products"}
)


def is_rebalanceable(name: str) -> bool:
    """Whether a class of this name may participate in rebalancing."""
    return name not in NON_REBALANCEABLE
