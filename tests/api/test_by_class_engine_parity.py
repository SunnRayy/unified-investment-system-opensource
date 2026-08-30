"""Byte-parity gate for the P&L-engine migration of ``get_performance_by_class``.

Release 1 / Step 4 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md). ``get_performance_by_class`` (/performance/by-class) is now a
thin formatter over ``compute_portfolio_pnl`` (with ``with_transaction_provenance``
so closed / sold-only assets carry their resolved class); its private per-asset
loop + sold-only registry query were deleted. This test pins the engine-backed
endpoint to the exact V7.8.3 behavior.

Three anchors so the parity claim cannot be vacuous:

1. ``_legacy_by_class`` — a frozen, verbatim copy of the pre-engine loop; the
   engine output must equal it, class-for-class, field-by-field.
2. ``GOLDEN_TOP`` — hand-checked per-class constants (anti-vacuity), including a
   fully-sold closed asset whose realized P&L must aggregate into its class.
3. A balance-only-class mutation guard: the Fixed-Income class KEEPS the bond's
   market value (weight) but contributes 0 cost and 0 unrealized — the V7.8.3
   phantom rule. Breaking the engine's balance-only zeroing turns this red.
"""
import asyncio

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.api.routes import performance as perf
from src.services.currency import calculate_cost_basis_cny
from src.services.portfolio_helpers import (
    get_display_name, resolve_top_class, is_non_balanceable_class,
    calculate_realized_pnl,
)

FIXED_FX = 7.1


