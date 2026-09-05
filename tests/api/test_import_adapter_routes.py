import functools
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

import src.import_adapters.reader_generator as reader_generator
from src.api.main import app


def _init_db(db_path: Path):
    conn = duckdb.connect(str(db_path))
    conn.execute(Path("src/database/schema.sql").read_text(encoding="utf-8"))
    conn.close()


def _redirect_generated_artifacts(monkeypatch, tmp_path: Path) -> None:
    """Point the reader generator's file writes at tmp_path.

    `ImportAdapterApproveRequest.generate_reader` defaults to True, so hitting
    the approve route runs the real `generate_reader_artifacts`, whose output
    paths are *function defaults* — `config/readers/`, `config/settings.yaml`,
    `config/source_authority.yaml`, `data/import_adapters/`. Relative paths, so
    they resolve against the repo working tree.

    On 2026-08-30 this test wrote `{pattern: 'US_*', authority: 'Adapter_Demo',
    priority: 3}` into the real, git-tracked `config/source_authority.yaml`. The
    injected rule outranked `US_STK_*`/`US_ETF_*`, which broke two co-authority
    tombstone tests in a completely unrelated file — and the corrupted config
    was staged for commit. pyyaml also round-tripped the file, stripping its
    quoting and blank lines, so the diff looked like a wholesale rewrite.

    The route imports `generate_reader_artifacts` inside the handler, so it
    resolves from this module at call time and monkeypatching here takes effect.
    This wraps rather than stubs: the real generator still runs, it just writes
    somewhere disposable. Artifact *content* is covered separately by
    tests/import_adapters/test_reader_generator.py, which injects the same paths.
    """
    monkeypatch.setattr(
        reader_generator,
        "generate_reader_artifacts",
        functools.partial(
            reader_generator.generate_reader_artifacts,
            config_readers_dir=tmp_path / "config" / "readers",
            settings_path=tmp_path / "config" / "settings.yaml",
            authority_path=tmp_path / "config" / "source_authority.yaml",
            data_dir_root=tmp_path / "data" / "import_adapters",
        ),
    )


def test_import_adapter_routes(tmp_path: Path, monkeypatch):
    db = tmp_path / "test.duckdb"
    _init_db(db)
    monkeypatch.setenv("UIS_DB_PATH", str(db))
    _redirect_generated_artifacts(monkeypatch, tmp_path)
    client = TestClient(app)

    upload = client.post(
        "/settings/import-adapters/demo/upload?import_type=holdings",
        files={"file": ("h.csv", b"asset_id,snapshot_date,quantity,market_value,currency\nUS_AAPL,2026-05-09,1,1000,CNY\n", "text/csv")},
    )
    assert upload.status_code == 200
    run_id = upload.json()["run_id"]

    configure = client.post("/settings/import-adapters/demo/configure", json={"run_id": run_id, "column_mapping": upload.json()["inferred_mapping"]})
    assert configure.status_code == 200

    validate = client.post("/settings/import-adapters/demo/validate", json={"run_id": run_id})
    assert validate.status_code == 200

    stage = client.post("/settings/import-adapters/demo/stage", json={"run_id": run_id})
    assert stage.status_code == 200

    approve = client.post("/settings/import-adapters/demo/approve", json={"source_system": "Adapter_Demo", "asset_prefixes": ["US_"], "authority_priority": 3})
    assert approve.status_code == 200

    ls = client.get("/settings/import-adapters")
    assert ls.status_code == 200
    assert ls.json()["adapters"][0]["source_system"] == "Adapter_Demo"

    # The redirect above must stay a redirect, not become a stub. If someone
    # replaces the wrapper with a no-op the route still returns 200 and every
    # assertion above still passes — so pin the evidence that generation ran,
    # and that it ran *here*.
    assert (tmp_path / "config" / "source_authority.yaml").exists(), (
        "generate_reader_artifacts did not run — the approve route's "
        "generate_reader path is no longer exercised by this test"
    )
    assert (tmp_path / "config" / "readers" / "demo.yaml").exists()

    # And the real tree is untouched: the rule went to the temp copy only.
    real_authority = Path("config/source_authority.yaml").read_text(encoding="utf-8")
    assert "Adapter_Demo" not in real_authority, (
        "the adapter wizard wrote into the repository's real source_authority.yaml"
    )
