"""Byte-parity gate for the P&L-engine migration of ``get_wealthos_assets``.

Release 1 / Step 2 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md). ``get_wealthos_assets`` was re-expressed as a thin formatter
over ``compute_portfolio_pnl`` (via ``src/services/pnl/wealthos.py``); the old
private per-asset loop was deleted from the route. This test pins the
engine-backed endpoint to the *exact* V7.8.3 behavior on a fixed-FX, frozen-clock
fixture.

Anchors (as in the summary-parity gate):
1. ``_legacy_wealthos`` — a frozen, faithful copy of the pre-engine loop, kept
   here (and only here) as the reference implementation. Engine output == it,
   field-by-field. The fixture uses distinct |P&L| values so there are NO
   sort-key ties (the legacy tie-order came from an unordered ``SELECT DISTINCT``
   and is inherently non-deterministic), making the comparison exact.
2. ``GOLDEN`` — hand-checked per-asset constants (anti-vacuity).
3. A treatment mutation guard — cash / balance-only / traded / closed records are
   asserted directly, so breaking the engine's treatment turns this red.
"""
import asyncio
from datetime import date, datetime

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.api.routes.data import get_wealthos_assets
from src.services.currency import is_balance_only_holding
from src.services.portfolio_helpers import calculate_realized_pnl
from src.services.position_lots import unrealized_from_holdings_row
from src.services.pnl.pnl_math import calculate_unrealized_pl_values

FIXED_FX = 7.1


# ── Fixture: distinct |pl| per asset → no sort-key ties ──────────────────────
#   US_STK_VOO   active USD traded    |pl|=426000
#   CN_FUND_A    active CNY traded    |pl|=3500  (has a sell → realized 1500)
#   MM_CASH      active Money Market  |pl|=0     (cash-equiv: invested==value)
#   INS_POLICY   active Insurance     balance-only → invested/pl/ret=None; non-rebal
#   US_STK_SOLD  closed USD           |pl|=7100  (fully sold)
def _seed(path):
    conn = duckdb.connect(str(path))
    conn.execute(
        """CREATE TABLE holdings (
            asset_id VARCHAR, asset_name VARCHAR, source_system VARCHAR,
            market_value DOUBLE, cost_price_unit DOUBLE, market_price_unit DOUBLE,
            quantity DOUBLE, currency VARCHAR, snapshot_date DATE, is_shadow BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE transactions (
            asset_id VARCHAR, asset_name VARCHAR, transaction_type VARCHAR,
            quantity DOUBLE, price_unit DOUBLE, amount_net DOUBLE,
            currency VARCHAR, transaction_date DATE, source_system VARCHAR,
            is_provisional BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE asset_registry (
            canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE taxonomy_classes (
            id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (3,'Equity','股票',NULL,TRUE),
        (4,'US Equity','美股',3,TRUE),
        (7,'CN Equity','A股',3,TRUE),
        (10,'Money Market','货基',NULL,TRUE),
        (11,'Insurance','保险',NULL,FALSE)"""
    )
    conn.execute(
        """INSERT INTO asset_registry VALUES
        ('US_STK_VOO','Vanguard S&P 500','US Equity',TRUE),
        ('CN_FUND_A','易方达','CN Equity',TRUE),
        ('MM_CASH','示例流动货币B','Money Market',TRUE),
        ('INS_POLICY','安泰人生','Insurance',FALSE),
        ('US_STK_SOLD','Sold Co','US Equity',TRUE)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('US_STK_VOO','Vanguard S&P 500','Schwab_CSV',142000.0,400.0,100.0,200.0,'USD',DATE '2026-07-01',FALSE),
        ('CN_FUND_A','易方达','CN_Fund_Excel',50000.0,1.2,1.4,40000.0,'CNY',DATE '2026-07-01',FALSE),
        ('MM_CASH','示例流动货币B','CN_Fund_Excel',20000.0,1.0,1.0,20000.0,'CNY',DATE '2026-07-01',FALSE),
        ('INS_POLICY','安泰人生','Insurance_Excel',100000.0,NULL,100000.0,1.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('US_STK_VOO','Vanguard S&P 500','buy',200.0,400.0,80000.0,'USD',DATE '2025-01-15','Schwab_CSV',FALSE),
        ('CN_FUND_A','易方达','buy',40000.0,1.2,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel',FALSE),
        ('CN_FUND_A','易方达','sell',10000.0,1.35,13500.0,'CNY',DATE '2025-06-01','CN_Fund_Excel',FALSE),
        ('US_STK_SOLD','Sold Co','buy',100.0,50.0,5000.0,'USD',DATE '2025-03-01','Schwab_CSV',FALSE),
        ('US_STK_SOLD','Sold Co','sell',100.0,60.0,6000.0,'USD',DATE '2025-09-01','Schwab_CSV',FALSE)"""
    )
    conn.close()


