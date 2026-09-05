"""A fresh install must arrive with an active risk profile.

Regression cover for the 2026-09-02 finding: `risk_profiles` and
`risk_profile_allocations` had no seed, so every database except the owner's
had no active profile. The Allocation Report resolves a class target with

    targets.get(class_name, {"target": 0.0, "tolerance": 5.0})

so with no active profile every class resolves to a 0% target and every
holding reads as over target — a first run showed 100% of the portfolio
flagged as breaching, on the one screen whose entire job is to say what is not
fine.

Invisible to the only person who could have noticed it: his database has four
hand-entered profiles, and the database is never exported. These tests fail on
a fresh database, which is the only place the bug ever existed.
"""

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import bootstrap_database
from src.database.risk_profile_seeds import ALLOCATIONS, PROFILES


@pytest.fixture
def fresh_db(tmp_path):
    """A brand-new database taken through the real bootstrap path."""
    db = DatabaseConnector(str(tmp_path / "fresh.duckdb"))
    bootstrap_database(db)
    yield db
    db.close()


def test_fresh_database_has_risk_profiles(fresh_db):
    count = fresh_db.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()[0]
    assert count == len(PROFILES), (
        f"fresh install seeded {count} risk profiles, expected {len(PROFILES)}"
    )


def test_fresh_database_has_exactly_one_active_profile(fresh_db):
    """The whole point. No active profile means every target reads 0%."""
    active = fresh_db.execute(
        "SELECT name FROM risk_profiles WHERE is_active = TRUE"
    ).fetchall()
    assert len(active) == 1, (
        f"expected exactly one active profile, found {[r[0] for r in active]} — "
        "zero actives is the bug this seed exists to fix; two is a UI-breaking "
        "ambiguity since get_active_profile() fetches one row"
    )


def test_every_seeded_profile_has_its_allocations(fresh_db):
    """A profile without allocations is worse than no profile: it looks active
    and silently targets 0% everywhere."""
    for name, _description, _active in PROFILES:
        count = fresh_db.execute(
            """
            SELECT COUNT(*)
            FROM risk_profile_allocations rpa
            JOIN risk_profiles rp ON rp.id = rpa.profile_id
            WHERE rp.name = ?
            """,
            [name],
        ).fetchone()[0]
        assert count == len(ALLOCATIONS[name]), (
            f"{name} seeded {count} allocations, expected {len(ALLOCATIONS[name])}"
        )


@pytest.mark.parametrize("profile_name", [p[0] for p in PROFILES])
def test_each_profile_sums_to_one_hundred_percent(fresh_db, profile_name):
    total = fresh_db.execute(
        """
        SELECT SUM(rpa.target_pct)
        FROM risk_profile_allocations rpa
        JOIN risk_profiles rp ON rp.id = rpa.profile_id
        WHERE rp.name = ?
        """,
        [profile_name],
    ).fetchone()[0]
    assert total is not None and abs(float(total) - 100.0) < 0.01, (
        f"{profile_name} targets sum to {total}, not 100 — the report renders "
        "these as percentages of net worth, so a set that does not sum to 100 "
        "misstates every drift figure on the page"
    )


def test_targets_are_set_on_sub_classes_not_top_level(fresh_db):
    """Targeting the leaves is what makes the sub-class rows render a real
    target. Top-level-only targeting leaves them at 0.00% with a warning
    triangle, which is the half-fix this seed deliberately avoids."""
    top_level_targeted = fresh_db.execute(
        """
        SELECT tc.name
        FROM risk_profile_allocations rpa
        JOIN taxonomy_classes tc ON tc.id = rpa.class_id
        WHERE tc.parent_id IS NULL
        """
    ).fetchall()
    assert not top_level_targeted, (
        f"top-level classes carry targets: {[r[0] for r in top_level_targeted]} — "
        "build_compass_allocation() sums child targets into the parent, so a "
        "target on both levels is double-counted"
    )


def test_active_profile_rolls_up_to_a_complete_top_level_allocation(fresh_db):
    """The number the Allocation Report actually shows for a top-level row."""
    rows = fresh_db.execute(
        """
        SELECT COALESCE(parent.name, tc.name) AS top_class, SUM(rpa.target_pct)
        FROM risk_profile_allocations rpa
        JOIN risk_profiles rp ON rp.id = rpa.profile_id AND rp.is_active = TRUE
        JOIN taxonomy_classes tc ON tc.id = rpa.class_id
        LEFT JOIN taxonomy_classes parent ON tc.parent_id = parent.id
        GROUP BY 1
        """
    ).fetchall()
    rolled = {r[0]: float(r[1]) for r in rows}
    assert abs(sum(rolled.values()) - 100.0) < 0.01, rolled
    for expected_class in ("Equity", "Fixed Income", "Cash"):
        assert rolled.get(expected_class, 0) > 0, (
            f"active profile has no {expected_class} target after roll-up: {rolled}"
        )


