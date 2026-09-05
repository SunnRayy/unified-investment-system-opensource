from src.import_adapters.mapping import infer_mapping, missing_required_fields, required_fields


def test_infer_holdings_mapping():
    headers = ["symbol", "name", "date", "qty", "price", "value", "currency", "account"]
    m = infer_mapping(headers, "holdings")
    assert m["asset_id"] == "symbol"
    assert m["snapshot_date"] == "date"


def test_infer_transactions_mapping():
    headers = ["symbol", "name", "trade_date", "action", "qty", "price", "amount", "fee", "currency", "account", "memo"]
    m = infer_mapping(headers, "transactions")
    assert m["asset_id"] == "symbol"
    assert m["transaction_type"] == "action"


def test_missing_required_fields():
    # snapshot_date is optional for holdings (auto-injected as today during staging)
    assert "quantity" in missing_required_fields({"asset_id": "a"}, "holdings")
    assert "snapshot_date" not in missing_required_fields({"asset_id": "a"}, "holdings")
    assert required_fields("holdings") == {"asset_id", "quantity"}
    assert required_fields("transactions") == {"asset_id", "transaction_date", "transaction_type"}
