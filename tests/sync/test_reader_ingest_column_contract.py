"""Structural guard: reader hooks vs the ingest column contract (WS-B2).

WHY THIS EXISTS
---------------
Every reader produces a transactions DataFrame; ``_normalize_transactions_df`` in
``src/sync/phases/_ingest.py`` consumes it and shapes it into
``TRANSACTIONS_INSERT_COLUMNS``. The two halves agree on field NAMES purely by
convention. Some readers emit the contract names directly; others emit reader-local
aliases (``price`` / ``amount`` / ``fees`` / ``description``) that a per-source
``rename_map`` inside the normalizer translates.

Nothing enforced that a reader's names were in either set. ``Broker_IBKR`` emitted
the aliases but has no ``rename_map`` branch, so pandas silently dropped every money
column: ``amount_gross`` became ``None`` → ``amount_net = round(0.0 - 0.0, 2) = 0.00``,
``price_unit`` NULL, commission and memo lost. Every IBKR transaction in production
was booked at zero cash. This is the "convention contract" failure class in
``uis-failure-classes`` and the producer/consumer half of ``two-sources-signature-bug``:
one name, two independent spellings, no enforcement.

HOW THE GUARD WORKS
-------------------
It does NOT restate the alias table — a copy of the mapping would drift with the
same silence as the bug it guards. Instead it OBSERVES the real ingest code:

  1. run the reader on its fixture to learn which columns it actually emits;
  2. rebuild that row with a unique, type-compatible sentinel in every cell;
  3. push it through the real ``_normalize_transactions_df``;
  4. any sentinel that does not appear anywhere in the normalized row was DROPPED —
     that column never reaches the database, whatever it was named.

Dropped columns must be explicitly declared in ``ALLOWED_DROPS`` with a reason. A new
reader (or a renamed field) that quietly stops reaching the DB turns this red.

HARD CONSTRAINT: no test here may instantiate DatabaseConnector or open
data/unified.duckdb (project DB-safety rule). Everything runs on fixtures in memory.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

ROOT = Path(__file__).parent.parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "readers"
CONFIG_DIR = ROOT / "config" / "readers"

_MOCK_FX = {"USD": 7.0}

# reader_key -> fixture path handed to ConfigDrivenReader.read()
READER_FIXTURES: Dict[str, Path] = {
    "gold": FIXTURE_DIR / "Gold_transactions.xlsx",
    "insurance": FIXTURE_DIR / "Insurance_Portfolio.xlsx",
    "rsu": FIXTURE_DIR / "RSU_transactions.xlsx",
    "cn_fund": FIXTURE_DIR / "funding_transactions.xlsx",
    "ibkr": FIXTURE_DIR / "ibkr",
    "ibkr_trades": FIXTURE_DIR / "ibkr_trades",
}

# reader_key -> yaml basename (ibkr_trades reuses the ibkr config, different fixture)
READER_CONFIGS: Dict[str, str] = {
    "gold": "gold",
    "insurance": "insurance",
    "rsu": "rsu",
    "cn_fund": "cn_fund",
    "ibkr": "ibkr",
    "ibkr_trades": "ibkr",
}

# Columns a reader may emit that legitimately never reach the transactions table.
# Anything NOT listed here is a silent data-loss bug. Add entries only with a reason.
ALLOWED_DROPS: Dict[str, Dict[str, str]] = {
    "Gold_Excel": {},
    "Insurance_Excel": {},
    "CN_Fund_Excel": {},
    "Broker_IBKR": {},
    "Schwab_CSV": {},
    "RSU_Excel": {
        # _normalize_transactions_df overwrites currency unconditionally with
        # _default_currency(source_system) ("USD" for RSU_Excel), so a reader-supplied
        # currency is discarded by design. Same resulting value; not data loss.
        "currency": "overwritten by _default_currency(source_system) — same value",
    },
}

# Every reader must land at least these in the DB row, or the fixture is not
# exercising the money path and the guard above would be vacuous.
_ALWAYS_REQUIRED = ("transaction_date", "asset_id", "transaction_type")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reader_transactions(reader_key: str) -> "tuple[str, pd.DataFrame]":
    """(source_system, transactions_df) from the real reader on its real fixture."""
    from src.sources.config_driven_reader import ConfigDrivenReader
    from src.sources.reader_config import load_reader_config

    cfg = load_reader_config(CONFIG_DIR / f"{READER_CONFIGS[reader_key]}.yaml")
    reader = ConfigDrivenReader(cfg)
    with patch(
        "src.market_data.fetchers.yfinance_fetcher.fetch_fx_rates",
        return_value=_MOCK_FX,
    ):
        data = reader.read(READER_FIXTURES[reader_key])
        _holdings, transactions = reader.transform(data)
    return cfg.identity.source_system, transactions


def _schwab_transactions() -> "tuple[str, pd.DataFrame]":
    """Schwab needs its two-CSV sync entry point rather than a single file path."""
    from src.sync.schwab_sync import sync_schwab

    config = {
        "source_registry": {
            "schwab": {
                "enabled": True,
                "data_dir": str(FIXTURE_DIR),
                "file_patterns": {
                    "positions": "Individual-Positions-*.csv",
                    "transactions": "Individual_*_Transactions_*.csv",
                },
            }
        }
    }
    return "Schwab_CSV", sync_schwab(config)["transactions"]


def _is_date_like(value: Any) -> bool:
    """True for real date objects AND for date-bearing strings.

    Readers are inconsistent: IBKR/Schwab emit ``transaction_date`` as a
    ``YYYY-MM-DD`` string, Insurance emits a real ``datetime``. Both must get a
    date sentinel or ``_to_date`` returns None and the normalizer drops the whole
    probe row.
    """
    from src.sync.phases._common import _to_date

    if isinstance(value, (_dt.date, _dt.datetime, pd.Timestamp)):
        return True
    return isinstance(value, str) and _to_date(value) is not None


def _is_number_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    # np.int64 does not subclass Python int (unlike np.float64, which does
    # subclass float) — an all-whole-number Excel column reads back as int64,
    # which used to fall through to the string-sentinel branch below and
    # produce a false "column dropped" failure having nothing to do with the
    # ingest contract. Both numpy scalar families are number-like.
    return isinstance(value, (int, float, np.integer, np.floating)) and not pd.isna(value)


def _representative(series: pd.Series) -> Any:
    """First non-null value in the column, else None."""
    non_null = series.dropna()
    return non_null.iloc[0] if len(non_null) else None


def _build_sentinel_row(tx_df: pd.DataFrame, source_system: str) -> "tuple[pd.DataFrame, Dict[str, Any]]":
    """One-row frame with a unique, type-compatible sentinel in every column.

    Returns (frame, {column: sentinel}). ``source_system`` keeps its real value —
    it is the dispatch key, not payload.
    """
    sentinels: Dict[str, Any] = {}
    numeric_seed = 90001.0
    date_seed = _dt.date(2011, 3, 5)
    date_offset = 0

    for col in tx_df.columns:
        if col == "source_system":
            sentinels[col] = source_system
            continue
        sample = _representative(tx_df[col])
        if _is_date_like(sample):
            sentinels[col] = date_seed + _dt.timedelta(days=date_offset)
            date_offset += 1
        elif _is_number_like(sample):
            sentinels[col] = numeric_seed
            numeric_seed += 1.0
        else:
            # str, None-only, or anything else → a traceable string. Strings are
            # accepted everywhere text is read and are never silently coerced.
            sentinels[col] = f"SENTINEL_{col}"

    return pd.DataFrame([sentinels]), sentinels


def _normalized_values(norm_row: pd.Series) -> List[Any]:
    out: List[Any] = []
    for value in norm_row.tolist():
        if isinstance(value, pd.Timestamp):
            out.append(value.date())
        else:
            out.append(value)
    return out


def _dropped_columns(tx_df: pd.DataFrame, source_system: str) -> Dict[str, Any]:
    """{column: sentinel} for every emitted column that does NOT reach the DB row."""
    from src.sync.phases._ingest import _normalize_transactions_df

    probe_df, sentinels = _build_sentinel_row(tx_df, source_system)
    norm = _normalize_transactions_df(probe_df, source_system)
    assert len(norm) == 1, (
        f"{source_system}: sentinel probe row was dropped entirely by the normalizer "
        f"(got {len(norm)} rows) — the probe, not the reader, is broken."
    )
    landed = _normalized_values(norm.iloc[0])

    dropped: Dict[str, Any] = {}
    for col, sentinel in sentinels.items():
        if col == "source_system":
            continue
        found = False
        for value in landed:
            if isinstance(sentinel, float) and isinstance(value, float):
                found = abs(value - sentinel) < 1e-6
            else:
                found = value == sentinel
            if found:
                break
        if not found:
            dropped[col] = sentinel
    return dropped


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reader_outputs() -> Dict[str, "tuple[str, pd.DataFrame]"]:
    outputs: Dict[str, "tuple[str, pd.DataFrame]"] = {}
    for key in READER_FIXTURES:
        outputs[key] = _reader_transactions(key)
    outputs["schwab"] = _schwab_transactions()
    return outputs


ALL_READER_KEYS = sorted(list(READER_FIXTURES) + ["schwab"])


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

class TestReaderIngestColumnContract:
    @pytest.mark.parametrize("reader_key", ALL_READER_KEYS)
    def test_no_emitted_column_is_silently_dropped(self, reader_key, reader_outputs):
        source_system, tx_df = reader_outputs[reader_key]
        assert not tx_df.empty, f"{reader_key}: fixture produced no transactions"

        dropped = _dropped_columns(tx_df, source_system)
        allowed = ALLOWED_DROPS.get(source_system, {})
        unexpected = {c: v for c, v in dropped.items() if c not in allowed}

        assert not unexpected, (
            f"{reader_key} ({source_system}) emits column(s) "
            f"{sorted(unexpected)} that never reach the transactions table.\n"
            f"_normalize_transactions_df reads "
            f"price_unit/amount_gross/commission_fee/memo and applies a per-source "
            f"rename_map; a name in neither set is dropped by pandas with no error "
            f"(this is exactly how Broker_IBKR shipped every trade at "
            f"amount_net=0.00).\n"
            f"Fix the reader to emit the contract name, or — if the column is "
            f"deliberately not persisted — declare it in ALLOWED_DROPS with a reason."
        )

    @pytest.mark.parametrize("reader_key", ALL_READER_KEYS)
    def test_probe_is_not_vacuous(self, reader_key, reader_outputs):
        """Anti-vacuity: the probe must actually observe columns surviving.

        Without this a broken probe (e.g. sentinels matching everything) would make
        the guard above pass unconditionally.
        """
        source_system, tx_df = reader_outputs[reader_key]
        _probe_df, sentinels = _build_sentinel_row(tx_df, source_system)
        dropped = _dropped_columns(tx_df, source_system)
        survived = set(sentinels) - set(dropped) - {"source_system"}
        assert len(survived) >= 4, (
            f"{reader_key}: only {sorted(survived)} survived normalization — "
            "the probe is not exercising the contract."
        )
        for required in _ALWAYS_REQUIRED:
            assert required in survived or required not in sentinels, (
                f"{reader_key}: {required} did not survive normalization"
            )

    @pytest.mark.parametrize("reader_key", ALL_READER_KEYS)
    def test_money_fields_reach_the_db_row(self, reader_key, reader_outputs):
        """Real (non-sentinel) run: a reader that reports cash must land cash.

        Complements the sentinel probe with a value-level check — a reader could
        pass the name contract and still land NULL if the hook computes nothing.
        """
        from src.sync.phases._ingest import _normalize_transactions_df

        source_system, tx_df = reader_outputs[reader_key]
        norm = _normalize_transactions_df(tx_df, source_system)
        assert not norm.empty

        # Rows that represent a cash-moving event. Share transfers (ACATS in/out)
        # legitimately carry no cash and are excluded.
        cash_rows = norm[norm["transaction_type"].isin(
            ["buy", "sell", "premium_payment", "dividend"]
        )]
        if cash_rows.empty:
            pytest.skip(f"{reader_key} fixture has no cash-moving rows")

        zero_or_null = cash_rows[
            cash_rows["amount_net"].isna()
            | (cash_rows["amount_net"].astype(float) == 0.0)
        ]
        assert zero_or_null.empty, (
            f"{reader_key} ({source_system}): cash-moving rows landed at "
            f"amount_net 0.00/NULL — the amount column is not reaching ingest:\n"
            f"{zero_or_null[['transaction_date', 'asset_id', 'transaction_type', 'amount_gross', 'amount_net']]}"
        )


class TestGuardActuallyDetectsTheBug:
    """Proof the guard is not decorative: it must go red on the pre-fix shape.

    Rather than trusting that reverting the hook would fail, reconstruct the exact
    pre-fix output (``price`` / ``amount`` / ``fees`` / ``description`` under
    ``Broker_IBKR``) and assert the detector reports every money column as dropped.
    """

    @staticmethod
    def _pre_fix_frame() -> pd.DataFrame:
        # Verbatim shape of ibkr_transactions_from_flex before the WS-B fix.
        return pd.DataFrame([{
            "asset_id": "US_STK_SGOV",
            "transaction_date": "2026-07-17",
            "transaction_type": "buy",
            "quantity": 64.0,
            "price": 100.62,
            "amount": -6439.68,
            "fees": 0.35,
            "description": "IBKR trade SGOV",
            "source_system": "Broker_IBKR",
        }])

    def test_detector_flags_every_pre_fix_money_column(self):
        dropped = _dropped_columns(self._pre_fix_frame(), "Broker_IBKR")
        assert {"price", "amount", "fees", "description"} <= set(dropped), (
            f"Guard failed to detect the known bug; dropped={sorted(dropped)}"
        )

    def test_contract_assertion_would_fail_on_pre_fix_output(self):
        dropped = _dropped_columns(self._pre_fix_frame(), "Broker_IBKR")
        unexpected = {c for c in dropped if c not in ALLOWED_DROPS["Broker_IBKR"]}
        assert unexpected, "ALLOWED_DROPS must not whitelist the IBKR money columns"

    def test_pre_fix_output_produces_the_zero_signature(self):
        """The observable production symptom, reproduced from the pre-fix shape."""
        from src.sync.phases._ingest import _normalize_transactions_df

        norm = _normalize_transactions_df(self._pre_fix_frame(), "Broker_IBKR")
        row = norm.iloc[0]
        assert float(row["amount_net"]) == 0.0
        assert row["amount_gross"] is None or pd.isna(row["amount_gross"])
        assert row["price_unit"] is None or pd.isna(row["price_unit"])
        assert row["memo"] is None or pd.isna(row["memo"])

    def test_post_fix_output_is_clean(self):
        """Same row through the fixed hook shape → nothing dropped, cash correct."""
        from src.sync.phases._ingest import _normalize_transactions_df

        fixed = pd.DataFrame([{
            "asset_id": "US_STK_SGOV",
            "transaction_date": "2026-07-17",
            "transaction_type": "buy",
            "quantity": 64.0,
            "price_unit": 100.62,
            "amount_gross": -6439.68,
            "commission_fee": 0.35,
            "memo": "IBKR trade SGOV",
            "source_system": "Broker_IBKR",
        }])
        assert _dropped_columns(fixed, "Broker_IBKR") == {}
        row = _normalize_transactions_df(fixed, "Broker_IBKR").iloc[0]
        assert abs(float(row["amount_net"]) - (-6440.03)) < 0.01
        assert abs(float(row["price_unit"]) - 100.62) < 1e-6
