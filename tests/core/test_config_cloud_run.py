from pathlib import Path

import pytest
import yaml

from src.config import load_config


def test_load_config_overrides_finance_dir_and_reader_data_dirs(tmp_path, monkeypatch):
    config_path = tmp_path / "settings.yaml"
    config_payload = {
        "finance_dir": "/original/finance",
        "source_registry": {
            "schwab": {"data_dir": "/old/schwab"},
            "cn_fund": {"data_dir": "/old/cn_fund"},
        },
    }
    config_path.write_text(yaml.safe_dump(config_payload), encoding="utf-8")

    override_dir = str(tmp_path / "uploads")
    monkeypatch.setenv("UIS_FINANCE_DIR", override_dir)

    config = load_config(str(config_path))

    assert config["finance_dir"] == override_dir
    assert config["source_registry"]["schwab"]["data_dir"] == str(Path(override_dir) / "schwab")
    assert config["source_registry"]["cn_fund"]["data_dir"] == str(Path(override_dir) / "cn_fund")


def _write_example(tmp_path):
    """A `.example` twin next to a missing real config — the fallback's precondition."""
    real = tmp_path / "settings.yaml"
    example = tmp_path / "settings.example.yaml"
    example.write_text(yaml.safe_dump({"finance_dir": "./data/import"}), encoding="utf-8")
    return real, example


def test_local_run_falls_back_to_the_example(tmp_path, monkeypatch):
    """`git clone && quickstart` must work without a config the newcomer cannot have."""
    real, _ = _write_example(tmp_path)
    monkeypatch.delenv("UIS_GCS_BUCKET", raising=False)

    config = load_config(str(real))

    assert config["finance_dir"] == "./data/import"


def test_cloud_run_refuses_to_fall_back(tmp_path, monkeypatch):
    """In cloud mode a missing real config must fail the boot, not silently
    serve the committed template.

    Before the open-source split settings.yaml was tracked and baked into the
    image; it is now restored from GCS at startup. A fallback here would run
    production on the template — different finance_dir, template prompts, and
    most of the owner's config blocks absent — announced by one log line.
    Cloud Run keeps the previous revision serving when a new one fails to
    start, so raising is the cheaper failure.
    """
    real, _ = _write_example(tmp_path)
    monkeypatch.setenv("UIS_GCS_BUCKET", "some-bucket")

    with pytest.raises(FileNotFoundError) as exc:
        load_config(str(real))

    message = str(exc.value)
    assert "Refusing to start" in message
    assert "some-bucket" in message, "the error must name the bucket to check"
    # The blob path must match src/storage/gcs.py's actual `config/<name>` prefix.
    # An error that sends the operator to the wrong GCS path is worse than none.
    assert "gs://some-bucket/config/settings.yaml" in message


def test_cloud_run_still_loads_a_real_config(tmp_path, monkeypatch):
    """The guard must not fire when the real file IS present in cloud mode."""
    real, _ = _write_example(tmp_path)
    real.write_text(yaml.safe_dump({"finance_dir": "/real/finance"}), encoding="utf-8")
    monkeypatch.setenv("UIS_GCS_BUCKET", "some-bucket")

    assert load_config(str(real))["finance_dir"] == "/real/finance"