# ── Frozen V7.8.3 reference — faithful copy of the pre-engine loop ────────────
def _legacy_wealthos(db, include_non_rebalanceable=False):
    today = date.today()
    today_fx = FIXED_FX
    active_rows = db.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT h.asset_id, MAX(h.asset_name) AS name,
               COALESCE(MAX(r.asset_class), 'Unknown') AS type,
               MAX(h.source_system) AS source_system,
               SUM(h.market_value) AS market_value, SUM(h.quantity) AS total_quantity,
               MAX(h.cost_price_unit) AS cost_price_unit, MAX(h.market_price_unit) AS market_price_unit,
               MAX(h.currency) AS currency
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        WHERE h.is_shadow = FALSE GROUP BY h.asset_id
        HAVING SUM(h.market_value) > 0 AND SUM(h.quantity) > 0
        """
    ).fetchall()
    active_map = {r[0]: r for r in active_rows}
    active_ids = set(active_map.keys())
    all_asset_ids = [
        r[0] for r in db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
        ).fetchall()
    ]
    all_asset_ids_set = set(all_asset_ids)
    for aid in sorted(active_ids):
        if aid not in all_asset_ids_set:
            all_asset_ids.append(aid)
    first_buy_map = {
        r[0]: r[1] for r in db.execute(
            """SELECT asset_id, MIN(transaction_date) FROM transactions
               WHERE LOWER(transaction_type) IN ('buy','vest','transfer_in','rsu_vest','premium_payment')
               GROUP BY asset_id"""
        ).fetchall()
    }
    total_invested_map = {
        r[0]: float(r[1] or 0.0) for r in db.execute(
            """SELECT asset_id, SUM(quantity*price_unit) FROM transactions
               WHERE LOWER(transaction_type) IN ('buy','vest','transfer_in','rsu_vest','premium_payment')
               GROUP BY asset_id"""
        ).fetchall()
    }
    asset_currency_map = {
        r[0]: str(r[1] or "CNY") for r in db.execute(
            "SELECT asset_id, MAX(currency) FROM transactions WHERE asset_id IS NOT NULL GROUP BY asset_id"
        ).fetchall()
    }
    closed_ids = [aid for aid in all_asset_ids if aid not in active_ids]
    closed_meta_map = {}
    if closed_ids:
        ph = ", ".join("?" for _ in closed_ids)
        for r in db.execute(
            f"""SELECT t.asset_id, MAX(t.asset_name), MAX(r.asset_class)
                FROM transactions t LEFT JOIN asset_registry r ON t.asset_id = r.canonical_id
                WHERE t.asset_id IN ({ph}) GROUP BY t.asset_id""", closed_ids
        ).fetchall():
            closed_meta_map[r[0]] = {"name": r[1], "type": r[2] or "Unknown"}

    def format_period(first_date):
        if first_date is None:
            return "Unknown"
        try:
            if isinstance(first_date, str):
                first_date = datetime.strptime(first_date, "%Y-%m-%d").date()
            elif hasattr(first_date, "date") and callable(first_date.date):
                first_date = first_date.date()
            elif not isinstance(first_date, date):
                return "Unknown"
            days = (today - first_date).days
            if days < 30:
                return f"{days}d"
            elif days < 365:
                return f"{days // 30}m"
            years = days // 365
            rem = (days % 365) // 30
            return f"{years}y {rem}m" if rem else f"{years}y"
        except Exception:
            return "Unknown"

    txn_asset_ids = all_asset_ids_set
    records = []
    for aid in all_asset_ids:
        is_active = aid in active_ids
        is_balance_only = False
        if is_active:
            row = active_map[aid]
            name = row[1] or aid
            asset_type = row[2] or "Unknown"
            market_value = float(row[4] or 0.0)
            quantity = float(row[5] or 0.0)
            cost_price_unit = float(row[6] or 0.0)
            market_price_unit = float(row[7] or 0.0)
            native_currency = str(row[8] or "CNY")
            cost_basis = cost_price_unit * quantity
            invested_amount = cost_basis * today_fx if native_currency == "USD" else cost_basis
            unrealized, unrealized_native = calculate_unrealized_pl_values(
                market_value=market_value, quantity=quantity, cost_price_unit=cost_price_unit,
                market_price_unit=market_price_unit, currency=native_currency,
                top_class=asset_type, sub_class=asset_type, today_fx=today_fx)
            unrealized_current_lots_pct = unrealized_from_holdings_row(
                market_value=market_value, quantity=quantity, cost_price_unit=cost_price_unit,
                market_price_unit=market_price_unit, currency=native_currency,
                top_class=asset_type, sub_class=asset_type, today_fx=today_fx)
            is_balance_only = is_balance_only_holding(
                cost_price_unit=cost_price_unit, has_transactions=aid in txn_asset_ids)
            status = "ACTIVE"
        else:
            meta = closed_meta_map.get(aid, {})
            name = meta.get("name") or aid
            asset_type = meta.get("type") or "Unknown"
            market_value = 0.0
            native_currency = asset_currency_map.get(aid, "CNY")
            invested_native = total_invested_map.get(aid, 0.0)
            invested_amount = invested_native * today_fx if native_currency == "USD" else invested_native
            unrealized = 0.0
            unrealized_native = 0.0
            status = "CLOSED"
            unrealized_current_lots_pct = None
        CASH = ("Cash", "现金", "Money Market", "Bank Wealth", "货币")
        is_cash_equiv = any(kw in (asset_type or "") for kw in CASH)
        native_currency = native_currency if native_currency else "CNY"
        if is_cash_equiv:
            unrealized = 0.0
            realized = 0.0
            lifetime_pl = 0.0
            lifetime_pl_native = 0.0
            if is_active:
                invested_amount = market_value
            unrealized_current_lots_pct = None
            ifr = invested_amount if invested_amount != 0 else abs(realized)
            ret = (lifetime_pl / ifr * 100) if ifr != 0 else 0.0
        elif is_balance_only:
            invested_amount = None
            realized = 0.0
            lifetime_pl = None
            lifetime_pl_native = None
            ret = None
            unrealized_current_lots_pct = None
        else:
            realized_native, realized_currency = calculate_realized_pnl(db, aid, start_date=None)
            realized = realized_native * today_fx if realized_currency == "USD" else realized_native
            lifetime_pl = unrealized + realized
            lifetime_pl_native = (unrealized_native + realized_native
                                  if native_currency == "USD" else lifetime_pl)
            ifr = invested_amount if invested_amount != 0 else abs(realized)
            ret = (lifetime_pl / ifr * 100) if ifr != 0 else 0.0
        records.append({
            "name": name, "code": aid, "type": asset_type,
            "period": format_period(first_buy_map.get(aid)), "status": status,
            "invested": round(invested_amount, 3) if invested_amount is not None else None,
            "cur": round(market_value, 2),
            "pl": round(lifetime_pl, 3) if lifetime_pl is not None else None,
            "pl_native": round(lifetime_pl_native, 3) if lifetime_pl_native is not None else None,
            "pnl_currency": native_currency,
            "ret": round(ret, 2) if ret is not None else None,
            "unrealized_current_lots_pct": (round(unrealized_current_lots_pct, 2)
                                            if unrealized_current_lots_pct is not None else None),
            "open_value_trap_review": False,
        })
    records.sort(key=lambda x: (0 if x["status"] == "ACTIVE" else 1,
                                -abs(x["pl"]) if x["pl"] is not None else 0.0))
    from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
    excluded_ids = set() if include_non_rebalanceable else fetch_non_rebalanceable_asset_ids(db)
    NON_REBAL = ["Real Estate", "Insurance", "房地产", "保险", "Property (房产)",
                 "Insurance (保险)", "Residential (住宅)", "Commercial (商业)", "REITs (信托)"]
    main, non_rebal = [], []
    for r in records:
        if not include_non_rebalanceable and (r["code"] in excluded_ids or r["type"] in NON_REBAL):
            non_rebal.append(r)
        else:
            main.append(r)
    return {"assets": main, "non_rebalanceable_assets": non_rebal}


@pytest.fixture
def frozen_fx(monkeypatch):
    import src.services.pnl.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)


def _new(db, inc):
    return asyncio.new_event_loop().run_until_complete(
        get_wealthos_assets(include_non_rebalanceable=inc, db=db)
    )


# Keys the payload has gained SINCE the frozen legacy reference was taken, each
# purely additive (no legacy field changed shape or value). Stripped before the
# equality assertion, then asserted separately below — so parity stays a strict
# dict comparison rather than decaying into a subset check.
#   has_manual_data    — Release 2 (#7): is this row's P&L an owner-entered override?
#   can_log_manual_pnl — Release 2 (#7): may the owner log P&L for this asset?
#   type_cn            — Program BIL / WS-9: Chinese companion for `type`
#                         (taxonomy_classes.name_cn), None when unset — the frontend
#                         resolver falls back to English, so no boolean invariant here.
ADDITIVE_SINCE_LEGACY = {"has_manual_data", "can_log_manual_pnl", "type_cn"}


def _strip_additive(payload: dict) -> dict:
    return {
        key: [
            {k: v for k, v in row.items() if k not in ADDITIVE_SINCE_LEGACY}
            for row in rows
        ] if isinstance(rows, list) else rows
        for key, rows in payload.items()
    }


@pytest.mark.parametrize("inc", [False, True])
def test_engine_wealthos_equals_frozen_legacy(tmp_path, frozen_fx, inc):
    db_path = tmp_path / "wealthos_parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_wealthos(db, include_non_rebalanceable=inc)
        new = _new(db, inc)
    finally:
        db.close()
    assert _strip_additive(new) == legacy


@pytest.mark.parametrize("inc", [False, True])
def test_additive_keys_are_present_on_every_row(tmp_path, frozen_fx, inc):
    """The other half of the parity contract: the additive keys must actually be
    there (so `_strip_additive` can never quietly hide a missing field), and with
    no override logged they must all read False."""
    db_path = tmp_path / "wealthos_additive.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        new = _new(db, inc)
    finally:
        db.close()

    rows = [r for k, v in new.items() if isinstance(v, list) for r in v]
    assert rows, "fixture produced no rows — the assertion below would be vacuous"
    for row in rows:
        assert ADDITIVE_SINCE_LEGACY <= set(row), f"{row['code']} is missing an additive key"
        assert row["has_manual_data"] is False, "no override is logged in this fixture"
        assert isinstance(row["can_log_manual_pnl"], bool)


# Hand-checked per-asset constants (FX = 7.1, all-time).
#   VOO   invested 80000*7.1=568000; unrealized_native (100-400)*200=-60000 ->
#         unrealized -426000; pl -426000; pl_native -60000; ret -75.0
#   CN_FUND_A  cost 1.2*40000=48000; unrealized 50000-48000=2000; realized
#         (1.35-1.2)*10000=1500; pl 3500; ret 3500/48000=7.29
#   MM_CASH    cash: invested==cur==20000; pl 0; ret 0
#   INS_POLICY balance-only: invested/pl/ret None; cur 100000
#   US_STK_SOLD closed: invested 5000*7.1=35500; realized 1000*7.1=7100; pl 7100
GOLDEN = {
    "US_STK_VOO": {"invested": 568000.0, "cur": 142000.0, "pl": -426000.0,
                   "pl_native": -60000.0, "pnl_currency": "USD", "ret": -75.0,
                   "status": "ACTIVE"},
    "CN_FUND_A": {"invested": 48000.0, "cur": 50000.0, "pl": 3500.0,
                  "pnl_currency": "CNY", "ret": 7.29, "status": "ACTIVE"},
    "MM_CASH": {"invested": 20000.0, "cur": 20000.0, "pl": 0.0, "ret": 0.0,
                "status": "ACTIVE"},
    "INS_POLICY": {"invested": None, "cur": 100000.0, "pl": None, "ret": None,
                   "status": "ACTIVE"},
    "US_STK_SOLD": {"invested": 35500.0, "cur": 0.0, "pl": 7100.0,
                    "pl_native": 1000.0, "pnl_currency": "USD", "status": "CLOSED"},
}


def test_wealthos_matches_golden_constants(tmp_path, frozen_fx):
    db_path = tmp_path / "wealthos_golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = _new(db, True)  # include everything so all 5 appear
    finally:
        db.close()
    by_code = {r["code"]: r for r in res["assets"] + res["non_rebalanceable_assets"]}
    for code, expected in GOLDEN.items():
        for k, v in expected.items():
            got = by_code[code][k]
            if isinstance(v, float):
                assert got == pytest.approx(v, abs=0.01), f"{code}.{k}: {got} != {v}"
            else:
                assert got == v, f"{code}.{k}: {got!r} != {v!r}"


def test_treatment_and_partition_mutation_guard(tmp_path, frozen_fx):
    """Breaking cash / balance-only / traded / closed handling turns this red."""
    db_path = tmp_path / "wealthos_treat.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        default = _new(db, False)   # exclude non-rebalanceable → INS partitioned out
    finally:
        db.close()
    main_codes = [r["code"] for r in default["assets"]]
    non_rebal_codes = [r["code"] for r in default["non_rebalanceable_assets"]]
    by_code = {r["code"]: r for r in default["assets"] + default["non_rebalanceable_assets"]}

    # Partition: Insurance is non-rebalanceable, the rest are main.
    assert non_rebal_codes == ["INS_POLICY"]
    assert set(main_codes) == {"US_STK_VOO", "CN_FUND_A", "MM_CASH", "US_STK_SOLD"}

    # Cash-equiv: invested == current value, zero P&L, no current-lots pct.
    mm = by_code["MM_CASH"]
    assert mm["invested"] == mm["cur"] == pytest.approx(20000.0)
    assert mm["pl"] == 0.0 and mm["ret"] == 0.0
    assert mm["unrealized_current_lots_pct"] is None

    # Balance-only: null invested/pl/ret, value retained.
    ins = by_code["INS_POLICY"]
    assert ins["invested"] is None and ins["pl"] is None and ins["ret"] is None
    assert ins["cur"] == pytest.approx(100000.0)

    # Traded (USD) keeps a real, non-zero lifetime P&L and current-lots pct.
    voo = by_code["US_STK_VOO"]
    assert voo["pl"] == pytest.approx(-426000.0)
    assert voo["unrealized_current_lots_pct"] is not None

    # Closed asset: zero current value, realized-only lifetime P&L.
    sold = by_code["US_STK_SOLD"]
    assert sold["status"] == "CLOSED" and sold["cur"] == 0.0
    assert sold["pl"] == pytest.approx(7100.0)

    # Active-then-closed ordering.
    assert default["assets"][-1]["code"] == "US_STK_SOLD"
