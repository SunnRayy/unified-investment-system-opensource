"""Database backup utility for DuckDB safety.

CRITICAL: unified.duckdb is gitignored - no version history to restore from.
Every destructive or mutating operation MUST be preceded by a timestamped backup.

Usage:
    from src.database.backup import create_backup, list_backups

    # Before any sync/mutation
    backup_path = create_backup(reason="pre-sync-v3")

    # List existing backups
    backups = list_backups()
"""
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import shutil
import logging
import re

from src.database.connector import resolve_db_path

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_DB_PATH = "data/unified.duckdb"
DEFAULT_BACKUP_DIR = "data/backups"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_KEEP_PER_REASON = 2
DEFAULT_MAX_BACKUPS = 14
DEFAULT_MAX_BACKUP_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB

_COLLISION_SUFFIX_PATTERN = re.compile(r"^(.*)_\d+$")


def _resolve_backup_dir(backup_dir: str, resolved_db_path: Optional[str] = None) -> Path:
    """Resolve backup directory, honouring UIS_DB_PATH when using the default.

    - When *backup_dir* differs from DEFAULT_BACKUP_DIR (e.g. an explicit absolute
      path passed by a caller such as compaction.py), return it unchanged.
    - When *backup_dir* IS the default, anchor the backup directory next to the
      resolved DB file so that the path is cwd-independent (important on Cloud Run
      where cwd ≠ project root and the DB lives at /tmp/data/).

    Args:
        backup_dir: The backup directory string as supplied by the caller.
        resolved_db_path: Pre-resolved absolute DB path (provided by create_backup
            which already calls resolve_db_path).  When None, this helper calls
            resolve_db_path(DEFAULT_DB_PATH) itself (list_backups / prune_backups).
    """
    if backup_dir != DEFAULT_BACKUP_DIR:
        return Path(backup_dir)
    # Default backup dir — anchor next to the resolved DB file.
    if resolved_db_path is not None:
        return Path(resolved_db_path).parent / "backups"
    return Path(resolve_db_path(DEFAULT_DB_PATH)).parent / "backups"


@dataclass
class BackupInfo:
    """Information about a database backup."""
    path: Path
    timestamp: datetime
    reason: str
    size_bytes: int


def create_backup(
    db_path: str = DEFAULT_DB_PATH,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    reason: str = "manual"
) -> Path:
    """Create a timestamped backup of the database.
    
    Args:
        db_path: Path to the database file to backup
        backup_dir: Directory to store backups
        reason: Label for the backup (e.g., "pre-sync", "pre-seed-taxonomy")

    Returns:
        Path to the created backup file

    Raises:
        FileNotFoundError: If db_path doesn't exist
    """
    resolved_db = resolve_db_path(db_path)
    db_path = Path(resolved_db)
    backup_dir_path = _resolve_backup_dir(backup_dir, resolved_db_path=resolved_db)

    # Validate source exists
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}. Cannot create backup."
        )

    # Create backup directory if needed
    backup_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Generate timestamped filename: unified_YYYY-MM-DD_HHMMSS_<reason>.duckdb
    timestamp = datetime.now()
    timestamp_str = timestamp.strftime("%Y-%m-%d_%H%M%S")
    
    # Sanitize reason for filename
    safe_reason = re.sub(r'[^a-zA-Z0-9_-]', '_', reason)
    
    backup_name = f"unified_{timestamp_str}_{safe_reason}.duckdb"
    backup_path = backup_dir_path / backup_name

    # Ensure we never overwrite - add counter if needed
    counter = 1
    while backup_path.exists():
        backup_name = f"unified_{timestamp_str}_{safe_reason}_{counter}.duckdb"
        backup_path = backup_dir_path / backup_name
        counter += 1

    # Copy the database
    shutil.copy2(db_path, backup_path)

    # Keep backup storage bounded while preserving restore-relevant snapshots.
    try:
        pruned = prune_backups(
            backup_dir=str(backup_dir_path),
            protected_paths=[backup_path]
        )
        if pruned:
            logger.info(f"Pruned {len(pruned)} old backup file(s)")
    except Exception as exc:
        # Backup creation succeeded; retention failure should not block the caller.
        logger.warning(f"Backup pruning failed: {exc}")
    
    logger.info(f"Database backup created: {backup_path}")
    
    return backup_path


