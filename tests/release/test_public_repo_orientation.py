"""Documentation written for public-repo readers must be published to them.

Three consecutive review rounds were spent on reporting rather than code, and
the third had a distinct cause worth pinning down. A note explaining that this
project publishes to a *generated* repository — one squashed commit, rewritten
each release, with several referenced paths deliberately absent — was written
specifically so an outside reviewer could tell "private by design" from "broken
link". It was then filed in `HANDOVER.md` and `docs/project-status.md`, neither
of which is in `tools/release/export_manifest.txt`. It 404'd for the only people
it was written for, and a reviewer had to ask where it went.

Naming a destination file is not the same as checking that destination is
visible to the intended reader. A plan can encode that error as easily as an
execution can — this one did.

So the explanation now lives in `CONTRIBUTING.md`, and these tests make that
placement load-bearing: they fail if it is removed, or if `CONTRIBUTING.md` ever
drops out of the export allowlist and takes the explanation private again.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "tools" / "release" / "export_manifest.txt"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

pytestmark = pytest.mark.skipif(
    not MANIFEST.is_file(), reason="export manifest absent (public export tree)"
)


@pytest.fixture(scope="module")
def manifest_paths() -> set[str]:
    return {
        line.strip()
        for line in MANIFEST.read_text().splitlines()
        if line.strip() and not line.startswith("#") and not line.startswith("EXCLUDE ")
    }


@pytest.fixture(scope="module")
def contributing() -> str:
    return CONTRIBUTING.read_text()


def test_contributing_is_exported(manifest_paths):
    """Anti-vacuity: the assertions below only mean something while this file
    actually reaches the public repo."""
    assert "CONTRIBUTING.md" in manifest_paths, (
        "CONTRIBUTING.md left the export allowlist — the orientation note it "
        "carries is now private again, which is the exact failure this file exists "
        "to prevent"
    )


def test_contributing_explains_the_squashed_history(contributing):
    """A reviewer seeing one commit must be able to learn that this is normal,
    from the repo itself, without asking."""
    lowered = contributing.lower()
    assert "one commit" in lowered or "single fresh commit" in lowered, contributing[-2000:]
    assert "date" in lowered, "the freshness signal (the date, not the count) is unexplained"


def test_contributing_warns_that_private_shas_do_not_resolve(contributing):
    """The specific thing that cost two review cycles: a SHA cited from the
    private repo, checked against this one."""
    lowered = contributing.lower()
    assert "sha" in lowered, contributing[-2000:]
    assert "not resolve" in lowered or "will not resolve" in lowered, (
        "nothing tells a reviewer that a quoted SHA may simply be from the other "
        "repository"
    )


def test_contributing_distinguishes_absent_by_design_from_broken(contributing):
    """The distinction the note exists to make. Without it, 'this file is not
    here' is indistinguishable from 'something is broken', and the only way to
    resolve it is to ask a human."""
    lowered = contributing.lower()
    assert "404" in lowered or "do not exist here" in lowered, contributing[-2000:]
    assert "design" in lowered, "nothing marks the absent paths as deliberate"


def test_private_only_docs_are_not_in_the_export(manifest_paths):
    """Guards the other half: these are the paths the note tells readers to
    expect to be missing. If one is ever exported, the note becomes wrong."""
    for private_path in (
        "HANDOVER.md",
        "docs/project-status.md",
        "docs/known-issues.md",
        "docs/plans",
        "docs/marketing",
    ):
        assert private_path not in manifest_paths, (
            f"{private_path} is now exported, so CONTRIBUTING.md's claim that it "
            "is absent by design is false — update the note or the manifest"
        )
