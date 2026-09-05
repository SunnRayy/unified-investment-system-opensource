"""Default risk profiles and their per-class targets, seeded on every install.

Why this file exists
--------------------
`risk_profiles` and `risk_profile_allocations` had no seed until 2026-09-02.
Both tables were populated only by the Risk Profiles UI, by hand. The owner's
database accumulated four profiles that way; that database is never exported,
so no other install ever got them.

The effect on a fresh install was total and, to the owner, invisible. The
Allocation Report resolves a class target with

    targets.get(class_name, {"target": 0.0, "tolerance": 5.0})

so with no active profile *every* class resolves to a 0% target, and every
holding — however small, however sensible — reads as over target. A first-run
user opened the rebalancing feature and saw 100% of their portfolio flagged as
breaching. That is the worst possible first impression for the one screen whose
entire job is to tell you what is *not* fine.

This is the project's recurring failure shape, and it is the third instance
found in as many weeks: *it worked because one machine's database held state no
other machine could obtain.* See `taxonomy_seeds.py` for the previous one.

Why the targets are set at sub-class level
------------------------------------------
The obvious fix — set five top-level targets, Equity 55 / Fixed Income 20 /
etc. — leaves the report half-broken, because the Allocation Report renders a
row per sub-class too. Those rows would still read `Target 0.00%` with a
warning triangle. `build_compass_allocation()` rolls child targets *up* into
their parent (`child_target_sum`), so targeting the leaves gives correct
numbers at both levels from one set of values. This also matches how the
feature is actually used: the owner's own active profile targets CN Equity,
US Equity, US Bonds, Gold and so on — never the top-level classes.

Sub-classes deliberately left untargeted (Bank Wealth, Other Commodity, Energy,
SMB) are handled by the *other* half of this fix: `compass_allocation` now
distinguishes "no target set" from "target is 0%" and reports the former as
unknown rather than as a 100% breach. Seeding a target for every leaf would
have papered over that distinction instead of fixing it.

What is in here
---------------
Nothing personal. Four textbook risk-ladder allocations — the same shape any
introductory asset-allocation table would give. They describe model portfolios,
not anyone's portfolio.

Naming: `name` is what the Risk Profiles page renders, verbatim, with no
localisation. The product defaults to English (Program BIL), so these seed in
English. `name_en` is left NULL because nothing reads it — a Chinese-locale
user renames a profile in the UI, and the owner's existing Chinese-named
profiles are never touched by this seed.
"""

from __future__ import annotations

# (name, description, is_active) — the standard risk ladder, low to high.
# Exactly one profile may be active; `Balanced` is the default because it is
# the one that makes a mixed demo portfolio read as partly on-target and partly
# drifting, which is what the report is for.
PROFILES: list[tuple[str, str, bool]] = [
    (
        "Conservative",
        "Capital preservation first. Bond- and cash-heavy, minimal alternatives.",
        False,
    ),
    (
        "Balanced",
        "A middle course — majority equity, a real bond and cash buffer, small gold and crypto sleeves.",
        True,
    ),
    (
        "Growth",
        "Long horizon. Equity-led, with the fixed-income sleeve kept only as ballast.",
        False,
    ),
    (
        "Aggressive",
        "Maximum growth, minimum buffer. Assumes no near-term call on the money.",
        False,
    ),
]

# profile name -> {sub-class name: target %}. Each profile sums to exactly 100.
#
# Every key must be a `taxonomy_classes.name` from taxonomy_seeds.SUB_CLASSES —
# the seed resolves names to ids and skips (with a WARNING) anything it cannot
# find, so a typo here degrades to a missing target rather than a crash.
ALLOCATIONS: dict[str, dict[str, float]] = {
    "Conservative": {
        "CN Equity": 10.0, "US Equity": 12.0, "HK ETF": 3.0,          # Equity 25
        "US Bonds": 22.0, "CN Bonds": 13.0, "Money Market": 10.0,     # Fixed Income 45
        "Gold": 8.0,                                                  # Commodity 8
        "Cash Checking": 6.0, "Cash Deposit": 14.0,                   # Cash 20
        "Crypto": 2.0,                                                # Alternative 2
    },
    "Balanced": {
        "CN Equity": 20.0, "US Equity": 27.0, "HK ETF": 8.0,          # Equity 55
        "US Bonds": 12.0, "CN Bonds": 5.0, "Money Market": 3.0,       # Fixed Income 20
        "Gold": 10.0,                                                 # Commodity 10
        "Cash Checking": 4.0, "Cash Deposit": 6.0,                    # Cash 10
        "Crypto": 5.0,                                                # Alternative 5
    },
    "Growth": {
        "CN Equity": 24.0, "US Equity": 36.0, "HK ETF": 10.0,         # Equity 70
        "US Bonds": 8.0, "CN Bonds": 2.0, "Money Market": 2.0,        # Fixed Income 12
        "Gold": 8.0,                                                  # Commodity 8
        "Cash Checking": 2.0, "Cash Deposit": 3.0,                    # Cash 5
        "Crypto": 5.0,                                                # Alternative 5
    },
    "Aggressive": {
        "CN Equity": 26.0, "US Equity": 44.0, "HK ETF": 10.0,         # Equity 80
        "US Bonds": 4.0, "CN Bonds": 1.0,                             # Fixed Income 5
        "Gold": 5.0,                                                  # Commodity 5
        "Cash Checking": 3.0,                                         # Cash 3
        "Crypto": 7.0,                                                # Alternative 7
    },
}