def test_every_targeted_class_name_exists_in_the_taxonomy(fresh_db):
    """A typo in ALLOCATIONS degrades to a silently missing target, so assert
    the names resolve rather than trusting the seed's own skip-and-warn."""
    known = {
        r[0] for r in fresh_db.execute("SELECT name FROM taxonomy_classes").fetchall()
    }
    for profile_name, allocations in ALLOCATIONS.items():
        unknown = set(allocations) - known
        assert not unknown, f"{profile_name} targets unknown classes: {sorted(unknown)}"


def test_seeding_twice_does_not_duplicate(fresh_db):
    profiles_before = fresh_db.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()[0]
    allocs_before = fresh_db.execute(
        "SELECT COUNT(*) FROM risk_profile_allocations"
    ).fetchone()[0]

    fresh_db.run_migrations()

    assert fresh_db.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()[0] == profiles_before
    assert (
        fresh_db.execute("SELECT COUNT(*) FROM risk_profile_allocations").fetchone()[0]
        == allocs_before
    )


def test_seed_never_steals_activation_from_an_existing_profile(tmp_path):
    """The owner's database has four hand-entered profiles with one active.
    The seed adds what is missing; it does not campaign for its own default."""
    db = DatabaseConnector(str(tmp_path / "owner.duckdb"))
    bootstrap_database(db)

    # Simulate the owner's shape: their own profile, active, seeds not yet run.
    db.execute("TRUNCATE risk_profile_allocations")
    db.execute("TRUNCATE risk_profiles")
    db.execute("DELETE FROM schema_version WHERE version = 193")
    db.execute(
        "INSERT INTO risk_profiles (id, name, is_active) VALUES (99, '均衡型', TRUE)"
    )

    db.run_migrations()

    active = db.execute(
        "SELECT name FROM risk_profiles WHERE is_active = TRUE"
    ).fetchall()
    assert [r[0] for r in active] == ["均衡型"], (
        f"seed changed the active profile to {[r[0] for r in active]} — an "
        "owner's choice must outrank the seed default"
    )

    # And it adds nothing at all. Only a database with *no* profiles has the bug
    # this seed fixes; one that already has them has already answered the
    # question, and four extra English profiles appearing in a live instance is
    # clutter, not a fix.
    names = [r[0] for r in db.execute("SELECT name FROM risk_profiles").fetchall()]
    assert names == ["均衡型"], (
        f"seed added profiles to a database that already had one: {names}"
    )
    db.close()


def test_seed_is_a_no_op_on_any_database_that_already_has_profiles(tmp_path):
    """The deploy-safety property, stated on its own.

    V192 (taxonomy) adds missing classes to *any* database, because a missing
    class silently breaks classification. V193 must not: a missing profile
    breaks nothing once one exists, so this migration has to be invisible to
    every already-populated instance.
    """
    db = DatabaseConnector(str(tmp_path / "populated.duckdb"))
    bootstrap_database(db)

    db.execute("TRUNCATE risk_profile_allocations")
    db.execute("TRUNCATE risk_profiles")
    db.execute(
        "INSERT INTO risk_profiles (id, name, is_active) VALUES (7, 'My Own', FALSE)"
    )
    db.execute("DELETE FROM schema_version WHERE version = 193")

    before = db.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()[0]
    db.run_migrations()
    after = db.execute("SELECT COUNT(*) FROM risk_profiles").fetchone()[0]

    assert before == after == 1, f"seed added {after - before} profile(s)"
    assert (
        db.execute("SELECT COUNT(*) FROM risk_profile_allocations").fetchone()[0] == 0
    ), "seed added allocations to a database it should not have touched"
    db.close()


def test_existing_profile_names_are_left_alone(tmp_path):
    """Re-running the seed must not rewrite a profile a person has edited."""
    db = DatabaseConnector(str(tmp_path / "edited.duckdb"))
    bootstrap_database(db)

    db.execute(
        "UPDATE risk_profiles SET description = 'edited by hand' WHERE name = 'Balanced'"
    )
    db.execute("DELETE FROM schema_version WHERE version = 193")

    db.run_migrations()

    desc = db.execute(
        "SELECT description FROM risk_profiles WHERE name = 'Balanced'"
    ).fetchone()[0]
    assert desc == "edited by hand", "seed overwrote an existing profile's description"
    db.close()
