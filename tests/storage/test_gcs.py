from pathlib import Path
from typing import Optional, List, Tuple

import pytest

from src.storage import gcs


class FakeBlob:
    def __init__(self, name: str, exists: bool = True, generation: Optional[int] = 7, upload_error: Optional[Exception] = None):
        self.name = name
        self._exists = exists
        self.generation = generation
        self.upload_error = upload_error
        self.download_targets: List[str] = []
        self.upload_calls: List[Tuple[str, Optional[int]]] = []

    def exists(self) -> bool:
        return self._exists

    def download_to_filename(self, filename: str) -> None:
        self.download_targets.append(filename)

    def upload_from_filename(self, filename: str, if_generation_match: Optional[int] = None) -> None:
        if self.upload_error is not None:
            raise self.upload_error
        self.upload_calls.append((filename, if_generation_match))


class FakeBucket:
    def __init__(self):
        self.blobs: dict[str, FakeBlob] = {}
        self.listed_blobs: list[FakeBlob] = []

    def blob(self, name: str) -> FakeBlob:
        if name not in self.blobs:
            self.blobs[name] = FakeBlob(name=name)
        return self.blobs[name]

    def get_blob(self, name: str) -> Optional[FakeBlob]:
        """Like bucket.blob() but returns None when blob does not exist (mirrors GCS SDK)."""
        b = self.blobs.get(name)
        if b is None or not b._exists:
            return None
        return b

    def list_blobs(self, prefix: str):
        assert prefix == "sources/"
        return self.listed_blobs


class FakeClient:
    def __init__(self, bucket: FakeBucket):
        self.bucket_obj = bucket
        self.bucket_calls: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.bucket_calls.append(name)
        return self.bucket_obj


def test_download_db_from_gcs_returns_false_when_blob_missing(monkeypatch, tmp_path):
    bucket = FakeBucket()
    bucket.blobs["db/unified.duckdb"] = FakeBlob(name="db/unified.duckdb", exists=False)
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    result = gcs.download_db_from_gcs("demo-bucket", str(tmp_path / "unified.duckdb"))

    assert result is False
    assert bucket.blobs["db/unified.duckdb"].download_targets == []


def test_download_db_from_gcs_downloads_when_blob_exists(monkeypatch, tmp_path):
    bucket = FakeBucket()
    blob = FakeBlob(name="db/unified.duckdb", exists=True)
    bucket.blobs["db/unified.duckdb"] = blob
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    dest = tmp_path / "unified.duckdb"
    result = gcs.download_db_from_gcs("demo-bucket", str(dest))

    assert result is True
    assert blob.download_targets == [str(dest)]


def test_download_sources_from_gcs_preserves_relative_paths(monkeypatch, tmp_path):
    bucket = FakeBucket()
    root_marker = FakeBlob(name="sources/", exists=True)
    blob_one = FakeBlob(name="sources/schwab/positions.csv", exists=True)
    blob_two = FakeBlob(name="sources/cn_fund/funds/latest.xlsx", exists=True)
    bucket.listed_blobs = [root_marker, blob_one, blob_two]
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    downloaded = gcs.download_sources_from_gcs("demo-bucket", str(tmp_path))

    assert downloaded == [
        str(tmp_path / "schwab" / "positions.csv"),
        str(tmp_path / "cn_fund" / "funds" / "latest.xlsx"),
    ]
    assert blob_one.download_targets == [str(tmp_path / "schwab" / "positions.csv")]
    assert blob_two.download_targets == [str(tmp_path / "cn_fund" / "funds" / "latest.xlsx")]


def test_upload_db_to_gcs_writes_backup_then_canonical_with_precondition(monkeypatch, tmp_path):
    local_db = tmp_path / "unified.duckdb"
    local_db.write_text("content", encoding="utf-8")

    bucket = FakeBucket()
    canonical_blob = FakeBlob(name="db/unified.duckdb", exists=True, generation=42)
    backup_blob = FakeBlob(name="backups/placeholder", exists=False)
    bucket.blobs["db/unified.duckdb"] = canonical_blob

    def _blob(name: str) -> FakeBlob:
        if name.startswith("backups/"):
            bucket.blobs[name] = backup_blob
            backup_blob.name = name
            return backup_blob
        return bucket.blobs[name]

    bucket.blob = _blob  # type: ignore[method-assign]
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    result = gcs.upload_db_to_gcs("demo-bucket", str(local_db))

    assert result.success is True
    assert backup_blob.name.startswith("backups/")
    assert backup_blob.name.endswith("_unified.duckdb")
    assert backup_blob.upload_calls == [(str(local_db), None)]
    assert canonical_blob.upload_calls == [(str(local_db), 42)]


