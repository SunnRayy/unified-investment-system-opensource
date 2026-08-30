"""Tests for the IBKR Flex Query reader (Workstream C1).

Covers:
  - _parse_flex_sections unit test (section parsing correctness)
  - Holdings: 3 rows (VTI, IEFA, CASH_USD); broker-agnostic asset IDs;
    market_value in CNY; source_system='Broker_IBKR'; account='IBKR_U0000123'
  - Transactions: 0 trades + 3 transfer_in rows (ACATS); NEVER buy/sell
  - Config: ibkr.yaml loads; format=flex_csv; hooks set

HARD CONSTRAINT: no test may instantiate DatabaseConnector or open
data/unified.duckdb (project DB-safety rule).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.pipeline

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "readers"
IBKR_FIXTURE_DIR = FIXTURE_DIR / "ibkr"
# Second fixture: a report that DOES contain TRNT trades (the ACATS-only fixture
# above mirrors the first production report and has an empty TRNT section, so it
# cannot exercise the buy/sell money path at all — that blind spot is how the
# amount_net=0.00 bug shipped).
IBKR_TRADES_FIXTURE_DIR = FIXTURE_DIR / "ibkr_trades"
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "readers"
IBKR_YAML = CONFIG_DIR / "ibkr.yaml"

# FX rate to patch — deterministic test assertions
_MOCK_FX = {"USD": 7.0}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ibkr_config():
    from src.sources.reader_config import load_reader_config
    return load_reader_config(IBKR_YAML)


@pytest.fixture(scope="module")
def ibkr_read_transform():
    """Run ConfigDrivenReader.read() + .transform() on the IBKR fixture directory.

    Patches fetch_fx_rates → {"USD": 7.0} for deterministic CNY assertions.
    """
    from src.sources.reader_config import load_reader_config
    from src.sources.config_driven_reader import ConfigDrivenReader

    cfg = load_reader_config(IBKR_YAML)
    reader = ConfigDrivenReader(cfg)

    with patch(
        "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
        return_value=_MOCK_FX,
    ):
        source_data = reader.read(IBKR_FIXTURE_DIR)
        holdings_df, transactions_df = reader.transform(source_data)

    return holdings_df, transactions_df, source_data.metadata


@pytest.fixture(scope="module")
def ibkr_trades_read_transform():
    """Same, on the TRNT-bearing fixture (1 buy + 1 sell + 1 ACATS transfer)."""
    from src.sources.reader_config import load_reader_config
    from src.sources.config_driven_reader import ConfigDrivenReader

    cfg = load_reader_config(IBKR_YAML)
    reader = ConfigDrivenReader(cfg)

    with patch(
        "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
        return_value=_MOCK_FX,
    ):
        source_data = reader.read(IBKR_TRADES_FIXTURE_DIR)
        holdings_df, transactions_df = reader.transform(source_data)

    return holdings_df, transactions_df, source_data.metadata


# ---------------------------------------------------------------------------
# Unit test: _parse_flex_sections
# ---------------------------------------------------------------------------

class TestParseFlexSections:
    def test_sections_present(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        flex_file = IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv"
        assert flex_file.exists(), f"Fixture missing: {flex_file}"
        sections = _parse_flex_sections(flex_file)

        assert "POST" in sections, "Expected POST section"
        assert "CRTT" in sections, "Expected CRTT section"
        assert "TRNT" in sections, "Expected TRNT section"
        assert "TRFR" in sections, "Expected TRFR section"

    def test_post_row_count(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        sections = _parse_flex_sections(IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv")
        assert len(sections["POST"]) == 2, "POST must have 2 position rows (persona: VTI, IEFA)"

    def test_crtt_row_count(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        sections = _parse_flex_sections(IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv")
        assert len(sections["CRTT"]) == 1, "CRTT must have 1 cash row"

    def test_trnt_empty(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        sections = _parse_flex_sections(IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv")
        assert len(sections["TRNT"]) == 0, "TRNT must be empty (no trades in fixture)"

    def test_trfr_row_count(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        sections = _parse_flex_sections(IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv")
        assert len(sections["TRFR"]) == 3, "TRFR must have 3 transfer rows"

    def test_post_has_symbol_column(self):
        from src.sources.config_driven_reader import _parse_flex_sections

        sections = _parse_flex_sections(IBKR_FIXTURE_DIR / "IBKR_UIS_Report.csv")
        assert "Symbol" in sections["POST"].columns


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestIbkrConfig:
    def test_yaml_loads(self, ibkr_config):
        assert ibkr_config.identity.source_key == "ibkr"
        assert ibkr_config.identity.source_system == "Broker_IBKR"

    def test_format_flex_csv(self, ibkr_config):
        assert ibkr_config.parsing is not None
        assert ibkr_config.parsing.format == "flex_csv"

    def test_hooks_set(self, ibkr_config):
        assert ibkr_config.parsing.holdings_from_sheet_hook == "ibkr_holdings_from_flex"
        assert ibkr_config.parsing.transactions_from_sheet_hook == "ibkr_transactions_from_flex"

    def test_category_reader(self, ibkr_config):
        assert ibkr_config.identity.category == "reader"

    def test_validator_set(self, ibkr_config):
        assert ibkr_config.identity.validator == "validate_ibkr_format"


# ---------------------------------------------------------------------------
# Holdings tests
# ---------------------------------------------------------------------------

class TestIbkrHoldings:
    def test_holdings_row_count(self, ibkr_read_transform):
        holdings_df, _, _ = ibkr_read_transform
        assert len(holdings_df) == 3, (
            f"Expected 3 holdings rows (VTI, IEFA, CASH_USD), got {len(holdings_df)}: "
            f"{holdings_df['asset_id'].tolist() if not holdings_df.empty else '(empty)'}"
        )

    def test_asset_ids_broker_agnostic(self, ibkr_read_transform):
        """Asset IDs must be broker-agnostic (same as Schwab holdings): US_STK_VTI not IBKR_VTI."""
        holdings_df, _, _ = ibkr_read_transform
        ids = set(holdings_df["asset_id"].tolist())
        assert "US_STK_VTI" in ids, f"Expected US_STK_VTI in {ids}"
        assert "US_STK_IEFA" in ids, f"Expected US_STK_IEFA in {ids}"
        assert "CASH_USD" in ids, f"Expected CASH_USD in {ids}"

    def test_ids_match_schwab_holdings_convention(self, ibkr_read_transform):
        """IBKR must emit the SAME canonical IDs Schwab holdings use (US_STK_*).

        Schwab's CSV classifies VTI/IEFA as stock (not ETF), so Schwab
        holdings live under US_STK_*. Co-authority requires one shared asset_id
        per asset across brokers, so IBKR must match — NOT mint US_ETF_* (which
        would be a different, FX-uncorrected asset). ETF-ness is recorded in
        asset_registry.asset_class, not the ID prefix. (ADR-016.)
        """
        holdings_df, _, _ = ibkr_read_transform
        ids = set(holdings_df["asset_id"].tolist())
        assert {"US_STK_VTI", "US_STK_IEFA"} <= ids
        assert not any(i.startswith("US_ETF_") for i in ids), (
            f"IBKR must not mint US_ETF_* ids: {ids}"
        )

    def test_source_system_all_broker_ibkr(self, ibkr_read_transform):
        holdings_df, _, _ = ibkr_read_transform
        assert (holdings_df["source_system"] == "Broker_IBKR").all(), (
            f"All source_system must be Broker_IBKR; got: {holdings_df['source_system'].unique()}"
        )

    def test_account_all_ibkr_u0000123(self, ibkr_read_transform):
        """persona.identity.ibkr_account = U0000123 (tools/demo_data/persona.yaml)."""
        holdings_df, _, _ = ibkr_read_transform
        assert (holdings_df["account"] == "IBKR_U0000123").all(), (
            f"All account must be IBKR_U0000123; got: {holdings_df['account'].unique()}"
        )

    def test_vti_quantity(self, ibkr_read_transform):
        """persona.ibkr.positions[0] = VTI, qty 30 (generator's persisted seed output)."""
        holdings_df, _, _ = ibkr_read_transform
        vti = holdings_df[holdings_df["asset_id"] == "US_STK_VTI"]
        assert len(vti) == 1
        assert float(vti.iloc[0]["quantity"]) == 30.0

    def test_vti_market_value_cny(self, ibkr_read_transform):
        """VTI PositionValueInBase=7649.70 (generator's seed output); ×7.0 FX → 53547.90 CNY."""
        holdings_df, _, _ = ibkr_read_transform
        vti = holdings_df[holdings_df["asset_id"] == "US_STK_VTI"]
        assert len(vti) == 1
        assert abs(float(vti.iloc[0]["market_value"]) - 53547.9) < 0.01, (
            f"VTI market_value must be 53547.9 CNY, got {vti.iloc[0]['market_value']}"
        )

    def test_cash_usd_market_value_cny(self, ibkr_read_transform):
        """CASH_USD EndingCash=1000; ×7.0 FX → 7000.0 CNY."""
        holdings_df, _, _ = ibkr_read_transform
        cash = holdings_df[holdings_df["asset_id"] == "CASH_USD"]
        assert len(cash) == 1
        assert abs(float(cash.iloc[0]["market_value"]) - 7000.0) < 0.01, (
            f"CASH_USD market_value must be 7000.0 CNY (1000×7.0), got {cash.iloc[0]['market_value']}"
        )

    def test_snapshot_date_from_post(self, ibkr_read_transform):
        """Snapshot date must come from POST ReportDate = 2026-06-12."""
        holdings_df, _, _ = ibkr_read_transform
        dates = holdings_df["snapshot_date"].unique().tolist()
        assert "2026-06-12" in [str(d)[:10] for d in dates], (
            f"Expected snapshot_date=2026-06-12 from POST ReportDate, got: {dates}"
        )

    def test_required_columns_present(self, ibkr_read_transform):
        holdings_df, _, _ = ibkr_read_transform
        required = {
            "asset_id", "quantity", "market_price_unit", "market_value",
            "cost_price_unit", "gain_dollar", "gain_percent",
            "snapshot_date", "source_system", "account",
        }
        missing = required - set(holdings_df.columns)
        assert not missing, f"Missing holdings columns: {missing}"


# ---------------------------------------------------------------------------
# Transactions tests
# ---------------------------------------------------------------------------

class TestIbkrTransactions:
    def test_transactions_row_count(self, ibkr_read_transform):
        """Fixture: 0 trades + 3 ACATS transfers = 3 total rows."""
        _, transactions_df, _ = ibkr_read_transform
        assert len(transactions_df) == 3, (
            f"Expected 3 transaction rows (0 trades + 3 transfers), got {len(transactions_df)}"
        )

    def test_all_transaction_type_transfer_in(self, ibkr_read_transform):
        """ACATS transfers must be transaction_type='transfer_in', NEVER buy/sell."""
        _, transactions_df, _ = ibkr_read_transform
        types = transactions_df["transaction_type"].unique().tolist()
        assert set(types) == {"transfer_in"}, (
            f"All transactions must be transfer_in; got: {types}"
        )
        assert "buy" not in types, "ACATS transfers must NOT be classified as 'buy'"
        assert "sell" not in types, "ACATS transfers must NOT be classified as 'sell'"

    def test_iefa_transfer_quantity(self, ibkr_read_transform):
        """persona.ibkr.positions[1] = IEFA, single-row ACATS transfer of the full 60 qty."""
        _, transactions_df, _ = ibkr_read_transform
        iefa_txns = transactions_df[transactions_df["asset_id"] == "US_STK_IEFA"]
        assert len(iefa_txns) == 1
        assert abs(float(iefa_txns.iloc[0]["quantity"]) - 60.0) < 0.01

    def test_transfer_price_zero(self, ibkr_read_transform):
        """ACATS transfers have price_unit=0 (non-realizing events)."""
        _, transactions_df, _ = ibkr_read_transform
        assert (transactions_df["price_unit"] == 0.0).all()

    def test_transfer_amount_zero(self, ibkr_read_transform):
        """ACATS share transfers move shares, not cash — 0.00 is CORRECT here.

        The matching Schwab legs are transfer_out rows with no cash either. Do not
        "repair" these alongside the trade-amount fix.
        """
        _, transactions_df, _ = ibkr_read_transform
        assert (transactions_df["amount_gross"] == 0.0).all()
        assert (transactions_df["commission_fee"] == 0.0).all()

    def test_required_columns_present(self, ibkr_read_transform):
        """Emitted names must be the INGEST contract names, not reader-local aliases.

        Regression guard for the amount_net=0.00 bug: the hook used to emit
        price/amount/fees/description, none of which _normalize_transactions_df
        reads for Broker_IBKR (it has no rename_map branch), so every money field
        was silently dropped.
        """
        _, transactions_df, _ = ibkr_read_transform
        required = {
            "asset_id", "transaction_date", "transaction_type",
            "quantity", "price_unit", "amount_gross", "commission_fee",
            "memo", "source_system",
        }
        missing = required - set(transactions_df.columns)
        assert not missing, f"Missing transaction columns: {missing}"
        forbidden = {"price", "amount", "fees", "description"} & set(transactions_df.columns)
        assert not forbidden, (
            f"IBKR hook must not emit reader-local aliases {forbidden} — "
            "_normalize_transactions_df has no rename_map branch for Broker_IBKR, "
            "so these are silently dropped (amount_net becomes 0.00)."
        )

    def test_all_transactions_source_system(self, ibkr_read_transform):
        _, transactions_df, _ = ibkr_read_transform
        assert (transactions_df["source_system"] == "Broker_IBKR").all()

    def test_metadata_has_account_id(self, ibkr_read_transform):
        _, _, metadata = ibkr_read_transform
        assert metadata.get("account_id") == "U0000123", (
            f"Expected account_id=U0000123 in metadata, got: {metadata.get('account_id')}"
        )


# ---------------------------------------------------------------------------
# Trade amounts (WS-B) — the money path, asserted THROUGH the ingest normalizer
#
# Every Broker_IBKR transaction landed in the DB with amount_net=0.00 and
# price_unit=NULL because the hook emitted price/amount/fees while
# _normalize_transactions_df reads price_unit/amount_gross/commission_fee and has
# no rename_map branch for Broker_IBKR. Asserting only on the hook output would
# not have caught it — the two halves have to be tested joined.
# ---------------------------------------------------------------------------

class TestIbkrTradeAmounts:
    @staticmethod
    def _normalized(transactions_df):
        from src.sync.phases._ingest import _normalize_transactions_df
        return _normalize_transactions_df(transactions_df, "Broker_IBKR")

    def test_fixture_has_trades(self, ibkr_trades_read_transform):
        _, transactions_df, _ = ibkr_trades_read_transform
        types = set(transactions_df["transaction_type"])
        assert {"buy", "sell", "transfer_in"} <= types, (
            f"Trades fixture must exercise buy + sell + transfer; got {types}"
        )

    def test_buy_amount_gross_negative_matches_schwab_convention(
        self, ibkr_trades_read_transform
    ):
        """AGENTS.md Rule 26: buys are cash OUT → amount_gross NEGATIVE.

        Same convention Schwab_CSV stores (buy -2500.00 / sell +2399.95). Do not
        introduce a fourth per-reader sign dialect.
        """
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        buy = norm[norm["transaction_type"] == "buy"].iloc[0]
        # persona.ibkr.trades[0]: VTI BUY 10 @ 239.66 (generator's seed output) → -2396.60
        assert abs(float(buy["amount_gross"]) - (-2396.6)) < 0.01, buy.to_dict()
        assert float(buy["price_unit"]) == 239.66
        assert abs(float(buy["commission_fee"]) - 0.91) < 1e-6

    def test_buy_amount_net_is_gross_minus_commission(self, ibkr_trades_read_transform):
        """_ingest derives amount_net = amount_gross - commission_fee.

        Under the negative-buy convention that makes a buy MORE negative by the
        fee, which is correct: the commission is additional cash out.
        """
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        buy = norm[norm["transaction_type"] == "buy"].iloc[0]
        assert abs(float(buy["amount_net"]) - (-2397.51)) < 0.01, buy.to_dict()

    def test_sell_amount_gross_positive_net_reduced_by_commission(
        self, ibkr_trades_read_transform
    ):
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        sell = norm[norm["transaction_type"] == "sell"].iloc[0]
        # persona.ibkr.trades[1]: IEFA SELL 15 @ 69.64 (generator's seed output) → +1044.60, commission 0.62 → net 1043.98
        assert abs(float(sell["amount_gross"]) - 1044.6) < 0.01, sell.to_dict()
        assert abs(float(sell["amount_net"]) - 1043.98) < 0.01, sell.to_dict()
        assert abs(float(sell["commission_fee"]) - 0.62) < 1e-6

    def test_sell_quantity_absolute(self, ibkr_trades_read_transform):
        """Flex reports sell Quantity as -15; stored quantity is magnitude."""
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        sell = norm[norm["transaction_type"] == "sell"].iloc[0]
        assert float(sell["quantity"]) == 15.0

    def test_no_trade_lands_at_zero(self, ibkr_trades_read_transform):
        """The shipped-bug signature: a trade row with amount_net 0.00 / price NULL."""
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        trades = norm[norm["transaction_type"].isin(["buy", "sell"])]
        assert not trades.empty
        assert not (trades["amount_net"].astype(float) == 0.0).any(), (
            f"Trade row with amount_net=0.00:\n{trades}"
        )
        assert trades["price_unit"].notna().all(), (
            f"Trade row with NULL price_unit:\n{trades}"
        )

    def test_transfer_row_still_zero_cash(self, ibkr_trades_read_transform):
        """ACATS legs must stay 0.00 — they are legitimately cashless."""
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        transfers = norm[norm["transaction_type"].str.startswith("transfer")]
        assert not transfers.empty
        assert (transfers["amount_net"].astype(float) == 0.0).all()

    def test_memo_survives_to_ingest(self, ibkr_trades_read_transform):
        """`description` used to be dropped too — memo was NULL on every row."""
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        assert norm["memo"].notna().all(), norm[["transaction_type", "memo"]]
        assert any("IBKR trade" in str(m) for m in norm["memo"])

    def test_currency_usd(self, ibkr_trades_read_transform):
        """Rule 2: transaction amounts carry their native currency; IBKR base = USD."""
        _, transactions_df, _ = ibkr_trades_read_transform
        norm = self._normalized(transactions_df)
        assert set(norm["currency"]) == {"USD"}
