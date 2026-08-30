"""Structural guard: nothing under src/ raw-`open()`s a real config path.

`src/config.py::_resolve_config_file` is the ONE place that decides which
config file to read. It carries two behaviours nothing else has: the fallback
to the committed `.example` template (so `git clone && quickstart` works for a
newcomer who cannot have the owner's gitignored config), and the cloud
fail-fast guard (so a Cloud Run instance refuses to boot on the template
rather than quietly serving production from it).

Any code that opens "config/<name>.yaml" directly bypasses BOTH. Program OSR
untracked the real config files, which turned every such bypass into a
fresh-clone breakage overnight — and they were invisible locally, because a
developer's working tree still has the real files. CI is the only environment
that sees the committed set. Four sites existed when this guard was written:

  - src/validation/run_reader_validation.py — its own shadow `load_config()`,
    reached from the on-demand-audit route, so the endpoint 500'd. This is the
    one that failed CI and blocked the V7.9.1 deploy.
  - src/services/llm_client.py — raised on construction, killing the advisor.
  - src/services/settings_manager.py — 500 from the settings endpoints.
  - src/api/routes/sentiment.py — swallowed by a bare `except`, so it degraded
    silently with no FRED key. The quietest and the worst.

This is the anti-drift ratchet (same house style as
tests/services/test_goal_resolver_structural_guard.py): the next such bypass
fails here, at desk, instead of at deploy time.

Uses an AST scan rather than a grep so it flags real `open()` calls and not
the many docstrings, comments and log messages that legitimately name
"config/settings.yaml" in prose.
"""
from __future__ import annotations

import ast
from pathlib import Path

# The gitignored configs — the ONLY ones that can be absent from a clone, and
# so the only ones that need the resolver. Kept in step with .gitignore by
# test_guarded_set_matches_the_committed_templates below. Other config/*.yaml
# files (source_authority, thresholds, canonical_underlyings, readers/) are
# tracked and present everywhere, so opening those directly is fine.
_GUARDED_CONFIGS = frozenset({
    "config/settings.yaml",
    "config/reference_sheet.yaml",
    "config/verification.yaml",
})