def test_upload_db_to_gcs_retries_on_precondition_failed_and_succeeds(monkeypatch, tmp_path):
    """On PreconditionFailed, upload_db_to_gcs must re-fetch generation and retry once.
    If the retry succeeds, the function returns GCSUploadResult(success=True).
    The old behaviour (silently returning None) is GONE — that was the bug.
    """
    class PreconditionFailed(Exception):
        pass

    local_db = tmp_path / "unified.duckdb"
    local_db.write_text("content", encoding="utf-8")

    # The canonical blob starts with generation=9 and raises on first upload attempt.
    # After a simulated concurrent write, get_blob() returns generation=10 on the retry call.
    canonical_blob = FakeBlob(
        name="db/unified.duckdb",
        exists=True,
        generation=9,
        upload_error=PreconditionFailed("stale generation"),
    )
    # Retry blob (fresh generation=10) succeeds on upload
    retry_blob = FakeBlob(name="db/unified.duckdb", exists=True, generation=10)

    get_blob_calls: list[str] = []
    blob_calls: list[str] = []

    class RetryBucket(FakeBucket):
        def get_blob(self, name: str):
            get_blob_calls.append(name)
            if len(get_blob_calls) == 1:
                return canonical_blob  # stale generation on first check
            return retry_blob  # updated generation on retry

        def blob(self, name: str):
            blob_calls.append(name)
            if name.startswith("backups/"):
                b = FakeBlob(name=name, exists=False)
                self.blobs[name] = b
                return b
            # First blob("db/unified.duckdb") call → canonical (will raise PreconditionFailed)
            # Second call → retry blob (will succeed)
            canonical_calls = [c for c in blob_calls if c == "db/unified.duckdb"]
            if len(canonical_calls) == 1:
                return canonical_blob
            return retry_blob

    bucket = RetryBucket()
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    result = gcs.upload_db_to_gcs("demo-bucket", str(local_db))

    assert result.success is True
    # Two get_blob() calls: once before first attempt, once before retry
    assert len(get_blob_calls) == 2, f"Expected 2 get_blob calls, got {len(get_blob_calls)}"
    # Retry blob was called with the refreshed generation
    assert any(call[1] == 10 for call in retry_blob.upload_calls), (
        f"Expected retry with generation=10, got upload_calls={retry_blob.upload_calls}"
    )


def test_upload_db_to_gcs_raises_on_persistent_precondition_failed(monkeypatch, tmp_path):
    """If the retry also fails with PreconditionFailed, the exception must propagate — no silent success."""
    class PreconditionFailed(Exception):
        pass

    local_db = tmp_path / "unified.duckdb"
    local_db.write_text("content", encoding="utf-8")

    # Both first attempt and retry raise PreconditionFailed
    always_failing_blob = FakeBlob(
        name="db/unified.duckdb",
        exists=True,
        generation=9,
        upload_error=PreconditionFailed("still stale"),
    )

    class AlwaysFailBucket(FakeBucket):
        def get_blob(self, name: str):
            return always_failing_blob

        def blob(self, name: str):
            if name.startswith("backups/"):
                b = FakeBlob(name=name, exists=False)
                self.blobs[name] = b
                return b
            return always_failing_blob

    bucket = AlwaysFailBucket()
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    with pytest.raises(PreconditionFailed):
        gcs.upload_db_to_gcs("demo-bucket", str(local_db))


def test_upload_source_to_gcs_uses_reader_prefix(monkeypatch, tmp_path):
    local_file = tmp_path / "Schwab-2026-04-08.csv"
    local_file.write_text("x", encoding="utf-8")

    bucket = FakeBucket()
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    gcs.upload_source_to_gcs("demo-bucket", "schwab", str(local_file))

    blob = bucket.blobs["sources/schwab/Schwab-2026-04-08.csv"]
    assert blob.upload_calls == [(str(local_file), None)]


# ── Program OSR WS-4b: reference_sheet.yaml / verification.yaml restore ────────

def test_download_reference_sheet_from_gcs_returns_false_when_blob_missing(monkeypatch, tmp_path):
    bucket = FakeBucket()
    bucket.blobs["config/reference_sheet.yaml"] = FakeBlob(
        name="config/reference_sheet.yaml", exists=False
    )
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    result = gcs.download_reference_sheet_from_gcs("demo-bucket", str(tmp_path / "reference_sheet.yaml"))

    assert result is False
    assert bucket.blobs["config/reference_sheet.yaml"].download_targets == []


def test_download_reference_sheet_from_gcs_downloads_when_blob_exists(monkeypatch, tmp_path):
    bucket = FakeBucket()
    blob = FakeBlob(name="config/reference_sheet.yaml", exists=True)
    bucket.blobs["config/reference_sheet.yaml"] = blob
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    dest = tmp_path / "reference_sheet.yaml"
    result = gcs.download_reference_sheet_from_gcs("demo-bucket", str(dest))

    assert result is True
    assert blob.download_targets == [str(dest)]


