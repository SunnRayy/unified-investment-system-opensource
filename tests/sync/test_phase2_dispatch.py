"""Hermetic unit tests for the Phase-2 registry-driven reader dispatch (ADR-018).

No real DB, no sync, no file I/O. Verifies:
1. _PHASE2_READER_ORDER keys == _PHASE2_READER_FUNCS keys (same set, no dupes in order list).
2. Dispatch order + generic fallback: the 7 known keys run their specialized stubs;
   a synthetic "custom_demo" key (category=="reader") falls through to _run_config_reader.
"""

from unittest.mock import MagicMock, patch
import pytest

pytestmark = pytest.mark.pipeline

import src.sync.orchestrator as orch
from src.sync.orchestrator import (
    _PHASE2_READER_ORDER,
    _PHASE2_READER_FUNCS,
    _dispatch_phase2_readers,
    SyncResult,
)


# ---------------------------------------------------------------------------
# Test 1 — structural invariants
# ---------------------------------------------------------------------------

class TestDispatchTableInvariants:
    def test_order_and_funcs_have_same_keys(self):
        """_PHASE2_READER_ORDER and _PHASE2_READER_FUNCS must cover exactly the same keys."""
        assert set(_PHASE2_READER_ORDER) == set(_PHASE2_READER_FUNCS.keys()), (
            "Keys mismatch between _PHASE2_READER_ORDER and _PHASE2_READER_FUNCS"
        )

    def test_order_list_has_no_duplicates(self):
        """_PHASE2_READER_ORDER must not contain duplicate keys."""
        assert len(_PHASE2_READER_ORDER) == len(set(_PHASE2_READER_ORDER)), (
            f"Duplicate key(s) in _PHASE2_READER_ORDER: {_PHASE2_READER_ORDER}"
        )

    def test_known_keys_present(self):
        """All 7 historical reader keys must be present."""
        expected = {"schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary", "ibkr"}
        assert expected == set(_PHASE2_READER_ORDER)

    def test_historical_order_preserved(self):
        """The first 7 entries must follow the historical ingest order."""
        expected_order = [
            "schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary", "ibkr"
        ]
        assert _PHASE2_READER_ORDER == expected_order, (
            f"Order changed. Expected {expected_order}, got {_PHASE2_READER_ORDER}"
        )


# ---------------------------------------------------------------------------
# Fake registry helpers
# ---------------------------------------------------------------------------

def _make_fake_config_obj(category: str):
    """Return a minimal object that looks like a ReaderConfig."""
    obj = MagicMock()
    obj.identity.category = category
    return obj


def _make_fake_registry(extra_keys: list):
    """Return a fake SourceRegistry with the 7 built-ins PLUS any extra keys."""
    known_keys = list(_PHASE2_READER_ORDER)
    all_keys = known_keys + extra_keys

    def _reader_keys():
        return all_keys

    def _config_for_key(k):
        if k in extra_keys:
            return _make_fake_config_obj("reader")
        return _make_fake_config_obj("reader")  # built-ins also have category="reader"

    def _key_to_system():
        mapping = {
            "schwab": "Schwab_CSV",
            "cn_fund": "CN_Fund_Excel",
            "gold": "Gold_Excel",
            "insurance": "Insurance_Excel",
            "rsu": "RSU_Excel",
            "financial_summary": "Financial_Summary_Excel",
            "ibkr": "Broker_IBKR",
        }
        for k in extra_keys:
            mapping[k] = f"Custom_{k}"
        return mapping

    reg = MagicMock()
    reg.reader_keys.side_effect = _reader_keys
    reg._config_for_key.side_effect = _config_for_key
    reg.key_to_system.side_effect = _key_to_system
    return reg


# ---------------------------------------------------------------------------
# Test 2 — dispatch order and generic fallback
# ---------------------------------------------------------------------------