# The ONE resolver. It must open the real path — that is its whole job.
#
# Nothing else belongs here. In particular src/services/settings_manager.py is
# deliberately NOT allowlisted: its reads go through `_settings_read_path()`
# (which delegates to the resolver), and its writes address `SETTINGS_PATH`, a
# Path built from `__file__` rather than a literal, so they never match this
# guard. Writes MUST keep targeting the real file — see the docstring on
# `_settings_read_path` for why resolving a write target would overwrite the
# committed template and publish it to GCS as production config.
_ALLOWED_FILES = {
    # The resolver itself. Opening the real path is its whole job.
    "src/config.py": "the ONE resolver",
    # Read-modify-WRITE: the import wizard merges a new reader into
    # settings.yaml and writes it back (_atomic_yaml_write at the same path).
    # Resolving its path would make it read the template and then write the
    # merged result over the committed template. Same asymmetry as
    # settings_manager's writers — the write target must stay the real file.
    # Its read half is a candidate for the resolver; tracked as follow-up
    # rather than changed alongside a deploy fix.
    "src/import_adapters/reader_generator.py": "read-modify-write, addresses the real file",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Passing a path through one of these IS the correct thing to do, so
# `open(_resolve_config_file(Path("config/settings.yaml")))` is not a bypass.
_RESOLVER_NAMES = frozenset({"_resolve_config_file", "load_config"})


def _contains_config_path_literal(node: ast.AST) -> bool:
    """True if this subtree contains a real-config path string constant.

    Walking the subtree (rather than checking the argument directly) catches
    the wrapped forms too: `open(Path("config/settings.yaml"))`,
    `open(os.path.join("config/settings.yaml"))`.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value in _GUARDED_CONFIGS:
                return True
    return False


def _goes_through_resolver(node: ast.AST) -> bool:
    """True if this subtree routes the path through the canonical resolver."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in _RESOLVER_NAMES:
                return True
    return False


def _raw_config_open_sites(src_dir: Path) -> dict[str, list[int]]:
    """Return {relative_path: [line numbers]} for every `open(...)` call whose
    first argument names a real config file."""
    root = src_dir.parent
    hits: dict[str, list[int]] = {}

    for path in sorted(src_dir.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWED_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - src/ must always parse
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_open = (isinstance(func, ast.Name) and func.id == "open") or (
                isinstance(func, ast.Attribute) and func.attr == "open"
            )
            if not is_open or not node.args:
                continue
            arg = node.args[0]
            if _contains_config_path_literal(arg) and not _goes_through_resolver(arg):
                hits.setdefault(rel, []).append(node.lineno)

        # Second form: the path hides in a function's DEFAULT argument and is
        # opened later via the parameter name, so the `open()` call site has no
        # literal to match. This is exactly how src/services/llm_client.py's
        # bypass stayed invisible — `def __init__(self, settings_path: str =
        # "config/settings.yaml")` — until a fresh clone hit it.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # …unless the function resolves it before use, which is the fix.
            if _goes_through_resolver(node):
                continue
            defaults = list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]
            for default in defaults:
                if _contains_config_path_literal(default):
                    hits.setdefault(rel, []).append(default.lineno)

    # ast.walk is breadth-first, so raw append order is not source order.
    return {rel: sorted(lines) for rel, lines in hits.items()}


def test_no_raw_config_open_outside_the_resolver():
    """Every config read under src/ goes through src/config.py."""
    src_dir = _project_root() / "src"
    hits = _raw_config_open_sites(src_dir)

    assert hits == {}, (
        "These files open a real config path directly instead of going through "
        "src/config.py::_resolve_config_file, so they lose the .example "
        "fallback (breaking a fresh clone) and the cloud fail-fast guard:\n"
        + "\n".join(
            f"  {rel}:{','.join(str(n) for n in lines)}" for rel, lines in sorted(hits.items())
        )
    )


def test_guarded_set_matches_the_committed_templates():
    """`_GUARDED_CONFIGS` must track the configs that can actually be absent.

    A config is absent-able exactly when it ships as a `.example` template
    instead of the real file. If someone adds a fourth such config and forgets
    this set, the guard would silently stop covering it — the same silent-gap
    failure it exists to prevent.
    """
    config_dir = _project_root() / "config"
    from_disk = {
        f"config/{p.name.replace('.example', '')}"
        for p in sorted(config_dir.glob("*.example.yaml"))
    }

    assert from_disk == set(_GUARDED_CONFIGS), (
        "Committed .example templates and _GUARDED_CONFIGS disagree.\n"
        f"  on disk : {sorted(from_disk)}\n"
        f"  in guard: {sorted(_GUARDED_CONFIGS)}"
    )


def test_guard_detects_a_planted_bypass(tmp_path):
    """Anti-vacuity: the scan must actually catch a bypass.

    Without this, deleting the detector body or tightening the regex to match
    nothing would leave `test_no_raw_config_open_outside_the_resolver` passing
    forever on an empty result — a green test that guards nothing.
    """
    src_dir = tmp_path / "src"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "sub" / "offender.py").write_text(
        'import yaml\n'
        'from pathlib import Path\n'
        'def load():\n'
        '    with open("config/settings.yaml") as f:\n'
        '        return yaml.safe_load(f)\n'
        'def load_wrapped():\n'
        '    return open(Path("config/verification.yaml"))\n',
        encoding="utf-8",
    )
    # A file that names the path only in prose must NOT be flagged.
    (src_dir / "sub" / "innocent.py").write_text(
        '"""Reads config/settings.yaml via the resolver."""\n'
        'from src.config import load_config\n'
        'def load():\n'
        '    return load_config()  # config/settings.yaml\n',
        encoding="utf-8",
    )
    # Opening the template itself is not a bypass.
    (src_dir / "sub" / "template_reader.py").write_text(
        'def load():\n'
        '    return open("config/settings.example.yaml")\n',
        encoding="utf-8",
    )
    # Routing the literal through the resolver is the CORRECT form.
    (src_dir / "sub" / "resolved.py").write_text(
        'from pathlib import Path\n'
        'from src.config import _resolve_config_file\n'
        'def load():\n'
        '    return open(_resolve_config_file(Path("config/settings.yaml")))\n',
        encoding="utf-8",
    )

    hits = _raw_config_open_sites(src_dir)

    assert set(hits) == {"src/sub/offender.py"}, hits
    assert hits["src/sub/offender.py"] == [4, 7], hits
