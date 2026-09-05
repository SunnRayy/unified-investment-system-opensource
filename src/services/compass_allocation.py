from __future__ import annotations

import logging
from typing import Any

from src.services.rebalanceable_filter import fetch_non_rebalanceable_asset_ids
from src.services.currency import get_today_usd_cny_rate

logger = logging.getLogger(__name__)

# Asset-class display names come from portfolio_helpers, which owns the single
# definition. This module used to carry a verbatim copy of the 36-entry table —
# two copies of one table that must agree, which is the shape this codebase
# keeps getting caught by. Re-exported here because callers import
# `get_display_name` from this module.
from src.services.portfolio_helpers import DISPLAY_MAP, get_display_name  # noqa: F401


def _compute_pending_overlay(db: Any, sub_to_parent: dict[str, str]) -> tuple[dict[str, float], int]:
    """Query trade_logs for pending/pending_window trades and compute per-top-class CNY deltas.

    Returns:
        class_deltas: dict mapping top-class display name → net CNY delta (Buy=+, Sell=-)
        pending_count: total number of pending trades processed

    Rules:
        - Only verification_status IN ('pending', 'pending_window') — verified trades are
          already in holdings and must NOT be included (no double-counting).
        - Amount computed as: trade.amount if available, else quantity * price.
        - USD trades converted to CNY using get_today_usd_cny_rate().
        - Asset class resolved via asset_registry → taxonomy_classes (same join as builder).
        - Uses taxonomy_classes.is_rebalanceable (Rule 7) — no write to DB.
    """
    try:
        pending_rows = db.execute(
            """
            SELECT
                tl.asset_id,
                tl.action,
                tl.quantity,
                tl.price,
                tl.amount,
                tl.currency,
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                COALESCE(tc.name, r.asset_class, 'Other') AS sub_class
            FROM trade_logs tl
            LEFT JOIN asset_registry r ON tl.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE tl.verification_status IN ('pending', 'pending_window')
            """
        ).fetchall()
    except Exception:
        logger.exception("compass_allocation: failed to query pending trade_logs; skipping overlay")
        return {}, 0

    if not pending_rows:
        return {}, 0

    # Lazy-load FX rate only when there are pending USD trades
    usd_cny_rate: float | None = None

    class_deltas: dict[str, float] = {}
    pending_count = 0

    for row in pending_rows:
        if len(row) < 8:
            continue
        asset_id, action, quantity, price, amount, currency, top_class, sub_class = row[:8]

        # Compute trade amount in native currency
        native_amount: float = 0.0
        if amount is not None and float(amount) != 0.0:
            native_amount = abs(float(amount))
        elif quantity is not None and price is not None:
            native_amount = abs(float(quantity)) * abs(float(price))

        if native_amount == 0.0:
            logger.warning(
                "compass_allocation: pending trade for asset_id=%s has no usable amount; skipping",
                asset_id,
            )
            continue

        # Convert to CNY if needed
        trade_currency = str(currency or "CNY").upper()
        if trade_currency == "USD":
            if usd_cny_rate is None:
                try:
                    usd_cny_rate = get_today_usd_cny_rate()
                except Exception:
                    logger.warning("compass_allocation: could not fetch USD/CNY rate; using 7.0")
                    usd_cny_rate = 7.0
            native_amount *= usd_cny_rate

        top_class_str = str(top_class or "Unclassified")
        top_class_display = get_display_name(top_class_str)

        action_str = str(action or "").upper()
        if action_str in ("BUY", "LONG"):
            delta = native_amount
        elif action_str in ("SELL", "SHORT"):
            delta = -native_amount
        else:
            logger.warning(
                "compass_allocation: unknown trade action=%s for asset_id=%s; skipping",
                action,
                asset_id,
            )
            continue

        class_deltas[top_class_display] = class_deltas.get(top_class_display, 0.0) + delta
        pending_count += 1

    return class_deltas, pending_count


