#!/usr/bin/env python3
"""Database maintenance: compact the local + cloud (GCS) DuckDB and prune backups.

Operates on the live cloud bucket named by $UIS_GCS_BUCKET and the local
data/ tree. Any command that touches the cloud bucket requires the env var
set — see docs/deployment-instructions.md.

  --compact-local   Compact data/unified.duckdb in place (EXPORT/IMPORT, row-count
                    verified). Requires the dev server stopped (./dev.sh stop) — the
                    lock-probe aborts cleanly otherwise, leaving the DB untouched.
  --compact-cloud   Download db/unified.duckdb -> compact -> upload back -> force a new
                    Cloud Run revision so the service reloads the compacted DB. Verifies
                    the GCS object shrank. Do it during a quiet window (no concurrent
                    dashboard writes) so a flush can't re-bloat GCS before the restart.
  --prune-backups   Keep only the KEEP_NEWEST most-recent backups in each of
                    the cloud bucket's backups/ AND local data/backups/; delete the rest.
                    Count-based so total storage stays bounded (~KEEP_NEWEST × DB size).
                    DRY-RUN unless --execute is passed.
                    Note: cloud-mirror-* and pre-pull-* files are EXCLUDED from this
                    count; they have their own retention enforced by --pull-cloud.
  --pull-cloud      Replace local data/unified.duckdb with the cloud (production) DB.
                    One direction only: cloud → local. Keeps a pre-pull backup of the
                    current local DB (2 newest) and a cloud-mirror snapshot (3 newest).
                    Requires the local backend to be stopped (or use --force).
                    Use ./dev.sh pull-cloud for the full stop→pull→start workflow.
  --all             prune-backups + compact-local + compact-cloud (prune first).

Examples:
  .venv/bin/python scripts/maint_db.py --prune-backups               # dry-run plan
  .venv/bin/python scripts/maint_db.py --prune-backups --execute     # delete
  .venv/bin/python scripts/maint_db.py --compact-cloud
  ./dev.sh stop && .venv/bin/python scripts/maint_db.py --compact-local && ./dev.sh start
  ./dev.sh pull-cloud                                                 # recommended
  .venv/bin/python scripts/maint_db.py --pull-cloud --yes            # manual (server stopped)

Safety: all gsutil calls are sequential (NO -m — gsutil's multiprocessing crashes on
macOS via the objc fork-safety check). Compaction never deletes the original until a
fresh, row-count-verified copy exists.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Program OSR WS-5b: was a hardcoded real bucket name, load-bearing for
# --compact-cloud/--pull-cloud/--prune-backups, not just a docstring
# reference. Now the same UIS_GCS_BUCKET env var the app
# server itself uses (src/storage/gcs_flush.py, src/api/main.py, ...) — see
# docs/deployment-instructions.md for how it's set. None (not a fallback
# default) when unset, so a bucket-touching command fails loudly via
# _require_bucket() below rather than silently operating on a bucket that
# isn't yours. --compact-local needs none of this.
_BUCKET_NAME = os.environ.get("UIS_GCS_BUCKET")
BUCKET = f"gs://{_BUCKET_NAME}" if _BUCKET_NAME else None
CLOUD_DB = f"{BUCKET}/db/unified.duckdb" if BUCKET else None
GCS_BACKUPS = f"{BUCKET}/backups" if BUCKET else None


def _bucket_from_secret_manager() -> str | None:
    """Resolve the bucket from Secret Manager when the env var is unset.

    UIS_GCS_BUCKET is a `valueFrom` Secret Manager reference on Cloud Run, not
    an inline value, so a developer shell usually does NOT have it exported.
    Every automated caller therefore failed — most importantly the pre-push
    release hook, which warned into a scrolling push log and continued. Nobody
    read it, and GCS backups reached 46 objects / 4.9 GiB before anyone looked.

    This is not guessing a default bucket (the thing the comment above rightly
    refuses to do): it reads the same authoritative source the deployment does.
    Lazy — only called when the env var is missing, so it costs nothing on the
    normal path and nothing at all for --compact-local.
    """
    try:
        r = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest",
             "--secret=UIS_GCS_BUCKET"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = r.stdout.strip() if r.returncode == 0 else ""
    return name or None


def _require_bucket() -> None:
    global _BUCKET_NAME, BUCKET, CLOUD_DB, GCS_BACKUPS
    if not _BUCKET_NAME:
        _BUCKET_NAME = _bucket_from_secret_manager()
        if _BUCKET_NAME:
            BUCKET = f"gs://{_BUCKET_NAME}"
            CLOUD_DB = f"{BUCKET}/db/unified.duckdb"
            GCS_BACKUPS = f"{BUCKET}/backups"
            print(f"[maint_db] UIS_GCS_BUCKET unset — resolved "
                  f"{_BUCKET_NAME!r} from Secret Manager.")
    if not _BUCKET_NAME:
        sys.exit(
            "UIS_GCS_BUCKET is not set and could not be resolved from Secret "
            "Manager (is `gcloud` authenticated?) — required for any cloud "
            "operation (--compact-cloud, --pull-cloud, --prune-backups). "
            "export UIS_GCS_BUCKET=<your-bucket-name> "
            "(see docs/deployment-instructions.md)."
        )
LOCAL_DB = REPO_ROOT / "data" / "unified.duckdb"
LOCAL_BACKUPS = REPO_ROOT / "data" / "backups"
SERVICE = "uis-dashboard"
REGION = "us-central1"
BACKEND_PORT = 8008  # matches dev.sh BACKEND_PORT

# Count-based retention: keep this many newest backups per location. With a 30-80 MB
# DB this bounds each backup set well under 1 GiB.
KEEP_NEWEST = int(os.environ.get("UIS_KEEP_NEWEST", "8"))

# gsutil's -m parallel mode crashes on macOS (objc fork-safety) — keep it disabled and
# set the fork-safety escape hatch for any child Python it spawns.
_ENV = {**os.environ, "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}

# --------------------------------------------------------------------------
# --pull-cloud constants
# --------------------------------------------------------------------------

# Self-managed backup retention limits (separate from KEEP_NEWEST).
# These files are created exclusively by --pull-cloud and have their own
# rotation logic; --prune-backups must not count or delete them.
CLOUD_MIRROR_KEEP = 3   # keep 3 newest cloud-mirror-* backups
PRE_PULL_KEEP = 2       # keep 2 newest pre-pull-* backups

# Prefixes identifying --pull-cloud managed backups.
_SELF_MANAGED_PREFIXES: tuple[str, ...] = ("cloud-mirror-", "pre-pull-")

# Minimum acceptable staging DB before overwriting the local DB.
MIN_HOLDINGS_COUNT = 600
MIN_SCHEMA_VERSION = 64
MIN_STAGING_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB

# Staging path: dotfile so it is excluded from regular glob("*.duckdb") scans.
STAGING_NAME = ".cloud-pull-staging.duckdb"


def _sh(cmd: list[str], capture: bool = True) -> str:
    res = subprocess.run(cmd, capture_output=capture, text=True, env=_ENV)
    if res.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{res.stderr}")
    return res.stdout if capture else ""


def _human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TiB"


# --------------------------------------------------------------------------
# Backup pruning (count-based)
# --------------------------------------------------------------------------

def _gcs_backups() -> list[tuple[str, datetime, int]]:
    """(uri, mtime, size_bytes) for every object under backups/ (robust to naming)."""
    out = _sh(["gsutil", "ls", "-l", f"{GCS_BACKUPS}/**"])
    items: list[tuple[str, datetime, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1].startswith("gs://") and parts[-1].endswith(".duckdb"):
            try:
                size = int(parts[0])
                ts = datetime.strptime(parts[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
            items.append((parts[-1], ts, size))
    return items


def _local_backups() -> list[tuple[str, datetime, int]]:
    if not LOCAL_BACKUPS.exists():
        return []
    return [
        (str(f), datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc), f.stat().st_size)
        for f in LOCAL_BACKUPS.glob("*.duckdb")
        # Exclude self-managed backups (cloud-mirror-*, pre-pull-*) — they have their
        # own retention policy enforced by --pull-cloud. Including them in the general
        # KEEP_NEWEST prune would delete mirrors/pre-pulls that should be kept longer.
        # Also exclude the dotfile staging file (.cloud-pull-staging.duckdb) in case
        # glob("*.duckdb") matches dotfiles on the current platform.
        if not f.name.startswith(".")
        and not any(f.name.startswith(p) for p in _SELF_MANAGED_PREFIXES)
    ]


def _plan(items: list[tuple[str, datetime, int]]):
    """Keep the newest backup from each of the KEEP_NEWEST most recent DATES.

    Previously this kept the newest KEEP_NEWEST OBJECTS, which silently
    collapses the restore window whenever one day produces that many backups —
    exactly what a busy release day does. On 2026-08-29 the bucket held 16
    backups from that single day, so "keep newest 8" meant eight copies of one
    afternoon and the deletion of every restore point going back three weeks.
    Eight copies of one afternoon is not a backup history; it is one backup,
    stored eight times.

    Date-based retention spends the same storage budget on a window that
    actually spans time: at most one backup per day, for the newest
    KEEP_NEWEST days. Same bounded cost (KEEP_NEWEST x DB size), far better
    recovery. Same-day duplicates are deleted, so a busy day costs one slot,
    not eight.
    """
    # Floor: never prune a collection that is already within budget. Date
    # retention is stricter than count retention on SMALL sets — five local
    # backups spread over two days would drop to two, deleting history to
    # reclaim nothing. The policy exists to bound growth, not to trim tidy
    # collections, so below the budget everything is kept.
    if len(items) <= KEEP_NEWEST:
        return sorted(items, key=lambda x: x[1], reverse=True), []

    by_date: dict[object, list[tuple[str, datetime, int]]] = {}
    for it in items:
        by_date.setdefault(it[1].date(), []).append(it)

    keep: list[tuple[str, datetime, int]] = []
    delete: list[tuple[str, datetime, int]] = []
    for i, day in enumerate(sorted(by_date, reverse=True)):
        ranked = sorted(by_date[day], key=lambda x: x[1], reverse=True)
        if i < KEEP_NEWEST:
            keep.append(ranked[0])       # newest backup that day
            delete.extend(ranked[1:])    # same-day duplicates
        else:
            delete.extend(ranked)        # older than the retention window

    keep.sort(key=lambda x: x[1], reverse=True)
    delete.sort(key=lambda x: x[1], reverse=True)
    return keep, delete


def prune_backups(execute: bool) -> None:
    _require_bucket()
    for label, items, is_gcs in (("GCS", _gcs_backups(), True),
                                 ("LOCAL", _local_backups(), False)):
        keep, delete = _plan(items)
        keep_sz = sum(s for _, _, s in keep)
        del_sz = sum(s for _, _, s in delete)
        print(f"\n===== {label} backups: {len(items)} total "
              f"({_human(keep_sz + del_sz)}) — keep {len(keep)} ({_human(keep_sz)}), "
              f"delete {len(delete)} ({_human(del_sz)}) | retain newest {KEEP_NEWEST} DATES =====")
        if keep:
            print(f"  newest kept: {keep[0][1].date()}  |  oldest kept: {keep[-1][1].date()}")
        if not delete:
            print("  nothing to delete.")
            continue
        if not execute:
            print(f"  [DRY-RUN] would delete {len(delete)} backups ({_human(del_sz)}). "
                  f"Re-run with --execute.")
            continue
        uris = [u for u, _, _ in delete]
        if is_gcs:
            CHUNK = 25
            for i in range(0, len(uris), CHUNK):
                batch = uris[i:i + CHUNK]
                _sh(["gsutil", "rm", *batch], capture=True)
                print(f"  ... deleted {min(i + CHUNK, len(uris))}/{len(uris)}")
        else:
            for u in uris:
                os.remove(u)
        print(f"  DELETED {len(delete)} {label} backups ({_human(del_sz)} reclaimed).")


# --------------------------------------------------------------------------
# Compaction
# --------------------------------------------------------------------------

def _gcs_object_size(uri: str) -> int:
    out = _sh(["gsutil", "du", uri])
    return int(out.split()[0]) if out.strip() else 0


def compact_local() -> None:
    from src.database.compaction import compact_database
    if not LOCAL_DB.exists():
        print(f"[local] {LOCAL_DB} not found — skipping.")
        return
    print(f"[local] compacting {LOCAL_DB} (server must be stopped) ...")
    try:
        r = compact_database(str(LOCAL_DB))
    except RuntimeError as e:
        print(f"[local] ABORTED (original untouched): {e}")
        print("[local] Stop the dev server first:  ./dev.sh stop")
        return
    print(f"[local] {_human(r['before_bytes'])} -> {_human(r['after_bytes'])} "
          f"| rows_verified={r['rows_verified']} | backup={r['backup_path']}")


def compact_cloud() -> None:
    _require_bucket()
    from src.database.compaction import compact_database
    before = _gcs_object_size(CLOUD_DB)
    print(f"[cloud] current {CLOUD_DB} = {_human(before)}")
    work = Path(tempfile.mkdtemp(prefix="uis_cloud_compact_")) / "unified.duckdb"
    _sh(["gsutil", "cp", CLOUD_DB, str(work)], capture=True)
    r = compact_database(str(work))
    print(f"[cloud] compacted: {_human(r['before_bytes'])} -> {_human(r['after_bytes'])} "
          f"| rows_verified={r['rows_verified']}")
    _sh(["gsutil", "cp", str(work), CLOUD_DB], capture=True)
    ts = int(datetime.now(timezone.utc).timestamp())
    print("[cloud] forcing new Cloud Run revision (cold start reloads compacted DB) ...")
    _sh(["gcloud", "run", "services", "update", SERVICE, "--region", REGION,
         "--update-env-vars", f"DB_COMPACTED_AT={ts}", "--quiet"], capture=True)
    after = _gcs_object_size(CLOUD_DB)
    print(f"[cloud] DONE. {CLOUD_DB} now {_human(after)} (was {_human(before)})")
    try:
        import shutil
        shutil.rmtree(work.parent, ignore_errors=True)
    except Exception:
        pass
    if after >= before:
        print("[cloud] WARNING: object did not shrink — a concurrent flush may have "
              "re-bloated it. Retry during a fully quiet window.")


# --------------------------------------------------------------------------
# Pull-cloud: replace local DB with the cloud (production) DB
# --------------------------------------------------------------------------

def verify_pulled_db(path: Path) -> tuple[bool, str]:
    """Verify a downloaded staging DB is safe to install as the local DB.

    Checks (in order):
    1. File exists and is > MIN_STAGING_SIZE_BYTES (10 MiB).
    2. Opens read-only with duckdb (detects corruption / wrong format).
    3. SELECT COUNT(*) FROM holdings >= MIN_HOLDINGS_COUNT (600).
    4. Both trade_logs and schema_version tables exist.
    5. SELECT MAX(version) FROM schema_version >= MIN_SCHEMA_VERSION (64).

    Returns (True, summary) on success, (False, reason) on any failure.
    The local DB is NEVER touched by this function.
    """
    import duckdb  # lazy — not needed for other maint_db operations

    if not path.exists():
        return False, f"staging file not found: {path}"
    size = path.stat().st_size
    if size < MIN_STAGING_SIZE_BYTES:
        return False, (
            f"staging too small: {_human(size)} "
            f"(min {_human(MIN_STAGING_SIZE_BYTES)})"
        )

    conn = None
    try:
        conn = duckdb.connect(str(path), read_only=True)
        # 3. Holdings count
        count = conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        if count < MIN_HOLDINGS_COUNT:
            return False, f"holdings count too low: {count} < {MIN_HOLDINGS_COUNT}"
        # 4. Required tables
        for tbl in ("trade_logs", "schema_version"):
            exists = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [tbl],
            ).fetchone()[0]
            if not exists:
                return False, f"required table missing: {tbl}"
        # 5. Schema version
        ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if ver is None or ver < MIN_SCHEMA_VERSION:
            return False, (
                f"schema_version too low: {ver} "
                f"(min {MIN_SCHEMA_VERSION})"
            )
        return True, f"{count} holdings, schema_version={ver}"
    except Exception as exc:
        return False, f"DB verification failed: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _rotate_backups(backup_dir: Path, pattern: str, keep: int) -> None:
    """Keep the `keep` newest files matching pattern; silently delete the rest."""
    files = sorted(
        backup_dir.glob(pattern),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        old.unlink()
        print(f"  [prune-{pattern}] removed: {old.name}")


def _check_server_running() -> bool:
    """Return True if the backend server appears to be listening on BACKEND_PORT."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{BACKEND_PORT}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False  # assume not running on probe error


