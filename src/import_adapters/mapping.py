from __future__ import annotations


HOLDINGS_CANONICAL_FIELDS = [
    "asset_id", "asset_name", "snapshot_date", "quantity", "market_price_unit",
    "market_value", "currency", "account",
]

TRANSACTION_CANONICAL_FIELDS = [
    "asset_id", "asset_name", "transaction_date", "transaction_type", "quantity",
    "price_unit", "amount_gross", "commission_fee", "currency", "account", "memo",
]

ALIASES = {
    "asset_id": {"asset_id", "symbol", "ticker", "code"},
    "asset_name": {"asset_name", "name", "asset"},
    "snapshot_date": {"snapshot_date", "date", "holding_date"},
    "transaction_date": {"transaction_date", "date", "trade_date"},
    "transaction_type": {"transaction_type", "type", "action"},
    "quantity": {"quantity", "qty", "shares"},
    "market_price_unit": {"market_price", "price", "unit_price"},
    "market_value": {"market_value", "value", "amount"},
    "price_unit": {"price", "price_unit", "unit_price"},
    "amount_gross": {"amount", "gross", "amount_gross"},
    "commission_fee": {"fee", "commission", "commission_fee"},
    "currency": {"currency", "ccy"},
    "account": {"account", "account_name"},
    "memo": {"memo", "note", "description"},
}


def required_fields(import_type: str) -> set[str]:
    if import_type == "holdings":
        # snapshot_date is optional — defaults to today during staging when not mapped
        return {"asset_id", "quantity"}
    if import_type == "transactions":
        return {"asset_id", "transaction_date", "transaction_type"}
    raise ValueError(f"Unsupported import type: {import_type}")


def infer_mapping(headers: list[str], import_type: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lowered = {h.lower().strip(): h for h in headers}
    for field, names in ALIASES.items():
        for n in names:
            if n in lowered:
                mapping[field] = lowered[n]
                break
    allowed = set(HOLDINGS_CANONICAL_FIELDS if import_type == "holdings" else TRANSACTION_CANONICAL_FIELDS)
    return {k: v for k, v in mapping.items() if k in allowed}


def missing_required_fields(mapping: dict[str, str], import_type: str) -> list[str]:
    return sorted([field for field in required_fields(import_type) if field not in mapping])