def prune_backups(
    backup_dir: str = DEFAULT_BACKUP_DIR,
    *,
    now: Optional[datetime] = None,
    max_files: int = DEFAULT_MAX_BACKUPS,
    max_total_bytes: int = DEFAULT_MAX_BACKUP_BYTES,
    recent_days: int = DEFAULT_RETENTION_DAYS,
    keep_per_reason: int = DEFAULT_KEEP_PER_REASON,
    protected_paths: Optional[list[Path]] = None,
) -> list[Path]:
    """Prune backups using restore-focused retention policy.

    Policy:
    - Keep all backups with `_KEEP` in filename stem
    - Keep newest backup overall
    - Keep newest N backups per reason (collision suffixes normalized)
    - Keep newest 1 backup/day for the last M days
    - Enforce hard caps on non-KEEP parsed backups:
      - max file count
      - max total bytes

    Files that do not match the standard timestamped naming pattern are untouched.
    """
    backup_dir_path = _resolve_backup_dir(backup_dir)
    if not backup_dir_path.exists():
        return []

    backups = list_backups(str(backup_dir_path))
    if not backups:
        return []

    current_time = now or datetime.now()
    keep_paths: set[Path] = set()
    protected = {Path(p) for p in (protected_paths or [])}

    entries = []
    for backup in backups:
        keep_mark = "_KEEP" in backup.path.stem
        base_reason = _COLLISION_SUFFIX_PATTERN.sub(r"\1", backup.reason)
        entries.append({
            "info": backup,
            "keep_mark": keep_mark,
            "base_reason": base_reason,
        })

    # Keep explicitly protected paths and manual _KEEP backups.
    keep_paths.update(protected)
    for entry in entries:
        if entry["keep_mark"]:
            keep_paths.add(entry["info"].path)

    # Keep newest overall parsed backup.
    keep_paths.add(entries[0]["info"].path)

    # Keep newest N backups per normalized reason.
    kept_by_reason: dict[str, int] = {}
    for entry in entries:
        reason = entry["base_reason"]
        if kept_by_reason.get(reason, 0) >= keep_per_reason:
            continue
        keep_paths.add(entry["info"].path)
        kept_by_reason[reason] = kept_by_reason.get(reason, 0) + 1

    # Keep newest 1 backup/day for recent window.
    cutoff = current_time - timedelta(days=recent_days)
    seen_days: set[date] = set()
    for entry in entries:
        ts = entry["info"].timestamp
        if ts < cutoff:
            continue
        day = ts.date()
        if day in seen_days:
            continue
        seen_days.add(day)
        keep_paths.add(entry["info"].path)

    def kept_non_keep() -> list[dict]:
        return [
            entry for entry in entries
            if (
                entry["info"].path in keep_paths
                and not entry["keep_mark"]
                and entry["info"].path not in protected
            )
        ]

    # Hard cap by file count.
    capped = kept_non_keep()
    if len(capped) > max_files:
        for entry in capped[max_files:]:
            keep_paths.discard(entry["info"].path)

    # Hard cap by total non-KEEP size.
    capped = kept_non_keep()
    total_size = sum(entry["info"].size_bytes for entry in capped)
    if total_size > max_total_bytes:
        for entry in reversed(capped):  # oldest first
            if total_size <= max_total_bytes:
                break
            keep_paths.discard(entry["info"].path)
            total_size -= entry["info"].size_bytes

    removed: list[Path] = []
    for entry in entries:
        path = entry["info"].path
        if path in keep_paths:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)

    return removed


def list_backups(backup_dir: str = DEFAULT_BACKUP_DIR) -> list[BackupInfo]:
    """List all backups sorted by timestamp (newest first).

    Args:
        backup_dir: Directory containing backup files

    Returns:
        List of BackupInfo objects, sorted newest first
    """
    backup_dir = _resolve_backup_dir(backup_dir)

    if not backup_dir.exists():
        return []
    
    backups = []
    
    # Pattern: unified_YYYY-MM-DD_HHMMSS_<reason>.duckdb
    pattern = re.compile(r'unified_(\d{4}-\d{2}-\d{2})_(\d{6})_(.+)\.duckdb')
    
    for file in backup_dir.iterdir():
        if not file.suffix == '.duckdb':
            continue
            
        match = pattern.match(file.name)
        if not match:
            continue
            
        date_str, time_str, reason = match.groups()
        
        # Note: We intentionally do NOT strip collision counter suffixes (like _1, _2).
        # The reason is stored exactly as captured from the filename.
        # This avoids false positives like stripping "phase_3" to "phase".
        
        try:
            timestamp = datetime.strptime(f"{date_str}_{time_str}", "%Y-%m-%d_%H%M%S")
        except ValueError:
            # Skip files with invalid timestamps
            continue
            
        backups.append(BackupInfo(
            path=file,
            timestamp=timestamp,
            reason=reason,
            size_bytes=file.stat().st_size
        ))
    
    # Sort by timestamp, newest first
    backups.sort(key=lambda b: b.timestamp, reverse=True)
    
    return backups