def _download_cloud_db(staging: Path) -> None:
    """Download the live cloud DB to the given local staging path via gsutil."""
    staging.parent.mkdir(parents=True, exist_ok=True)
    _sh(["gsutil", "cp", CLOUD_DB, str(staging)], capture=True)


def pull_cloud(yes: bool, force: bool) -> None:
    """Replace local data/unified.duckdb with the cloud (production) DB.

    Flow:
      1. Server guard — refuse if backend is running on BACKEND_PORT (unless --force).
      2. Interactive y/N confirmation (skipped by --yes).
      3. Download CLOUD_DB → LOCAL_BACKUPS/.cloud-pull-staging.duckdb via gsutil.
      4. Verify staging (size, holdings count, required tables, schema version).
         Failure: delete staging, abort — local DB untouched.
      5. Install with insurance:
         a. shutil.move current LOCAL_DB → pre-pull-<ts>.duckdb (keep PRE_PULL_KEEP).
         b. Delete stale WAL (belongs to old DB; must not be applied to new file).
         c. shutil.copy2 staging → cloud-mirror-<ts>.duckdb (keep CLOUD_MIRROR_KEEP).
         d. os.replace staging → LOCAL_DB (atomic on POSIX).
         e. Print summary.
    """
    _require_bucket()

    # 1. Server guard.
    if _check_server_running() and not force:
        print(
            f"[pull-cloud] ERROR: backend server appears to be running on "
            f"port {BACKEND_PORT}.\n"
            "  Stop it first:  ./dev.sh stop\n"
            "  Or use --force to override (not recommended while the server is live).",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Confirm prompt unless --yes.
    if not yes:
        print(
            f"\n[pull-cloud] This will REPLACE your local DB with the cloud (production) DB:\n"
            f"  Source : {CLOUD_DB}\n"
            f"  Target : {LOCAL_DB}\n"
            f"  Safety : current local DB  → data/backups/pre-pull-<ts>.duckdb  "
            f"(keep {PRE_PULL_KEEP} newest)\n"
            f"           cloud snapshot    → data/backups/cloud-mirror-<ts>.duckdb  "
            f"(keep {CLOUD_MIRROR_KEEP} newest)\n"
        )
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("[pull-cloud] Aborted.")
            sys.exit(0)

    # 3. Download cloud DB to staging.
    staging = LOCAL_BACKUPS / STAGING_NAME
    LOCAL_BACKUPS.mkdir(parents=True, exist_ok=True)
    print(f"\n[pull-cloud] Downloading {CLOUD_DB} → {staging} ...")
    try:
        _download_cloud_db(staging)
    except Exception as exc:
        print(f"[pull-cloud] DOWNLOAD FAILED: {exc}", file=sys.stderr)
        if staging.exists():
            staging.unlink()
        sys.exit(1)

    # 4. Verify staging BEFORE touching the live DB — if this fails the local
    #    DB is completely untouched.
    print("[pull-cloud] Verifying staged DB ...")
    ok, msg = verify_pulled_db(staging)
    if not ok:
        print(f"[pull-cloud] VERIFICATION FAILED: {msg}", file=sys.stderr)
        if staging.exists():
            staging.unlink()
            print("[pull-cloud] Staging file deleted. Local DB is untouched.")
        sys.exit(1)
    print(f"[pull-cloud] Verification passed: {msg}")

    # 5. Install with insurance (all file ops via Python os/shutil — never shell).
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 5a. Archive current local DB to a pre-pull backup.
    pre_pull_path = LOCAL_BACKUPS / f"pre-pull-{ts}.duckdb"
    # The WAL file (.wal) belongs to the OLD db. We must delete it AFTER moving
    # the old db away so the new file is never exposed to a stale WAL that could
    # corrupt or confuse it.
    wal_path = Path(str(LOCAL_DB) + ".wal")
    if LOCAL_DB.exists():
        print(f"[pull-cloud] Archiving {LOCAL_DB.name} → {pre_pull_path.name} ...")
        shutil.move(str(LOCAL_DB), str(pre_pull_path))
        # Old DB is now moved away — safe to delete its WAL.
        if wal_path.exists():
            wal_path.unlink()
            print("[pull-cloud] Removed stale WAL file from old DB.")

    # 5b. Copy staging → durable cloud-mirror backup before installing.
    mirror_path = LOCAL_BACKUPS / f"cloud-mirror-{ts}.duckdb"
    print(f"[pull-cloud] Creating insurance mirror → {mirror_path.name} ...")
    shutil.copy2(str(staging), str(mirror_path))

    # 5c. Atomic install: staging replaces unified.duckdb.
    print(f"[pull-cloud] Installing cloud DB as {LOCAL_DB.name} ...")
    os.replace(str(staging), str(LOCAL_DB))

    # Rotate self-managed backups to their retention limits.
    _rotate_backups(LOCAL_BACKUPS, "pre-pull-*.duckdb", PRE_PULL_KEEP)
    _rotate_backups(LOCAL_BACKUPS, "cloud-mirror-*.duckdb", CLOUD_MIRROR_KEEP)

    # 5d. Summary.
    installed_size = LOCAL_DB.stat().st_size
    pre_pull_count = len(list(LOCAL_BACKUPS.glob("pre-pull-*.duckdb")))
    mirror_count = len(list(LOCAL_BACKUPS.glob("cloud-mirror-*.duckdb")))
    print(
        f"\n[pull-cloud] SUCCESS\n"
        f"  Installed : {LOCAL_DB}  ({_human(installed_size)})\n"
        f"  DB stats  : {msg}\n"
        f"  Backups   : {pre_pull_count} pre-pull, {mirror_count} cloud-mirror retained\n"
    )

    _pull_reference_workbook()


def _pull_reference_workbook() -> None:
    """Fetch the generated UIS_Reference_Data.xlsx beside the owner's spreadsheet.

    The sync publishes it to GCS because on Cloud Run it is written to
    /tmp/sources and discarded. Pulling it here means the workbook the owner's
    Financial Summary links to refreshes on the same command that refreshes the
    DB, instead of silently freezing (it sat five weeks stale, its Schwab row
    reading less than half the real value, because nothing carried it across).

    Advisory: a failure must not fail an otherwise-good pull. But it says so
    loudly — a silent skip is the exact failure mode being fixed.
    """
    # gsutil, not google-cloud-storage: that package is a CLOUD dependency and
    # is NOT installed in the local venv, which is why every other cloud call in
    # this script shells out too. Importing the client here would raise
    # ModuleNotFoundError on the very machine this feature exists to serve.
    try:
        from src.config import load_config
        from src.sync.reference_export import OUTPUT_FILENAME

        finance_dir = (load_config() or {}).get("finance_dir", "")
        if not finance_dir:
            print("  Reference  : finance_dir not configured — skipped")
            return

        dest = Path(finance_dir) / OUTPUT_FILENAME
        remote = f"{BUCKET}/exports/{OUTPUT_FILENAME}"
        probe = subprocess.run(["gsutil", "-q", "stat", remote],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            print("  Reference  : none published in GCS yet — run a cloud sync "
                  "to publish one (local workbook unchanged)")
            return

        # Download beside the target then os.replace, so an interrupted transfer
        # cannot leave a truncated workbook where a stale-but-valid one was.
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            r = subprocess.run(["gsutil", "cp", remote, str(tmp)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:200] or "gsutil cp failed")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        print(f"  Reference  : {dest} refreshed from cloud")
    except Exception as exc:
        print(f"  Reference  : WARNING — could not refresh the workbook: {exc}")
        print("               Your Financial Summary may be reading stale values.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compact-local", action="store_true")
    ap.add_argument("--compact-cloud", action="store_true")
    ap.add_argument("--prune-backups", action="store_true")
    ap.add_argument("--pull-cloud", action="store_true",
                    help="Replace local DB with cloud (production) DB — one direction only")
    ap.add_argument("--all", action="store_true", help="prune + compact-local + compact-cloud")
    ap.add_argument("--execute", action="store_true", help="actually delete (else dry-run)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip interactive confirmation for --pull-cloud")
    ap.add_argument("--force", action="store_true",
                    help="Skip server-running guard for --pull-cloud")
    args = ap.parse_args()
    if not (args.compact_local or args.compact_cloud or args.prune_backups
            or args.all or args.pull_cloud):
        ap.error(
            "specify --prune-backups / --compact-local / --compact-cloud "
            "/ --pull-cloud / --all"
        )
    if args.prune_backups or args.all:
        prune_backups(execute=args.execute or args.all)
    if args.compact_local or args.all:
        compact_local()
    if args.compact_cloud or args.all:
        compact_cloud()
    if args.pull_cloud:
        pull_cloud(yes=args.yes, force=args.force)


if __name__ == "__main__":
    main()
