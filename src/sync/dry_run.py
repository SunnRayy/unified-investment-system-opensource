"""Dry-run sync: run full pipeline against a throwaway DB copy.

Usage (CLI):
    python main.py --sync-v3 --dry-run

The original DB is never touched — all mutations go to a PID-scoped tmp copy
that is deleted when the function returns (or if it raises).
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from src.database.connector import DatabaseConnector, resolve_db_path
from src.database.schema import bootstrap_database
from src.sync.orchestrator import run_full_sync_v3

logger = logging.getLogger(__name__)


def run_dry_sync(db_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run full sync pipeline against a tmp DB copy.

    The production DB is untouched. All reader mutations, market-data upserts,
    integrity checks, and the sync-diff report run against the copy.

    Args:
        db_path: Path to the live production DB (or default sentinel).
        config:  Config dict identical to what the normal sync receives.

    Returns:
        A summary dict with keys:
            new_holdings      – asset_count_after - asset_count_before
            changed_holdings  – live_price_holdings_updated + position_deltas_detected
            removed_holdings  – max(0, asset_count_before - asset_count_after)
            sync_warnings     – number of warnings emitted during the dry sync
            integrity_status  – "ok" | "degraded" | "failed"
            tmp_path          – empty string (tmp file already deleted on return)
    """
    live_path = Path(resolve_db_path(db_path))
    tmp_path = live_path.parent / f"uis_dry_run_{os.getpid()}.duckdb"

    # Sibling WAL file that DuckDB may create alongside the tmp copy.
    tmp_wal = Path(str(tmp_path) + ".wal")

    tmp_connector = None
    try:
        # ── 1. Clone production DB ──────────────────────────────────────────
        shutil.copy2(str(live_path), str(tmp_path))
        logger.info("dry-run: tmp DB copy created at %s", tmp_path)

        # Copy live WAL sibling so the tmp DB opens with all committed data.
        # DuckDB writes committed changes to the WAL before checkpointing to the main file.
        live_wal = Path(str(live_path) + ".wal")
        tmp_wal_seed = Path(str(tmp_path) + ".wal")
        if live_wal.exists():
            shutil.copy2(str(live_wal), str(tmp_wal_seed))

        # ── 2. Open connector + reconcile schema/migrations ─────────────────
        tmp_connector = DatabaseConnector(str(tmp_path))
        bootstrap_database(tmp_connector)

        # ── 3. Run full sync (dry_run=True suppresses backup side-effects) ──
        result = run_full_sync_v3(tmp_connector, config, dry_run=True)

        # ── 4. Build summary ─────────────────────────────────────────────────
        diff = result.sync_diff or {}
        asset_before = diff.get("asset_count_before", 0)
        asset_after = diff.get("asset_count_after", 0)

        integrity_status = "ok"
        if not result.success:
            integrity_status = "failed"
        elif result.degraded:
            integrity_status = "degraded"

        summary: Dict[str, Any] = {
            "new_holdings": max(0, asset_after - asset_before),
            "changed_holdings": (
                result.live_price_holdings_updated
                + result.position_deltas_detected
            ),
            "removed_holdings": max(0, asset_before - asset_after),
            "sync_warnings": len(result.warnings),
            "integrity_status": integrity_status,
            "tmp_path": "",  # will be deleted before we return
        }

    finally:
        # ── 5. Clean up ──────────────────────────────────────────────────────
        if tmp_connector is not None:
            try:
                tmp_connector.close()
            except Exception:
                pass

        for p in (tmp_path, tmp_wal):
            if p.exists():
                try:
                    p.unlink()
                    logger.info("dry-run: deleted tmp file %s", p)
                except Exception as exc:
                    logger.warning("dry-run: could not delete %s: %s", p, exc)

    # Verify the original is untouched (sanity check — not a blocking error).
    try:
        if not live_path.exists():
            logger.error("dry-run: LIVE DB missing after dry run! %s", live_path)
    except Exception:
        pass

    return summary
