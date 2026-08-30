"""A fresh install must arrive with a usable asset-class taxonomy.

Regression cover for the 2026-08-30 finding: `taxonomy_classes` had no seed, so
every database except the owner's started empty. Allocation and attribution both
resolve a class through

    COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')

which means an empty table sends nearly everything to 'Unclassified' — a first
run showed ~70% unclassified and an empty performance breakdown.

The bug was invisible to the only person who could have noticed it: his database
had accumulated the 23 rows by hand through the taxonomy UI, and the database is
never exported. These tests fail on a fresh database, which is the only place
the bug ever existed.
"""

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database
from src.database.taxonomy_seeds import (
    NON_REBALANCEABLE,
    SUB_CLASSES,
    TOP_LEVEL_CLASSES,
)

EXPECTED_TOTAL = len(TOP_LEVEL_CLASSES) + sum(len(v) for v in SUB_CLASSES.values())


@pytest.fixture
def fresh_db(tmp_path):
    """A brand-new database taken through the real bootstrap path."""
    db = DatabaseConnector(str(tmp_path / "fresh.duckdb"))
    bootstrap_database(db)
    yield db
    db.close()


def test_fresh_database_has_a_populated_taxonomy(fresh_db):
    count = fresh_db.execute("SELECT COUNT(*) FROM taxonomy_classes").fetchone()[0]
    assert count == EXPECTED_TOTAL, (
        f"fresh install seeded {count} taxonomy classes, expected {EXPECTED_TOTAL} — "
        "an empty or partial taxonomy sends holdings to 'Unclassified'"
    )
    # Anti-vacuity: a seed that produced nothing must not read as success.
    assert EXPECTED_TOTAL > 0