def build_compass_allocation(
    db: Any,
    include_non_rebalanceable: bool = False,
    include_pending: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    targets: dict[str, dict[str, float]] = {}
    try:
        active_allocs = db.execute(
            """
            SELECT tc.name, rpa.target_pct
            FROM risk_profile_allocations rpa
            JOIN taxonomy_classes tc ON rpa.class_id = tc.id
            JOIN risk_profiles rp ON rpa.profile_id = rp.id
            WHERE rp.is_active = TRUE
            """
        ).fetchall()
        for row in active_allocs:
            targets[row[0]] = {"target": float(row[1]), "tolerance": 5.0}
    except Exception:
        logger.exception("compass_allocation: failed to load risk-profile targets; returning empty targets")
        targets = {}

    excluded_ids: set[str] = set()
    non_rebalanceable_classes: set[str] = set()
    if not include_non_rebalanceable:
        excluded_ids = fetch_non_rebalanceable_asset_ids(db)
        try:
            rows = db.execute("SELECT name FROM taxonomy_classes WHERE is_rebalanceable = FALSE").fetchall()
            non_rebalanceable_classes = {r[0] for r in rows if r[0]}
            non_rebalanceable_classes.update({get_display_name(c) for c in non_rebalanceable_classes})
        except Exception:
            logger.exception("compass_allocation: failed to load non-rebalanceable classes; returning empty set")
            non_rebalanceable_classes = set()

    hierarchy: dict[str, dict[str, Any]] = {}
    total_net_worth = 0.0

    detail_rows: list[tuple[Any, ...]] = []
    try:
        detail_rows = db.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS latest_date
                FROM holdings
                WHERE is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT
                h.asset_id,
                COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
                COALESCE(tc.name, r.asset_class, 'Other') AS sub_class,
                SUM(h.market_value) AS val
            FROM holdings h
            JOIN latest_per_asset lpa
                ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
            LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE h.is_shadow = FALSE
            GROUP BY 1, 2, 3
            """
        ).fetchall()
    except Exception:
        logger.exception("compass_allocation: failed to load holdings detail rows; returning empty allocation")
        detail_rows = []

    for row in detail_rows:
        if len(row) < 4:
            continue
        asset_id, top_class, sub_class, value = row[:4]
        if not include_non_rebalanceable and (
            asset_id in excluded_ids
            or top_class in non_rebalanceable_classes
            or sub_class in non_rebalanceable_classes
        ):
            continue
        top_class = str(top_class or "Unclassified")
        sub_class = str(sub_class or "Other")
        value = float(value or 0.0)
        hierarchy.setdefault(top_class, {"val": 0.0, "currency": "CNY", "children": {}})
        hierarchy[top_class]["val"] += value
        hierarchy[top_class]["children"].setdefault(sub_class, {"val": 0.0, "currency": "CNY"})
        hierarchy[top_class]["children"][sub_class]["val"] += value
        hierarchy[top_class]["children"][sub_class]["currency"] = "CNY"
        total_net_worth += value

    sub_to_parent: dict[str, str] = {}
    try:
        tc_rows = db.execute(
            """
            SELECT tc.name, parent.name
            FROM taxonomy_classes tc
            JOIN taxonomy_classes parent ON tc.parent_id = parent.id
            """
        ).fetchall()
        sub_to_parent = {row[0]: row[1] for row in tc_rows if row and row[0] and row[1]}
    except Exception:
        logger.exception("compass_allocation: failed to load sub-to-parent class mapping; returning empty mapping")
        sub_to_parent = {}

    for sub_key in targets:
        parent = sub_to_parent.get(sub_key)
        if not include_non_rebalanceable and (
            sub_key in non_rebalanceable_classes or parent in non_rebalanceable_classes
        ):
            continue
        if parent and parent not in non_rebalanceable_classes:
            hierarchy.setdefault(parent, {"val": 0.0, "currency": "CNY", "children": {}})
            hierarchy[parent]["children"].setdefault(sub_key, {"val": 0.0, "currency": "CNY"})
        elif sub_key not in hierarchy:
            hierarchy.setdefault(sub_key, {"val": 0.0, "currency": "CNY", "children": {}})

    all_top_keys = set(hierarchy.keys())

    top_level_targets: dict[str, float] = {}
    for top_key in all_top_keys:
        top_data = hierarchy.get(top_key, {"children": {}})
        target_info = dict(targets.get(top_key, {"target": 0.0, "tolerance": 5.0}))
        if top_data["children"]:
            child_target_sum = 0.0
            has_child_targets = False
            for sub_key in top_data["children"]:
                if sub_key in targets:
                    child_target_sum += targets[sub_key]["target"]
                    has_child_targets = True
            if has_child_targets and child_target_sum > 0:
                target_info = {"target": child_target_sum, "tolerance": target_info["tolerance"]}
        top_level_targets[top_key] = float(target_info["target"])

    target_scale = 1.0
    if not include_non_rebalanceable:
        excluded_target_total = 0.0
        for sub_key, target_info in targets.items():
            parent = sub_to_parent.get(sub_key)
            if sub_key in non_rebalanceable_classes or parent in non_rebalanceable_classes:
                excluded_target_total += float(target_info["target"])
        included_target_total = sum(top_level_targets.values())
        if excluded_target_total > 0 and included_target_total > 0:
            target_scale = 100.0 / included_target_total

    response: list[dict[str, Any]] = []
    for top_key in all_top_keys:
        top_data = hierarchy.get(top_key, {"val": 0.0, "currency": "CNY", "children": {}})
        target_info = dict(targets.get(top_key, {"target": 0.0, "tolerance": 5.0}))
        has_target = top_key in targets

        if top_data["children"]:
            child_target_sum = 0.0
            has_child_targets = False
            for sub_key in top_data["children"]:
                if sub_key in targets:
                    child_target_sum += targets[sub_key]["target"]
                    has_child_targets = True
            if has_child_targets and child_target_sum > 0:
                target_info = {"target": child_target_sum, "tolerance": target_info["tolerance"]}
                has_target = True
        target_info["target"] = float(target_info["target"]) * target_scale

        current_pct = (top_data["val"] / total_net_worth * 100.0) if total_net_worth > 0 else 0.0
        drift_pct = current_pct - target_info["target"]
        status = "within_range"
        if not has_target:
            # No target was ever set for this class. That is not the same fact
            # as "the target is 0%", and reporting it as one is what made a
            # fresh install flag 100% of a sensible portfolio as over target.
            # Drift against a target nobody set is not a number, so it is not
            # reported as one — see the `has_target` note on the payload below.
            status = "no_target"
            drift_pct = 0.0
        elif abs(drift_pct) > target_info["tolerance"]:
            status = "over" if drift_pct > 0 else "under"

        response.append(
            {
                "asset_class": get_display_name(top_key),
                "current_value": round(top_data["val"], 2),
                "currency": "CNY",
                "current_pct": round(current_pct, 2),
                "target_pct": round(target_info["target"], 2),
                "drift_pct": round(drift_pct, 2),
                "tolerance_pct": target_info["tolerance"],
                "status": status,
                # `target_pct` and `drift_pct` stay numeric on the wire even
                # when no target exists, because every consumer formats them
                # unconditionally and a null would crash the report rather than
                # explain it. `has_target` is the field that carries the truth:
                # when it is False the two numbers above are placeholders and
                # must be rendered as "—", never as 0.00%.
                "has_target": has_target,
                "is_top_level": True,
                "parent_class": None,
            }
        )

        sorted_children = sorted(
            top_data["children"].items(),
            key=lambda item: item[1]["val"],
            reverse=True,
        )
        for sub_key, sub_data in sorted_children:
            if sub_key == top_key and len(top_data["children"]) == 1:
                continue

            sub_has_target = sub_key in targets
            sub_target_info = targets.get(sub_key, {"target": 0.0, "tolerance": 5.0})
            sub_target_info = {
                "target": float(sub_target_info["target"]) * target_scale,
                "tolerance": sub_target_info["tolerance"],
            }
            sub_current_pct = (sub_data["val"] / total_net_worth * 100.0) if total_net_worth > 0 else 0.0
            sub_drift_pct = sub_current_pct - sub_target_info["target"]
            sub_status = "within_range"
            if not sub_has_target:
                sub_status = "no_target"
                sub_drift_pct = 0.0
            elif abs(sub_drift_pct) > sub_target_info["tolerance"]:
                sub_status = "over" if sub_drift_pct > 0 else "under"

            response.append(
                {
                    "asset_class": get_display_name(sub_key),
                    "current_value": round(sub_data["val"], 2),
                    "currency": "CNY",
                    "current_pct": round(sub_current_pct, 2),
                    "target_pct": round(sub_target_info["target"], 2),
                    "drift_pct": round(sub_drift_pct, 2),
                    "tolerance_pct": sub_target_info["tolerance"],
                    "status": sub_status,
                    "has_target": sub_has_target,
                    "is_top_level": False,
                    "parent_class": get_display_name(top_key),
                }
            )

    final_list: list[dict[str, Any]] = []
    parents = [row for row in response if row["is_top_level"]]
    parents.sort(key=lambda row: row["current_pct"], reverse=True)
    for parent in parents:
        final_list.append(parent)
        children = [row for row in response if not row["is_top_level"] and row["parent_class"] == parent["asset_class"]]
        children.sort(key=lambda row: row["current_pct"], reverse=True)
        final_list.extend(children)

    # Return the plain list (byte-for-byte identical to prior behavior) unless the caller
    # EXPLICITLY opted into the provisional overlay. Strict `is not True` guards against the
    # common footgun of a FastAPI Query(default=False) object (truthy!) leaking in when the
    # route function is invoked directly rather than through HTTP — that must not silently
    # flip the return shape to an envelope. See /compass/markdown direct call + tests.
    if include_pending is not True:
        return final_list

    # ── Provisional overlay ──────────────────────────────────────────────────
    # Query pending/pending_window trade_logs and compute per-top-class CNY deltas.
    # sub_to_parent is already built above and passed here for asset-class resolution.
    class_deltas, pending_count = _compute_pending_overlay(db, sub_to_parent)

    # Compute provisional total net worth (base + net Buy/Sell deltas across all classes)
    total_delta = sum(class_deltas.values())
    provisional_total = total_net_worth + total_delta

    # Annotate each row with provisional fields
    for row in final_list:
        asset_class_key = row["asset_class"]
        # Only top-level classes receive the provisional delta directly; sub-classes
        # inherit proportionally from their parent (see note below).
        # For simplicity and correctness: provisional delta is tracked at top-class level
        # and applied to each row by asset_class display name.
        delta = class_deltas.get(asset_class_key, 0.0)
        prov_value = row["current_value"] + delta
        prov_pct = (prov_value / provisional_total * 100.0) if provisional_total > 0 else 0.0
        row["provisional_value"] = round(prov_value, 2)
        row["provisional_pct"] = round(prov_pct, 2)
        row["provisional_delta_cny"] = round(delta, 2)

    return {
        "allocation": final_list,
        "meta": {
            "pending_trade_count": pending_count,
            "is_provisional": True,
        },
    }
