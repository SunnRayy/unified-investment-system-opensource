"""Unit tests for the F4.2 canonical-underlying signal dedup (PRD 2026-07-07).

Defect reproduced: VOO and S&P500 emit conflicting valuation signals (HIGH
vs FAIR/69%) for the same underlying because each ticker is an independently
collected/scored series. The fix maps instruments to a canonical underlying
(config/canonical_underlyings.yaml) and mirrors the canonical series' signal
onto every mapped instrument in the /valuation/snapshot/latest read path.
"""
from __future__ import annotations

from src.services.valuation.canonical_underlyings import (
    apply_canonical_signal_dedup,
    load_canonical_underlyings,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_load_canonical_underlyings_seed_mapping_present():
    """The committed config seeds VOO/IVV/SPY -> SP500, with SP500 -> 'S&P500'."""
    underlyings, series = load_canonical_underlyings()

    assert underlyings["VOO"] == "SP500"
    assert underlyings["IVV"] == "SP500"
    assert underlyings["SPY"] == "SP500"
    assert series["SP500"] == "S&P500"


def test_load_canonical_underlyings_missing_file_falls_back():
    """A missing/bad config path must degrade to the built-in seed, never raise."""
    underlyings, series = load_canonical_underlyings(config_path="/nonexistent/path.yaml")

    assert underlyings["VOO"] == "SP500"
    assert series["SP500"] == "S&P500"


# ---------------------------------------------------------------------------
# Dedup transform
# ---------------------------------------------------------------------------

def _voo_row(signal: str) -> dict:
    return {
        "ticker": "VOO",
        "row_kind": "holding",
        "valuation_signal": signal,
        "signal_basis": "pe_forward 24.10 >= high 22.00",
        "percentile_value": None,
        "percentile_metric": "pe_forward",
        "pct_years": None,
    }


def _sp500_row(signal: str, percentile: float) -> dict:
    return {
        "ticker": "S&P500",
        "row_kind": "tracked_index",
        "valuation_signal": signal,
        "signal_basis": f"历史10年 {percentile:.0f}th分位，处于合理区间",
        "percentile_value": percentile,
        "percentile_metric": "pe_ttm",
        "pct_years": 10,
    }


def test_voo_mirrors_sp500_signal_when_both_series_exist_with_different_raw_signals():
    """Unit test required by PRD F4.2 acceptance: VOO row returns the SP500
    signal when both series exist with different raw signals (HIGH vs FAIR/69%)."""
    rows = [_voo_row("HIGH"), _sp500_row("FAIR", 69.0)]

    result = apply_canonical_signal_dedup(rows)
    voo = next(r for r in result if r["ticker"] == "VOO")
    sp500 = next(r for r in result if r["ticker"] == "S&P500")

    assert voo["valuation_signal"] == "FAIR", "VOO must display the canonical (SP500) signal"
    assert voo["percentile_value"] == 69.0
    assert voo["canonical_underlying"] == "SP500"
    assert voo["signal_source_series"] == "S&P500"
    # Canonical source row itself is unaffected (still shows its own signal).
    assert sp500["valuation_signal"] == "FAIR"
    assert sp500["canonical_underlying"] is None


def test_dedup_leaves_unmapped_tickers_untouched():
    """A ticker with no canonical mapping (e.g. MSFT) must pass through unchanged."""
    rows = [
        {"ticker": "MSFT", "valuation_signal": "LOW", "signal_basis": "x"},
        _sp500_row("FAIR", 69.0),
    ]

    result = apply_canonical_signal_dedup(rows)
    msft = next(r for r in result if r["ticker"] == "MSFT")

    assert msft["valuation_signal"] == "LOW"
    assert msft["canonical_underlying"] is None
    assert msft["signal_source_series"] is None


def test_dedup_leaves_instrument_signal_when_source_series_absent():
    """If the canonical source series hasn't been collected yet, the mapped
    instrument keeps its own signal rather than being blanked out."""
    rows = [_voo_row("HIGH")]

    result = apply_canonical_signal_dedup(rows)
    voo = result[0]

    assert voo["valuation_signal"] == "HIGH"
    assert voo["canonical_underlying"] == "SP500"
    assert voo["signal_source_series"] is None


def test_invariant_no_two_instruments_share_canonical_with_different_signals():
    """Invariant (PRD F4.2 acceptance): across all instruments in API output, no
    two instruments mapping to the same canonical underlying may carry
    different signal values."""
    rows = [_voo_row("HIGH"), _sp500_row("FAIR", 69.0)]
    rows.append({**_voo_row("LOW"), "ticker": "IVV"})
    rows.append({**_voo_row("N/A"), "ticker": "SPY"})

    result = apply_canonical_signal_dedup(rows)

    by_canonical: dict[str, set[str]] = {}
    for row in result:
        canonical = row.get("canonical_underlying")
        if canonical is None:
            continue
        by_canonical.setdefault(canonical, set()).add(row["valuation_signal"])

    for canonical_id, signals in by_canonical.items():
        assert len(signals) == 1, (
            f"Instruments mapped to canonical underlying {canonical_id!r} carry "
            f"conflicting signals: {signals}"
        )