# ── Fixture ──────────────────────────────────────────────────────────────────
#   US_STK_VOO   US Equity  USD traded          mv142000  cost568000  unreal-426000
#   CN_FUND_A    CN Equity  CNY traded + sell   mv 50000  cost 48000  unreal  2000  realized 1500
#   MM_CASH      Money Mkt  CNY cash            mv 20000  cost 20000  unreal     0
#   INS_POLICY   Insurance  CNY traded          mv 30000  cost 25000  unreal  5000  (non-rebal)
#   Bond_BAL     CN Bonds   balance-only        mv200000  cost     0  unreal     0  (phantom guard)
#   US_STK_SOLD  US Equity  USD fully sold      no holding — realized 7100 only (closed)
#   Equity top = US_STK_VOO + CN_FUND_A (active) + US_STK_SOLD (closed realized).
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
        "CREATE TABLE asset_registry (canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR, is_rebalanceable BOOLEAN)"
    )
    conn.execute(
        "CREATE TABLE taxonomy_classes (id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER, is_rebalanceable BOOLEAN)"
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (1,'Fixed Income','固定收益',NULL,TRUE),
        (2,'CN Bonds','中国债券',1,TRUE),
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
        ('Bond_BAL','招行固收债券','CN Bonds',TRUE),
        ('US_STK_SOLD','已清仓股','US Equity',TRUE)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('US_STK_VOO','Vanguard S&P 500','Schwab_CSV',142000.0,400.0,100.0,200.0,'USD',DATE '2026-07-01',FALSE),
        ('CN_FUND_A','易方达','CN_Fund_Excel',50000.0,1.2,1.4,40000.0,'CNY',DATE '2026-07-01',FALSE),
        ('MM_CASH','示例流动货币B','CN_Fund_Excel',20000.0,1.0,1.0,20000.0,'CNY',DATE '2026-07-01',FALSE),
        ('INS_POLICY','安泰人生','Insurance_Excel',30000.0,100.0,120.0,250.0,'CNY',DATE '2026-07-01',FALSE),
        ('Bond_BAL','招行固收债券','Financial_Summary_Excel',200000.0,NULL,200000.0,1.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('US_STK_VOO','Vanguard S&P 500','buy',200.0,400.0,80000.0,'USD',DATE '2025-01-15','Schwab_CSV',FALSE),
        ('CN_FUND_A','易方达','buy',40000.0,1.2,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel',FALSE),
        ('CN_FUND_A','易方达','sell',10000.0,1.35,13500.0,'CNY',DATE '2025-06-01','CN_Fund_Excel',FALSE),
        ('INS_POLICY','安泰人生','buy',250.0,100.0,25000.0,'CNY',DATE '2024-05-01','Insurance_Excel',FALSE),
        ('US_STK_SOLD','已清仓股','buy',100.0,50.0,5000.0,'USD',DATE '2025-03-01','Schwab_CSV',FALSE),
        ('US_STK_SOLD','已清仓股','sell',100.0,60.0,6000.0,'USD',DATE '2025-09-01','Schwab_CSV',FALSE)"""
    )
    conn.close()


# ── Frozen V7.8.3 reference — faithful copy of the pre-engine by-class loop ───
def _legacy_by_class(db, period="all_time", exclude=False):
    start_date = perf.period_start_date(period)
    latest_cte, latest_params = perf.latest_snapshot_cte(start_date)
    today_fx = FIXED_FX
    txn_asset_ids = perf._assets_with_transactions(db)

    if start_date:
        all_tx_assets = db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE transaction_date >= ?",
            (start_date,),
        ).fetchall()
    else:
        all_tx_assets = db.execute("SELECT DISTINCT asset_id FROM transactions").fetchall()
    realized_map = {}
    for row in all_tx_assets:
        aid = row[0]
        if aid:
            realized_native, realized_currency = calculate_realized_pnl(db, aid, start_date=start_date)
            realized_map[aid] = (
                realized_native * today_fx if realized_currency == "USD" else realized_native
            )

    raw_query = f"""
        {latest_cte}
        SELECT h.asset_id, MAX(h.asset_name) as name,
            COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified') as top_class,
            COALESCE(MAX(r.asset_class), 'Unclassified') as sub_class,
            SUM(h.market_value) as market_value, SUM(h.quantity) as quantity,
            MAX(h.cost_price_unit) as cost_price_unit, MAX(h.market_price_unit) as market_price_unit,
            MAX(h.currency) as currency
        FROM holdings h JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
        LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
        WHERE h.is_shadow = FALSE GROUP BY h.asset_id
    """
    raw_rows = db.execute(raw_query, latest_params or None).fetchall()
    top_agg = {}
    sub_agg = {}
    total_mv = 0.0
    processed_assets = set()

    for row in raw_rows:
        aid = row[0]
        top = resolve_top_class(row[2])
        sub = get_display_name(row[3])
        mv = float(row[4] or 0.0)
        quantity = float(row[5] or 0.0)
        cost_price_unit = float(row[6] or 0.0)
        market_price_unit = float(row[7] or 0.0)
        currency = str(row[8] or "CNY")
        if perf._is_balance_only(top_class=top, sub_class=sub,
                                 cost_price_unit=cost_price_unit, aid=aid, txn_ids=txn_asset_ids):
            cost = 0.0
            unrealized = 0.0
        else:
            cost = calculate_cost_basis_cny(market_value=mv, quantity=quantity,
                                            cost_price_unit=cost_price_unit, currency=currency,
                                            top_class=top, sub_class=sub, today_fx=today_fx)
            unrealized, _ = perf.calculate_unrealized_pl_values(
                market_value=mv, quantity=quantity, cost_price_unit=cost_price_unit,
                market_price_unit=market_price_unit, currency=currency,
                top_class=top, sub_class=sub, today_fx=today_fx)
        realized = realized_map.get(aid, 0.0)
        if top not in top_agg:
            top_agg[top] = {"mv": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0, "count": 0}
        top_agg[top]["mv"] += mv
        top_agg[top]["cost"] += cost
        top_agg[top]["unrealized"] += unrealized
        top_agg[top]["realized"] += realized
        top_agg[top]["count"] += 1
        key = (top, sub)
        if key not in sub_agg:
            sub_agg[key] = {"mv": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0, "count": 0}
        sub_agg[key]["mv"] += mv
        sub_agg[key]["cost"] += cost
        sub_agg[key]["unrealized"] += unrealized
        sub_agg[key]["realized"] += realized
        sub_agg[key]["count"] += 1
        total_mv += mv
        processed_assets.add(aid)

    sold_assets = [aid for aid in realized_map.keys() if aid not in processed_assets]
    if sold_assets:
        placeholders = ','.join(['?'] * len(sold_assets))
        class_query = f"""
            SELECT r.canonical_id,
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') as top_class,
                COALESCE(r.asset_class, 'Unclassified') as sub_class
            FROM asset_registry r
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE r.canonical_id IN ({placeholders})
        """
        class_rows = db.execute(class_query, sold_assets).fetchall()
        class_map = {row[0]: (resolve_top_class(row[1]), get_display_name(row[2])) for row in class_rows}
        for aid in sold_assets:
            top, sub = class_map.get(aid, ("Unclassified", "Unclassified"))
            realized = realized_map.get(aid, 0.0)
            if top not in top_agg:
                top_agg[top] = {"mv": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0, "count": 0}
            top_agg[top]["realized"] += realized
            key = (top, sub)
            if key not in sub_agg:
                sub_agg[key] = {"mv": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0, "count": 0}
            sub_agg[key]["realized"] += realized

    top_classes = []
    for top, data in top_agg.items():
        cost = data["cost"]
        unrealized = data["unrealized"]
        realized = data["realized"]
        lifetime = unrealized + realized
        ret_pct = (lifetime / cost * 100) if cost != 0 else 0.0
        weight_pct = (data["mv"] / total_mv * 100) if total_mv != 0 else 0.0
        top_classes.append({"class_name": top, "market_value": data["mv"], "cost_basis": cost,
                            "unrealized_pl": unrealized, "realized_pl": realized, "lifetime_pl": lifetime,
                            "return_pct": round(ret_pct, 2), "weight_pct": round(weight_pct, 1),
                            "asset_count": data["count"]})
    top_classes = [c for c in top_classes if not (c["class_name"] == "Unclassified" and c["asset_count"] == 0)]
    top_classes.sort(key=lambda x: x["market_value"], reverse=True)

    sub_classes = []
    for (top, sub), data in sub_agg.items():
        cost = data["cost"]
        unrealized = data["unrealized"]
        realized = data["realized"]
        lifetime = unrealized + realized
        ret_pct = (lifetime / cost * 100) if cost != 0 else 0.0
        weight_pct = (data["mv"] / total_mv * 100) if total_mv != 0 else 0.0
        sub_classes.append({"top_class": top, "sub_class": sub, "market_value": data["mv"], "cost_basis": cost,
                            "unrealized_pl": unrealized, "realized_pl": realized, "lifetime_pl": lifetime,
                            "return_pct": round(ret_pct, 2), "weight_pct": round(weight_pct, 1),
                            "asset_count": data["count"]})
    sub_classes.sort(key=lambda x: x["market_value"], reverse=True)

    if exclude:
        top_classes = [item for item in top_classes if not is_non_balanceable_class(item["class_name"])]
        kept = {item["class_name"] for item in top_classes}
        sub_classes = [item for item in sub_classes
                       if item["top_class"] in kept and not is_non_balanceable_class(item["top_class"])]
        filtered_total_mv = sum(item["market_value"] for item in top_classes)
        for item in top_classes:
            item["weight_pct"] = round(item["market_value"] / filtered_total_mv * 100, 1) if filtered_total_mv != 0 else 0.0
        for item in sub_classes:
            item["weight_pct"] = round(item["market_value"] / filtered_total_mv * 100, 1) if filtered_total_mv != 0 else 0.0
        total_mv = filtered_total_mv

    total_cost_basis = sum(c["cost_basis"] for c in top_classes)
    return {"total_market_value": total_mv, "total_cost_basis": total_cost_basis,
            "top_classes": top_classes, "sub_classes": sub_classes}


@pytest.fixture
def frozen_fx(monkeypatch):
    import src.services.pnl.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)


def _new(db, period, exclude):
    return asyncio.new_event_loop().run_until_complete(
        perf.get_performance_by_class(period=period, exclude_non_balanceable=exclude,
                                      include_non_rebalanceable=not exclude, db=db))


def _strip_cn_fields(payload: dict) -> dict:
    """Program BIL / WS-9 added additive ``*_cn`` companion fields (class_name_cn,
    top_class_cn, sub_class_cn) to /performance/by-class — a DELIBERATE divergence
    from the frozen V7.8.3 legacy shape, not drift. ``_legacy_by_class`` stays a
    verbatim copy of the pre-engine loop (it must NOT grow bilingual awareness),
    so strip the companions from the engine output before the byte-parity
    comparison. Every other field must still match exactly."""
    stripped = {**payload}
    for key in ("top_classes", "sub_classes"):
        stripped[key] = [
            {k: v for k, v in row.items() if not k.endswith("_cn")}
            for row in payload[key]
        ]
    return stripped


@pytest.mark.parametrize("exclude", [False, True])
def test_engine_by_class_equals_frozen_legacy(tmp_path, frozen_fx, exclude):
    db_path = tmp_path / "by_class_parity.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        legacy = _legacy_by_class(db, "all_time", exclude)
        new = _new(db, "all_time", exclude)
    finally:
        db.close()
    assert _strip_cn_fields(new) == legacy


# ── Hand-checked constants (FX = 7.1, all-time, include everything) ───────────
# Equity realized = CN_FUND_A 1500 + US_STK_SOLD (60-50)*100*7.1 = 7100 → 8600.
def _golden_top(resolve):
    return {
        resolve("Fixed Income"): {"market_value": 200000.0, "cost_basis": 0.0, "unrealized_pl": 0.0,
                                  "realized_pl": 0.0, "lifetime_pl": 0.0, "return_pct": 0.0, "asset_count": 1},
        resolve("Equity"): {"market_value": 192000.0, "cost_basis": 616000.0, "unrealized_pl": -424000.0,
                            "realized_pl": 8600.0, "lifetime_pl": -415400.0, "asset_count": 2},
        resolve("Money Market"): {"market_value": 20000.0, "cost_basis": 20000.0, "unrealized_pl": 0.0,
                                  "realized_pl": 0.0, "lifetime_pl": 0.0, "return_pct": 0.0, "asset_count": 1},
        resolve("Insurance"): {"market_value": 30000.0, "cost_basis": 25000.0, "unrealized_pl": 5000.0,
                               "realized_pl": 0.0, "lifetime_pl": 5000.0, "return_pct": 20.0, "asset_count": 1},
    }


def test_by_class_matches_golden_constants(tmp_path, frozen_fx):
    db_path = tmp_path / "by_class_golden.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = _new(db, "all_time", False)  # include everything
    finally:
        db.close()

    by_name = {c["class_name"]: c for c in res["top_classes"]}
    golden = _golden_top(resolve_top_class)
    for name, expected in golden.items():
        assert name in by_name, f"missing top class {name!r}"
        got = by_name[name]
        for k, v in expected.items():
            if isinstance(v, float):
                assert got[k] == pytest.approx(v, abs=0.01), f"{name}.{k}: {got[k]} != {v}"
            else:
                assert got[k] == v, f"{name}.{k}: {got[k]!r} != {v!r}"

    # Totals: displayed market value + sum of displayed class cost bases.
    assert res["total_market_value"] == pytest.approx(442000.0)   # incl. Bond_BAL 200000
    assert res["total_cost_basis"] == pytest.approx(661000.0)     # 616000 + 20000 + 0 + 25000

    # Sorted by market_value DESC.
    assert [c["class_name"] for c in res["top_classes"]] == [
        resolve_top_class("Fixed Income"), resolve_top_class("Equity"),
        resolve_top_class("Insurance"), resolve_top_class("Money Market"),
    ]

    # Closed asset's realized landed in Equity's US Equity sub-class (no MV, no count).
    us_eq = next(s for s in res["sub_classes"]
                 if s["sub_class"] == get_display_name("US Equity"))
    assert us_eq["realized_pl"] == pytest.approx(7100.0)   # US_STK_SOLD realized
    assert us_eq["market_value"] == pytest.approx(142000.0)  # only VOO's MV
    assert us_eq["asset_count"] == 1                          # closed asset NOT counted


def test_balance_only_class_phantom_guard(tmp_path, frozen_fx):
    """The Fixed-Income class KEEPS the bond's market value + weight but books
    0 cost and 0 unrealized. Breaking the engine's balance-only zeroing (charging
    the bond in at cost) turns this red — the ¥386K phantom regression."""
    db_path = tmp_path / "by_class_phantom.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = _new(db, "all_time", False)
    finally:
        db.close()

    fi = next(c for c in res["top_classes"] if c["class_name"] == resolve_top_class("Fixed Income"))
    # Value + weight retained (200000 / 442000 ≈ 45.2%) ...
    assert fi["market_value"] == pytest.approx(200000.0)
    assert fi["weight_pct"] == pytest.approx(45.2, abs=0.1)
    assert fi["asset_count"] == 1
    # ... but cost, unrealized, lifetime and return are ZERO — not a phantom gain.
    assert fi["cost_basis"] == 0.0
    assert fi["unrealized_pl"] == 0.0
    assert fi["lifetime_pl"] == 0.0
    assert fi["return_pct"] == 0.0


def test_exclude_non_balanceable_drops_class_and_reweights(tmp_path, frozen_fx):
    """exclude_non_balanceable drops the Insurance class and recomputes weights
    against the filtered total (Insurance's 30000 leaves the denominator)."""
    db_path = tmp_path / "by_class_exclude.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    try:
        res = _new(db, "all_time", True)
    finally:
        db.close()

    names = {c["class_name"] for c in res["top_classes"]}
    assert resolve_top_class("Insurance") not in names
    assert res["total_market_value"] == pytest.approx(412000.0)   # 442000 − INS 30000
    # Fixed Income reweighted against 412000: 200000/412000 ≈ 48.5%.
    fi = next(c for c in res["top_classes"] if c["class_name"] == resolve_top_class("Fixed Income"))
    assert fi["weight_pct"] == pytest.approx(48.5, abs=0.1)
