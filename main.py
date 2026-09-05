# main.py
"""Main entry point for Huinsight."""

import argparse
import sys
from pathlib import Path

from src.config import load_config
from src.database.connector import DatabaseConnector
# initialize_schema is not referenced in this module, but it must stay importable:
# tests/core/test_main.py patches it as main.initialize_schema.
from src.database.schema import bootstrap_database, initialize_schema  # noqa: F401

# Sync imports
from src.sync.dsa_sync import sync_market_data
from src.sync.orchestrator import run_full_sync_v3
from src.sync.dry_run import run_dry_sync

# Validation imports
from src.validation.cost_basis_validator import validate_cost_basis
from src.validation.data_integrity_gate import run_integrity_checks

# Backup imports
from src.database.backup import create_backup, list_backups

# Compaction import
from src.database.compaction import compact_database


def main():
    parser = argparse.ArgumentParser(description='Huinsight')
    parser.add_argument('--init', action='store_true', help='Initialize database')
    parser.add_argument('--sync-market', action='store_true', help='Sync only market data')
    parser.add_argument('--sync-v3', action='store_true', help='Run v3 sync with pre/post validation')
    parser.add_argument(
        '--strategy-review',
        action='store_true',
        help='Generate strategy alignment report',
    )
    parser.add_argument('--sync-rsu', action='store_true', help='Sync RSU vesting schedules from PIS')
    parser.add_argument('--refresh-prices', action='store_true', help='Refresh market prices from DSA')
    parser.add_argument('--validate-cost-basis', action='store_true', help='Validate cost basis consistency')
    parser.add_argument('--verify-monthly', action='store_true', help='Run monthly verification for the previous month')
    parser.add_argument('--verify-period', nargs=2, metavar=('START', 'END'), help='Run verification for custom period (YYYY-MM-DD YYYY-MM-DD)')
    parser.add_argument('--validate-readers', action='store_true', help='Run pre-insertion validation for all source readers')
    parser.add_argument('--check-integrity', action='store_true', help='Run 14 self-derived data integrity invariant checks')
    parser.add_argument('--audit', action='store_true', help='[Deprecated] Alias for --check-integrity (ground truth removed)')
    parser.add_argument('--backup', action='store_true', help='Create a manual database backup')
    parser.add_argument('--list-backups', action='store_true', help='List available database backups')
    parser.add_argument('--compact-db', action='store_true', help='Compact DuckDB (EXPORT/IMPORT) to reclaim space — takes a backup first')
    parser.add_argument(
        '--rsu-gains',
        nargs='*',
        metavar=('START_MONTH', 'END_MONTH'),
        default=None,
        help='Per-month realized gain on RSU share sales (FIFO vs vest price). '
             'Optionally pass two YYYY-MM bounds. Use with --json / --fx-rate.',
    )
    parser.add_argument(
        '--fx-rate',
        type=float,
        default=None,
        help='Explicit USD->CNY rate for --rsu-gains (default: today spot; the CNY '
             'column is indicative either way — see docs/known-issues.md §fx-constant)',
    )
    parser.add_argument('--dry-run', action='store_true', help='Run sync against a DB copy and report diffs without writing production')
    parser.add_argument('--json', action='store_true', help='Output machine-readable JSON (use with --check-integrity)')
    parser.add_argument('--config', default='config/settings.yaml', help='Config file path')
    
    args = parser.parse_args()
    
    # Load config
    try:
        config = load_config(args.config)
        db_config = config.get('database', {})
        db_path = db_config.get('path', 'data/unified.duckdb')

        if args.validate_readers:
            from src.validation.run_reader_validation import run_full_validation

            print("Running pre-insertion data validation...")
            print("(All DB access is READ-ONLY — no mutations)")
            print()

            report = run_full_validation(config=config, db_path=db_path)
            output_dir = Path("data/validation")
            output_dir.mkdir(parents=True, exist_ok=True)

            json_path = output_dir / "reader_validation_report.json"
            md_path = output_dir / "reader_validation_report.md"
            json_path.write_text(report.to_json(), encoding="utf-8")
            md_path.write_text(report.to_markdown(), encoding="utf-8")

            print(report.to_markdown())
            print()
            print(f"JSON report: {json_path}")
            print(f"Markdown report: {md_path}")
            return
        
        if args.rsu_gains is not None:
            # Read-only report — never writes. Uses the project-standard
            # read-only connection so it only ever contends with a genuine
            # writer, never with the running API server's readers.
            import json as _json
            from src.database.connector import connect_readonly_with_retry
            from src.services.rsu_realized_gains import (
                format_report, rsu_realized_gains_by_month,
            )

            if len(args.rsu_gains) not in (0, 2):
                print("❌ --rsu-gains takes either no arguments or exactly two "
                      "YYYY-MM bounds (START END)")
                sys.exit(1)
            start_month, end_month = (args.rsu_gains + [None, None])[:2]

            ro = connect_readonly_with_retry(db_path)
            try:
                payload = rsu_realized_gains_by_month(
                    ro, start_month=start_month, end_month=end_month,
                    fx_rate=args.fx_rate,
                )
            finally:
                ro.close()

            if args.json:
                print(_json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(format_report(payload))
            return

        if args.compact_db:
            print("Compacting database (EXPORT → IMPORT)...")
            try:
                result = compact_database(db_path)
                saved_mb = (result["before_bytes"] - result["after_bytes"]) / 1_000_000
                print(f"✅ Compact complete: {result['before_bytes']//1_000_000}MB → {result['after_bytes']//1_000_000}MB (saved {saved_mb:.1f}MB)")
                print(f"   Backup: {result['backup_path']}")
            except RuntimeError as e:
                print(f"❌ Compaction failed: {e}")
                print("   Hint: Stop the API server (./dev.sh stop) before running --compact-db")
                sys.exit(1)
            sys.exit(0)

        # Connect to database
        connector = DatabaseConnector(db_path)
        if args.init:
            print("Initializing database schema...")
            bootstrap_database(connector)
            print("✅ Database initialized")
        
        # Handle backup commands (before any sync operations)
        if args.backup:
            print("Creating manual database backup...")
            backup_path = create_backup(reason="manual-cli")
            print(f"✅ Backup created: {backup_path}")
        
        if args.list_backups:
            print("=== Available Backups ===")
            backups = list_backups()
            if backups:
                for b in backups:
                    print(f"  {b.timestamp} - {b.path} ({b.reason})")
                print(f"\nTotal: {len(backups)} backups")
            else:
                print("  No backups found.")
        
        # Ensure schema exists for sync and validation operations
        if args.sync_market or args.sync_v3 or args.validate_cost_basis or args.check_integrity or args.audit:
            bootstrap_database(connector)

        if args.sync_v3 and args.dry_run:
            print("=== Dry-Run Sync (production DB untouched) ===\n")
            connector.close()  # Checkpoint WAL before copy — ensures tmp copy is complete
            summary = run_dry_sync(db_path, config)
            print("--- Dry-Run Diff Summary ---")
            print(f"   New holdings:     {summary['new_holdings']}")
            print(f"   Changed holdings: {summary['changed_holdings']}")
            print(f"   Removed holdings: {summary['removed_holdings']}")
            print(f"   Sync warnings:    {summary['sync_warnings']}")
            print(f"   Integrity:        {summary['integrity_status']}")
            print("\n✅ Dry-run complete — original DB is unchanged.")
            sys.exit(0)

        if args.sync_v3:
            print("=== Running V3 Sync with Validation ===\n")
            result = run_full_sync_v3(connector, config)

            if result.success:
                print("✅ V3 Sync Complete")
                print(f"   Transactions: {result.transactions_synced}")
                print(f"   Holdings:     {result.holdings_synced}")
                print(f"   Market data:  {result.market_records_synced}")
                print(f"   Allocations:  {result.allocations_synced}")
                print(f"   Taxonomy:     {result.taxonomy_created} created, {result.taxonomy_updated} updated")

                if result.warnings:
                    print(f"\n⚠️  Warnings ({len(result.warnings)}):")
                    for w in result.warnings:
                        print(f"   - {w}")

                if result.cost_basis_discrepancies > 0:
                    print(f"\n   Cost basis discrepancies: {result.cost_basis_discrepancies}")
                if result.allocation_drifts > 0:
                    print(f"   Allocation drifts: {result.allocation_drifts}")

                # Phase 3: RSU sync
                print("\n--- Phase 3: RSU Sync ---")
                from src.sync.rsu_sync import sync_rsu

                rsu_result = sync_rsu(config)
                rsu_holdings = len(rsu_result.get("holdings", []))
                rsu_txns = len(rsu_result.get("transactions", []))
                print(f"   RSU sync:     {rsu_holdings} holdings, {rsu_txns} transactions")
            else:
                print(f"❌ V3 Sync Failed: {result.error_message}")
        elif args.sync_market:
            count = sync_market_data(connector, config)
            print(f"✅ Synced {count} market records")
        elif args.validate_cost_basis:
            print("=== Cost Basis Validation ===")
            discrepancies = validate_cost_basis(connector, threshold_pct=1.0)
            if discrepancies:
                print(f"\n⚠️  Found {len(discrepancies)} cost basis discrepancies:\n")
                for d in discrepancies:
                    print(f"  {d['asset_id']} ({d.get('asset_name', 'Unknown')[:25]}...):")
                    print(f"    Synced cost:     {d['synced_cost']:,.2f}")
                    print(f"    Calculated cost: {d['calculated_cost']:,.2f}")
                    print(f"    Difference:      {d['diff_pct']:.2f}%")
                    print(f"    Quantity:        {d['quantity']:,.4f}")
                    print()
                print("To fix: Run 'python main.py refresh-cost-basis' in PIS Legacy,")
                print("        then re-sync with 'python main.py --sync-all'")
            else:
                print("✅ No cost basis discrepancies found - data is consistent")
        elif args.check_integrity:
            import json as _json
            from src.validation.data_integrity_gate import INTEGRITY_CHECK_COUNT
            report = run_integrity_checks(connector)
            if getattr(args, 'json', False):
                # Machine-readable output; non-zero exit on any failure.
                # Agents and CI should use this form.
                payload = {
                    "count": INTEGRITY_CHECK_COUNT,
                    "passed": report.passed_count,      # verified only
                    "skipped": report.skipped_count,    # evaluated nothing
                    "failed": len(report.failed_checks),
                    "all_passed": report.all_passed,
                    "run_at": report.run_at.isoformat(),
                    "checks": [
                        {
                            "name": c.name,
                            "passed": c.passed,
                            "skipped": c.skipped,
                            "detail": c.details,
                        }
                        for c in report.checks
                    ],
                }
                print(_json.dumps(payload, indent=2))
            else:
                print("=== Data Integrity Gate ===\n")
                print(report.to_text())
                print()
                if report.all_passed:
                    print("✅ All integrity checks passed")
                else:
                    print(f"❌ {len(report.failed_checks)} check(s) failed — see details above")
            if not report.all_passed:
                exit(1)
        elif args.audit:
            print("=== Data Integrity Checks (--audit is deprecated, use --check-integrity) ===\n")
            report = run_integrity_checks(connector)
            print(report.to_text())
            print()
            if report.all_passed:
                print("✅ All integrity checks passed")
            else:
                print(f"❌ {len(report.failed_checks)} check(s) failed — see details above")
                exit(1)
        elif not args.init and not args.strategy_review:
            print("Usage: python main.py [OPTIONS]")
            print("  --init                Initialize database schema")
            print("  --sync-v3             Run v3 sync with pre/post validation (recommended)")
            print("  --sync-market         Sync only market data")
            print("  --strategy-review     Generate strategy alignment report")
            print("  --validate-cost-basis Validate cost basis consistency")
            print("  --validate-readers    Run pre-insertion reader validation report")
            print("  --check-integrity     Run 14 self-derived data integrity invariant checks")
            print("  --check-integrity --json  Machine-readable JSON output; non-zero exit on failure")
            print("  --audit               [Deprecated] Alias for --check-integrity")
            print("  --rsu-gains [S E]     Per-month realized gain on RSU sales (FIFO vs vest price)")
        
        if args.strategy_review:
            from src.services.strategy_reviewer import generate_strategy_report

            report = generate_strategy_report(connector)
            print(f"Strategy review: {report['target_scope_alignment_status']}")
            print(f"Contrarian score: {report['contrarian_score']}")
            print(f"Trading frequency (30d): {report['trading_frequency']['period_30d']} trades")

        if args.sync_rsu:
            from src.sync.rsu_sync import sync_rsu
            print("Syncing RSU from Excel...")
            result = sync_rsu(config)
            h = len(result.get("holdings", []))
            t = len(result.get("transactions", []))
            print(f"✅ RSU sync: {h} holdings, {t} transactions")

        if args.refresh_prices:
            from src.sync.dsa_sync import update_holdings_prices

            print("Refreshing market prices from DSA...")
            fx_rates = config.get("currency", {}).get("fallback_rates", {})
            # settings.yaml stores as "USD_CNY: 7.0" — normalize to {"USD": 7.0}
            normalized_fx = {k.replace("_CNY", ""): v for k, v in fx_rates.items()}

            result = update_holdings_prices(connector, fx_rates=normalized_fx)
            print(f"✅ Prices refreshed — DSA: {result['dsa']} rows")

        if args.verify_monthly or args.verify_period:
            from datetime import date
            from src.verification.monthly_verifier import run_monthly_verification
            import calendar

            if args.verify_period:
                start = date.fromisoformat(args.verify_period[0])
                end = date.fromisoformat(args.verify_period[1])
            else:
                # Previous month
                today = date.today()
                prev_month = today.month - 1 if today.month > 1 else 12
                prev_year = today.year if today.month > 1 else today.year - 1
                start = date(prev_year, prev_month, 1)
                end = date(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])

            print(f"\n=== Running Monthly Verification: {start} to {end} ===")

            # Fix 3: Check for existing records and warn
            existing_count = connector.execute("""
                SELECT COUNT(*) FROM verification_logs
                WHERE period_start = ? AND period_end = ? AND verification_type = 'monthly'
            """, (str(start), str(end))).fetchone()[0]
            if existing_count > 0:
                print(f"⚠️  Note: {existing_count} verification record(s) already exist for this period.")
                print("   This run will create a new record. Latest record is used by API.\n")

            result = run_monthly_verification(connector, start, end, config)
            print(f"\n📊 Verification Results:")
            print(f"   Adoption rate:      {result['adoption_rate']}%")
            # Fix 2: Show per-model breakdown
            if result.get('adoption_rate_by_model'):
                print(f"   Adoption by model:")
                for model, rate in sorted(result['adoption_rate_by_model'].items()):
                    print(f"      {model:15s}: {rate}%")
            print(f"   Max drift:          {result['max_allocation_drift']}%")
            print(f"   Portfolio return:   {result['portfolio_return']}%" if result['portfolio_return'] else "   Portfolio return:   N/A")
            print(f"   Alpha:              {result['alpha']}" if result['alpha'] else "   Alpha:              N/A")
            print(f"   Total insights:     {result['total_insights']}")
            print(f"\n✅ Verification saved to verification_logs")

        connector.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        exit(1)


if __name__ == '__main__':
    main()