class TestDispatchOrder:
    def _make_result(self):
        r = SyncResult(success=True)
        return r

    def test_seven_known_readers_called_in_order_no_extras(self):
        """With only the 7 built-in keys, the 7 specialized stubs run in order."""
        call_log = []

        def make_stub(key):
            def stub(connector, config, result):
                call_log.append(key)
                return 0, 0
            return stub

        fake_reg = _make_fake_registry(extra_keys=[])

        # Build patches: replace each _run_<key>_reader in orchestrator namespace
        patches = {}
        for key in _PHASE2_READER_ORDER:
            fn_name = f"_run_{key}_reader"
            patches[fn_name] = make_stub(key)

        with (
            patch.object(orch, "_run_schwab_reader", patches["_run_schwab_reader"]),
            patch.object(orch, "_run_cn_fund_reader", patches["_run_cn_fund_reader"]),
            patch.object(orch, "_run_gold_reader", patches["_run_gold_reader"]),
            patch.object(orch, "_run_insurance_reader", patches["_run_insurance_reader"]),
            patch.object(orch, "_run_rsu_reader", patches["_run_rsu_reader"]),
            patch.object(orch, "_run_financial_summary_reader", patches["_run_financial_summary_reader"]),
            patch.object(orch, "_run_ibkr_reader", patches["_run_ibkr_reader"]),
            patch("src.sources.registry.get_registry", return_value=fake_reg),
        ):
            # The dispatch table was built at module-load time referencing the
            # original function objects, so we patch _PHASE2_READER_FUNCS too.
            patched_funcs = {
                "schwab": patches["_run_schwab_reader"],
                "cn_fund": patches["_run_cn_fund_reader"],
                "gold": patches["_run_gold_reader"],
                "insurance": patches["_run_insurance_reader"],
                "rsu": patches["_run_rsu_reader"],
                "financial_summary": patches["_run_financial_summary_reader"],
                "ibkr": patches["_run_ibkr_reader"],
            }
            with patch.object(orch, "_PHASE2_READER_FUNCS", patched_funcs):
                connector = MagicMock()
                config = {"source_registry": {}}
                result = self._make_result()
                _dispatch_phase2_readers(connector, config, result)

        assert call_log == list(_PHASE2_READER_ORDER), (
            f"Call order wrong. Expected {list(_PHASE2_READER_ORDER)}, got {call_log}"
        )

    def test_generic_fallback_for_extra_reader(self):
        """A synthetic 'custom_demo' key (category==reader) goes through _run_config_reader."""
        call_log = []

        def make_stub(key):
            def stub(connector, config, result):
                call_log.append(key)
                return 0, 0
            return stub

        config_reader_calls = []

        def fake_config_reader(connector, config, result, reader_key):
            config_reader_calls.append(reader_key)
            call_log.append(reader_key)
            return 0, 0

        fake_reg = _make_fake_registry(extra_keys=["custom_demo"])

        patched_funcs = {
            "schwab": make_stub("schwab"),
            "cn_fund": make_stub("cn_fund"),
            "gold": make_stub("gold"),
            "insurance": make_stub("insurance"),
            "rsu": make_stub("rsu"),
            "financial_summary": make_stub("financial_summary"),
            "ibkr": make_stub("ibkr"),
        }

        with (
            patch.object(orch, "_PHASE2_READER_FUNCS", patched_funcs),
            patch.object(orch, "_run_config_reader", fake_config_reader),
            patch("src.sources.registry.get_registry", return_value=fake_reg),
        ):
            connector = MagicMock()
            config = {"source_registry": {}}
            result = self._make_result()
            _dispatch_phase2_readers(connector, config, result)

        expected_order = [
            "schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary", "ibkr",
            "custom_demo",
        ]
        assert call_log == expected_order, (
            f"Expected call order {expected_order}, got {call_log}"
        )
        assert config_reader_calls == ["custom_demo"], (
            f"'custom_demo' should have gone to _run_config_reader, got {config_reader_calls}"
        )

    def test_extra_reader_not_called_if_category_is_historical(self):
        """A key with category=='historical' must NOT be auto-dispatched as a reader."""
        call_log = []

        def make_stub(key):
            def stub(connector, config, result):
                call_log.append(key)
                return 0, 0
            return stub

        config_reader_calls = []

        def fake_config_reader(connector, config, result, reader_key):
            config_reader_calls.append(reader_key)
            call_log.append(reader_key)
            return 0, 0

        # fake registry: has "hist_source" with category="historical"
        known_keys = list(_PHASE2_READER_ORDER) + ["hist_source"]

        def _reader_keys():
            return known_keys

        def _config_for_key(k):
            obj = MagicMock()
            obj.identity.category = "historical" if k == "hist_source" else "reader"
            return obj

        fake_reg = MagicMock()
        fake_reg.reader_keys.side_effect = _reader_keys
        fake_reg._config_for_key.side_effect = _config_for_key
        fake_reg.key_to_system.return_value = {}

        patched_funcs = {k: make_stub(k) for k in _PHASE2_READER_ORDER}

        with (
            patch.object(orch, "_PHASE2_READER_FUNCS", patched_funcs),
            patch.object(orch, "_run_config_reader", fake_config_reader),
            patch("src.sources.registry.get_registry", return_value=fake_reg),
        ):
            connector = MagicMock()
            config = {"source_registry": {}}
            result = self._make_result()
            _dispatch_phase2_readers(connector, config, result)

        assert "hist_source" not in call_log, (
            f"'hist_source' (category=historical) should not be dispatched, got {call_log}"
        )
        assert config_reader_calls == [], (
            f"_run_config_reader should not have been called for historical source, got {config_reader_calls}"
        )
