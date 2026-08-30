from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_client():
    from google.cloud import storage  # noqa: PLC0415

    return storage.Client()


def _is_precondition_failed(exc: Exception) -> bool:
    return exc.__class__.__name__ == "PreconditionFailed"


def download_db_from_gcs(bucket_name: str, local_path: str) -> bool:
    """Download canonical DuckDB from GCS. Returns False when blob does not exist."""
    logger.info("Downloading database from gs://%s/db/unified.duckdb to %s", bucket_name, local_path)
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("db/unified.duckdb")

    if not blob.exists():
        logger.info("No canonical database found in bucket %s (first deploy).", bucket_name)
        return False

    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(local_path)
    logger.info("Downloaded database to %s", local_path)
    return True


class GCSUploadResult:
    """Structured return value for upload_db_to_gcs so callers can distinguish success from failure."""

    def __init__(self, *, success: bool, generation: int | None = None, error: str | None = None) -> None:
        self.success = success
        self.generation = generation  # GCS object generation after a successful upload
        self.error = error  # Human-readable failure reason

    def __repr__(self) -> str:
        if self.success:
            return f"GCSUploadResult(success=True, generation={self.generation})"
        return f"GCSUploadResult(success=False, error={self.error!r})"


def upload_db_to_gcs(bucket_name: str, local_path: str) -> GCSUploadResult:
    """Upload timestamped backup then canonical DB object with generation precondition.

    Returns a GCSUploadResult.  On PreconditionFailed (concurrent write / generation mismatch)
    the function re-fetches the current generation and retries ONCE.  If the retry also fails, or
    if any other exception is raised, it propagates — callers must not treat a raised exception
    as a silent no-op.

    The timestamped backup is always uploaded first and is NOT subject to the precondition; its
    presence on GCS is guaranteed regardless of whether the canonical upload succeeds.
    """
    logger.info("Uploading database %s to bucket %s", local_path, bucket_name)
    client = _get_client()
    bucket = client.bucket(bucket_name)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"backups/{timestamp}_unified.duckdb"
    backup_blob = bucket.blob(backup_name)
    backup_blob.upload_from_filename(local_path)
    logger.info("Uploaded DB backup to gs://%s/%s", bucket_name, backup_name)

    # get_blob() returns a fully-loaded Blob (with .generation) or None if missing
    existing = bucket.get_blob("db/unified.duckdb")
    generation_match = existing.generation if existing is not None else 0
    canonical_blob = bucket.blob("db/unified.duckdb")

    try:
        canonical_blob.upload_from_filename(local_path, if_generation_match=generation_match)
        uploaded_generation = canonical_blob.generation
        logger.info(
            "Uploaded canonical DB to gs://%s/db/unified.duckdb (generation=%s)",
            bucket_name,
            uploaded_generation,
        )
        return GCSUploadResult(success=True, generation=uploaded_generation)
    except Exception as exc:
        if not _is_precondition_failed(exc):
            raise

        # Generation mismatch — a concurrent writer updated the canonical object between our
        # get_blob() call and the upload.  Re-fetch the current generation and retry once.
        logger.warning(
            "Generation mismatch on canonical DB upload (first attempt): %s — re-fetching generation and retrying",
            exc,
        )
        retry_existing = bucket.get_blob("db/unified.duckdb")
        retry_generation = retry_existing.generation if retry_existing is not None else 0
        retry_blob = bucket.blob("db/unified.duckdb")
        # Let any exception from the retry propagate — do NOT silently swallow it.
        retry_blob.upload_from_filename(local_path, if_generation_match=retry_generation)
        uploaded_generation = retry_blob.generation
        logger.info(
            "Uploaded canonical DB to gs://%s/db/unified.duckdb on retry (generation=%s)",
            bucket_name,
            uploaded_generation,
        )
        return GCSUploadResult(success=True, generation=uploaded_generation)