def test_every_top_level_class_is_present_and_parentless(fresh_db):
    rows = fresh_db.execute(
        "SELECT name, parent_id, level FROM taxonomy_classes WHERE level = 0"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {n for n, _, _ in TOP_LEVEL_CLASSES}
    for name, parent_id, level in rows:
        assert parent_id is None, f"top-level class {name!r} must not have a parent"
        assert level == 0


def test_every_sub_class_points_at_its_declared_parent(fresh_db):
    rows = fresh_db.execute(
        """
        SELECT c.name, p.name
        FROM taxonomy_classes c
        JOIN taxonomy_classes p ON c.parent_id = p.id
        WHERE c.level = 1
        """
    ).fetchall()
    actual = {child: parent for child, parent in rows}
    expected = {
        child: parent
        for parent, children in SUB_CLASSES.items()
        for child, _ in children
    }
    assert actual == expected


def test_property_and_insurance_are_not_rebalanceable(fresh_db):
    """Rebalancing must never propose trading an apartment or a policy.

    `taxonomy_classes.is_rebalanceable` is the authority here — the
    `asset_registry` column of the same name is unreliable and reads TRUE even
    for property.
    """
    rows = fresh_db.execute(
        "SELECT name, is_rebalanceable FROM taxonomy_classes"
    ).fetchall()
    for name, rebalanceable in rows:
        assert rebalanceable == (name not in NON_REBALANCEABLE), (
            f"{name!r} has is_rebalanceable={rebalanceable}"
        )
    # The flag must actually discriminate, or the assertion above is vacuous.
    assert any(not r for _, r in rows) and any(r for _, r in rows)


def test_chinese_display_name_is_populated(fresh_db):
    """`name_cn` drives the zh-CN UI; a null here renders a blank class label."""
    populated = fresh_db.execute(
        "SELECT COUNT(*) FROM taxonomy_classes WHERE name_cn IS NOT NULL AND name_cn <> ''"
    ).fetchone()[0]
    # Without this, an empty table passes the assertion below by having no rows
    # to violate it — the exact bug under test would go unnoticed here.
    assert populated == EXPECTED_TOTAL, (
        f"only {populated} of {EXPECTED_TOTAL} classes carry a Chinese name"
    )
    missing = fresh_db.execute(
        "SELECT name FROM taxonomy_classes WHERE name_cn IS NULL OR name_cn = ''"
    ).fetchall()
    assert not missing, f"classes with no Chinese name: {[m[0] for m in missing]}"


def test_seeding_twice_does_not_duplicate(fresh_db):
    """Bootstrap runs on every startup — it must add only what is missing."""
    before = fresh_db.execute("SELECT COUNT(*) FROM taxonomy_classes").fetchone()[0]
    bootstrap_database(fresh_db)
    after = fresh_db.execute("SELECT COUNT(*) FROM taxonomy_classes").fetchone()[0]
    assert after == before == EXPECTED_TOTAL


def test_fresh_database_can_classify_by_asset_id(fresh_db):
    """The whole point: an uncurated database must still classify holdings.

    AutoTagger's other three strategies all match on values a person entered —
    specific asset IDs they own, or display names they recognise. A fresh
    install has none, which is why 58.8% of a first demo sync landed in
    'Unclassified' before the id_regex fallback existed.
    """
    from src.classification.auto_tagger import AutoTagger

    tagger = AutoTagger(fresh_db)
    cases = [
        ("US_STK_AAPL", "Apple Inc"),
        ("US_ETF_VOO", "Vanguard S&P 500"),
        ("CN_FUND_000198", "某货币基金"),
        ("INS_SomePolicy", "A policy"),
        ("ALTS_Paper_Gold", "Paper gold"),
        ("Property_Home", "A flat"),
        ("CASH_Deposit_X", "A deposit"),
    ]
    for asset_id, name in cases:
        result = tagger.classify(asset_id, name)
        assert result.class_id is not None, f"{asset_id} classified as Unclassified"
        assert result.method == "auto_id_regex"


def test_specific_id_prefixes_win_over_general_ones(fresh_db):
    """`CASH_Deposit_` must not be swallowed by `CASH_`. First match wins, so
    the ordering in ID_PREFIX_RULES is load-bearing, not cosmetic."""
    from src.classification.auto_tagger import AutoTagger

    tagger = AutoTagger(fresh_db)
    deposit = tagger.classify("CASH_Deposit_CMB", "定期")
    checking = tagger.classify("CASH_USD", "USD cash")

    name_of = lambda cid: fresh_db.execute(  # noqa: E731
        "SELECT name FROM taxonomy_classes WHERE id = ?", [cid]
    ).fetchone()[0]

    assert name_of(deposit.class_id) == "Cash Deposit"
    assert name_of(checking.class_id) == "Cash Checking"


def test_id_fallback_never_overrides_a_curated_rule(fresh_db):
    """The fallback runs last by design, so an established database that has
    already been curated behaves exactly as it did before this existed."""
    from src.classification.auto_tagger import AutoTagger

    equity = fresh_db.execute(
        "SELECT id FROM taxonomy_classes WHERE name = 'CN Bonds'"
    ).fetchone()[0]
    next_id = fresh_db.execute(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM classification_rules"
    ).fetchone()[0]
    fresh_db.execute(
        """
        INSERT INTO classification_rules (id, rule_type, pattern, class_id, priority, source)
        VALUES (?, 'exact_id', 'US_STK_AAPL', ?, 10, 'test')
        """,
        [next_id, equity],
    )

    result = AutoTagger(fresh_db).classify("US_STK_AAPL", "Apple Inc")
    assert result.class_id == equity, "the id_regex fallback overrode a curated exact_id rule"
    assert result.method != "auto_id_regex"


def test_tiers_are_seeded(fresh_db):
    from src.database.taxonomy_seeds import ASSET_TIERS

    rows = fresh_db.execute("SELECT id FROM asset_tiers").fetchall()
    assert {r[0] for r in rows} == {t for t, _ in ASSET_TIERS}


def test_existing_classes_are_left_alone(fresh_db):
    """An established database keeps its own edits; the seed only fills gaps.

    Someone who renamed a class's Chinese label, or set their own
    rebalanceable flag, must not have it reverted on the next startup.
    """
    fresh_db.execute(
        "UPDATE taxonomy_classes SET name_cn = '自定义' WHERE name = 'Gold'"
    )
    bootstrap_database(fresh_db)
    name_cn = fresh_db.execute(
        "SELECT name_cn FROM taxonomy_classes WHERE name = 'Gold'"
    ).fetchone()[0]
    assert name_cn == "自定义"