def test_download_verification_from_gcs_returns_false_when_blob_missing(monkeypatch, tmp_path):
    bucket = FakeBucket()
    bucket.blobs["config/verification.yaml"] = FakeBlob(
        name="config/verification.yaml", exists=False
    )
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    result = gcs.download_verification_from_gcs("demo-bucket", str(tmp_path / "verification.yaml"))

    assert result is False
    assert bucket.blobs["config/verification.yaml"].download_targets == []


def test_download_verification_from_gcs_downloads_when_blob_exists(monkeypatch, tmp_path):
    bucket = FakeBucket()
    blob = FakeBlob(name="config/verification.yaml", exists=True)
    bucket.blobs["config/verification.yaml"] = blob
    client = FakeClient(bucket)
    monkeypatch.setattr(gcs, "_get_client", lambda: client)

    dest = tmp_path / "verification.yaml"
    result = gcs.download_verification_from_gcs("demo-bucket", str(dest))

    assert result is True
    assert blob.download_targets == [str(dest)]


# ── Generated reference workbook round-trip ──────────────────────────────────

class TestReferenceDataRoundTrip:
    """The generated UIS_Reference_Data.xlsx must survive the cloud round-trip.

    Regression: on Cloud Run `finance_dir` is overridden to /tmp/sources, so
    export_reference_sheet wrote every refresh into ephemeral container storage.
    Nothing carried it anywhere, so the workbook the owner's spreadsheet links
    to silently froze for five weeks. These pin both halves of the delivery.
    """

    def test_upload_returns_false_when_nothing_to_upload(self, tmp_path, monkeypatch):
        from src.storage import gcs
        monkeypatch.setattr(gcs, "_get_client", lambda: pytest.fail(
            "must not contact GCS when the file is absent"))
        assert gcs.upload_reference_data_to_gcs("b", str(tmp_path / "nope.xlsx")) is False

    def test_upload_uses_the_exports_blob(self, tmp_path, monkeypatch):
        from src.storage import gcs
        src = tmp_path / "UIS_Reference_Data.xlsx"
        src.write_bytes(b"xlsx")
        seen = {}

        class _Blob:
            def upload_from_filename(self, path): seen["path"] = path

        class _Bucket:
            def blob(self, name):
                seen["blob"] = name
                return _Blob()

        monkeypatch.setattr(gcs, "_get_client", lambda: type("C", (), {"bucket": lambda s, n: _Bucket()})())
        assert gcs.upload_reference_data_to_gcs("b", str(src)) is True
        assert seen["blob"] == "exports/UIS_Reference_Data.xlsx"

    def test_download_absent_leaves_existing_file_untouched(self, tmp_path, monkeypatch):
        """A missing cloud copy must not destroy a stale-but-valid local one."""
        from src.storage import gcs
        dest = tmp_path / "UIS_Reference_Data.xlsx"
        dest.write_bytes(b"existing")

        class _Blob:
            def exists(self): return False

        class _Bucket:
            def blob(self, name): return _Blob()

        monkeypatch.setattr(gcs, "_get_client", lambda: type("C", (), {"bucket": lambda s, n: _Bucket()})())
        assert gcs.download_reference_data_from_gcs("b", str(dest)) is False
        assert dest.read_bytes() == b"existing"

    def test_download_is_atomic_and_leaves_no_part_file(self, tmp_path, monkeypatch):
        from src.storage import gcs
        dest = tmp_path / "UIS_Reference_Data.xlsx"
        dest.write_bytes(b"old")

        class _Blob:
            def exists(self): return True
            def download_to_filename(self, path): Path(path).write_bytes(b"new")

        class _Bucket:
            def blob(self, name): return _Blob()

        monkeypatch.setattr(gcs, "_get_client", lambda: type("C", (), {"bucket": lambda s, n: _Bucket()})())
        assert gcs.download_reference_data_from_gcs("b", str(dest)) is True
        assert dest.read_bytes() == b"new"
        assert not list(tmp_path.glob("*.part")), "temp file must not be left behind"

    def test_failed_download_does_not_clobber_the_local_workbook(self, tmp_path, monkeypatch):
        from src.storage import gcs
        dest = tmp_path / "UIS_Reference_Data.xlsx"
        dest.write_bytes(b"old")

        class _Blob:
            def exists(self): return True
            def download_to_filename(self, path):
                Path(path).write_bytes(b"trunc")
                raise OSError("connection reset mid-download")

        class _Bucket:
            def blob(self, name): return _Blob()

        monkeypatch.setattr(gcs, "_get_client", lambda: type("C", (), {"bucket": lambda s, n: _Bucket()})())
        with pytest.raises(OSError):
            gcs.download_reference_data_from_gcs("b", str(dest))
        assert dest.read_bytes() == b"old", "a torn download must not replace the good file"
        assert not list(tmp_path.glob("*.part"))