def download_settings_from_gcs(bucket_name: str, local_path: str) -> bool:
    """Download config/settings.yaml from GCS if it exists. Returns True when downloaded."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("config/settings.yaml")
    if not blob.exists():
        return False
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(local_path)
    logger.info("Downloaded settings.yaml from GCS to %s", local_path)
    return True


def upload_settings_to_gcs(bucket_name: str, local_path: str) -> None:
    """Upload config/settings.yaml to GCS. No-op if local file does not exist."""
    if not Path(local_path).exists():
        return
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("config/settings.yaml")
    blob.upload_from_filename(local_path)
    logger.info("Uploaded settings.yaml to gs://%s/config/settings.yaml", bucket_name)


def download_reference_sheet_from_gcs(bucket_name: str, local_path: str) -> bool:
    """Download config/reference_sheet.yaml from GCS if it exists.

    Program OSR WS-4b: mirrors download_settings_from_gcs — reference_sheet.yaml
    is one of the three real configs gitignored alongside settings.yaml (see
    src.config._resolve_config_file's .example fallback for the no-GCS case).
    Unlike settings.yaml, this file has no in-app edit/save path today, so
    there is no matching upload_reference_sheet_to_gcs — restore-at-boot only.
    Returns True when downloaded.
    """
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("config/reference_sheet.yaml")
    if not blob.exists():
        return False
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(local_path)
    logger.info("Downloaded reference_sheet.yaml from GCS to %s", local_path)
    return True


# ── Generated reference workbook (UIS_Reference_Data.xlsx) ───────────────────
# Distinct from config/reference_sheet.yaml above: that YAML is the INPUT that
# declares which rows the sheet contains; this is the generated OUTPUT the owner
# links his Financial-Summary cells to.
#
# It needs a GCS round-trip because syncs run cloud-only, and on Cloud Run
# `finance_dir` is overridden to /tmp/sources — so export_reference_sheet wrote
# every refresh into ephemeral container storage that is discarded on the next
# revision. The workbook on the owner's machine silently stopped updating at the
# cloud migration and was five weeks stale before anyone noticed (its Schwab row
# read less than half the real value). The sync now uploads here; pull-cloud
# fetches it back beside the owner's spreadsheet.
REFERENCE_DATA_BLOB = "exports/UIS_Reference_Data.xlsx"


def upload_reference_data_to_gcs(bucket_name: str, local_path: str) -> bool:
    """Upload the generated UIS_Reference_Data.xlsx. False if there is nothing to upload."""
    if not Path(local_path).exists():
        return False
    client = _get_client()
    bucket = client.bucket(bucket_name)
    bucket.blob(REFERENCE_DATA_BLOB).upload_from_filename(local_path)
    logger.info("Uploaded UIS_Reference_Data.xlsx to gs://%s/%s",
                bucket_name, REFERENCE_DATA_BLOB)
    return True


def download_reference_data_from_gcs(bucket_name: str, local_path: str) -> bool:
    """Download the generated UIS_Reference_Data.xlsx. False when absent.

    Writes via a temp file + os.replace so an interrupted download can never
    leave the owner with a truncated workbook where a stale-but-valid one was.
    """
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(REFERENCE_DATA_BLOB)
    if not blob.exists():
        return False
    dest = Path(local_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        blob.download_to_filename(str(tmp))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    logger.info("Downloaded UIS_Reference_Data.xlsx from GCS to %s", local_path)
    return True


def download_verification_from_gcs(bucket_name: str, local_path: str) -> bool:
    """Download config/verification.yaml from GCS if it exists.

    Program OSR WS-4b: mirrors download_settings_from_gcs — verification.yaml
    is one of the three real configs gitignored alongside settings.yaml. No
    in-app edit/save path today, so restore-at-boot only (no upload counterpart).
    Returns True when downloaded.
    """
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("config/verification.yaml")
    if not blob.exists():
        return False
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(local_path)
    logger.info("Downloaded verification.yaml from GCS to %s", local_path)
    return True


def download_sources_from_gcs(bucket_name: str, local_dir: str) -> list[str]:
    """Download all source files from sources/ prefix into local_dir preserving paths."""
    logger.info("Downloading source files from gs://%s/sources/ to %s", bucket_name, local_dir)
    client = _get_client()
    bucket = client.bucket(bucket_name)
    downloaded_paths: list[str] = []
    local_root = Path(local_dir)
    resolved_root = local_root.resolve()

    for blob in bucket.list_blobs(prefix="sources/"):
        if not blob.name or blob.name.endswith("/"):
            continue
        relative = blob.name.removeprefix("sources/")
        destination = local_root / relative
        resolved_dest = destination.resolve()

        # Path traversal guard: reject any blob name that escapes local_dir
        if not str(resolved_dest).startswith(str(resolved_root) + "/"):
            logger.warning("Skipping blob with suspicious path: %s", blob.name)
            continue

        if resolved_dest.exists():
            local_mtime = resolved_dest.stat().st_mtime
            blob_updated = blob.updated
            if blob_updated is not None and local_mtime >= blob_updated.timestamp():
                logger.info("Skipping download for %s; local file is newer or equal", resolved_dest)
                continue

        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(resolved_dest))
        downloaded_paths.append(str(resolved_dest))
        logger.info("Downloaded source file gs://%s/%s to %s", bucket_name, blob.name, destination)

    return downloaded_paths


def download_seed_pack_from_gcs(bucket_name: str, profile: str, local_dir: str) -> list[str]:
    """Download every file under gs://bucket/seeds/<profile>/ into local_dir,
    preserving relative paths. Returns the downloaded local paths (empty list
    if the prefix has no blobs — a first deploy before the pack was ever
    uploaded, or a profile name typo, is not fatal, matching
    download_sources_from_gcs's shape).

    Program OSR WS-3b: this is the delivery path for a PRIVATE profile (e.g.
    'private-ray') that must never be baked into the public Docker image —
    it stays gitignored locally and lives only in the private deployment's
    GCS bucket, mirroring how config/settings.yaml is restored (never
    committed, GCS-resident, downloaded at boot). Public profiles
    (example/empty) ship in the image via the Dockerfile instead and don't
    need this path at all.
    """
    logger.info("Downloading seed pack '%s' from gs://%s/seeds/%s/ to %s", profile, bucket_name, profile, local_dir)
    client = _get_client()
    bucket = client.bucket(bucket_name)
    prefix = f"seeds/{profile}/"
    downloaded_paths: list[str] = []
    local_root = Path(local_dir)
    resolved_root = local_root.resolve()

    for blob in bucket.list_blobs(prefix=prefix):
        if not blob.name or blob.name.endswith("/"):
            continue
        relative = blob.name.removeprefix(prefix)
        destination = local_root / relative
        resolved_dest = destination.resolve()

        # Path traversal guard: reject any blob name that escapes local_dir
        if not str(resolved_dest).startswith(str(resolved_root) + "/"):
            logger.warning("Skipping seed-pack blob with suspicious path: %s", blob.name)
            continue

        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(resolved_dest))
        downloaded_paths.append(str(resolved_dest))
        logger.info("Downloaded seed-pack file gs://%s/%s to %s", bucket_name, blob.name, destination)

    return downloaded_paths


def upload_seed_pack_to_gcs(bucket_name: str, profile: str, local_dir: str) -> None:
    """Upload every file under local_dir to gs://bucket/seeds/<profile>/,
    preserving relative paths. No-op if local_dir does not exist."""
    local_root = Path(local_dir)
    if not local_root.is_dir():
        logger.info("upload_seed_pack_to_gcs: %s does not exist, nothing to upload", local_dir)
        return

    client = _get_client()
    bucket = client.bucket(bucket_name)
    uploaded = 0
    for local_file in local_root.rglob("*"):
        if not local_file.is_file():
            continue
        relative = local_file.relative_to(local_root)
        blob_name = f"seeds/{profile}/{relative.as_posix()}"
        bucket.blob(blob_name).upload_from_filename(str(local_file))
        uploaded += 1
    logger.info("Uploaded %d file(s) to gs://%s/seeds/%s/", uploaded, bucket_name, profile)


def upload_source_to_gcs(bucket_name: str, reader_name: str, local_path: str) -> None:
    """Upload a single source file to sources/{reader_name}/{filename}."""
    filename = Path(local_path).name
    blob_name = f"sources/{reader_name}/{filename}"
    logger.info("Uploading source file %s to gs://%s/%s", local_path, bucket_name, blob_name)

    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    logger.info("Uploaded source file to gs://%s/%s", bucket_name, blob_name)


def prune_source_blobs(bucket_name: str, reader: str, keep: int = 3) -> list[str]:
    """Prune old source blobs under sources/{reader}/, keeping the newest ``keep`` blobs.

    Policy:
    - List all blobs under ``sources/{reader}/`` (excluding folder-sentinel blobs).
    - Sort by ``blob.updated`` descending (newest first).
    - Keep the first ``keep`` blobs; delete the rest.
    - **Never** delete the newest blob (index 0) — it is always protected.

    Returns:
        List of blob names that were deleted.

    Tolerates non-existent prefix (returns []).
    Does NOT raise — callers should treat failures as non-fatal warnings.
    """
    client = _get_client()
    bucket = client.bucket(bucket_name)
    prefix = f"sources/{reader}/"

    blobs = list(bucket.list_blobs(prefix=prefix))
    # Exclude folder-sentinel pseudo-blobs (empty name suffix or trailing /)
    blobs = [b for b in blobs if b.name and not b.name.endswith("/")]

    if len(blobs) <= keep:
        return []

    # Sort newest first by updated timestamp; blobs with no updated go to the end
    blobs.sort(
        key=lambda b: b.updated if b.updated is not None else datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )

    to_delete = blobs[keep:]
    deleted: list[str] = []
    for blob in to_delete:
        try:
            blob.delete()
            logger.info("Pruned GCS source blob: gs://%s/%s", bucket_name, blob.name)
            deleted.append(blob.name)
        except Exception as exc:
            logger.warning(
                "Failed to delete GCS source blob gs://%s/%s: %s", bucket_name, blob.name, exc
            )
    return deleted
