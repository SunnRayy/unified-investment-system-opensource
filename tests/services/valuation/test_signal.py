from src.services.valuation.signal import classify_signal, ValuationReference


def ref(low=19.0, high=30.0, mean=29.0, rate_sensitive=True):
    return ValuationReference("MSFT", "pe_forward", low, high, mean, rate_sensitive)


def test_low():
    s, b = classify_signal("pe_forward", 15.0, ref())
    assert s == "LOW"


def test_fair():
    s, b = classify_signal("pe_forward", 25.0, ref(), adj_factor=0.88)
    # high_eff = 30 * 0.88 = 26.4; 25 < 26.4 and > 19 → FAIR
    assert s == "FAIR"


def test_high_with_adjustment():
    s, b = classify_signal("pe_forward", 27.0, ref(), adj_factor=0.88)
    # high_eff = 30 * 0.88 = 26.4; 27 >= 26.4 → HIGH
    assert s == "HIGH"
    assert "adj×" in b


def test_none_value():
    s, b = classify_signal("pe_forward", None, ref())
    assert s == "N/A"
    assert b == "no_data"


def test_nan_value():
    s, b = classify_signal("pe_forward", float("nan"), ref())
    assert s == "N/A"


def test_boundary_inversion():
    r = ref(low=20.0, high=10.0)
    s, b = classify_signal("pe_forward", 15.0, r)
    assert s == "N/A"
    assert "invalid_reference_config" in b


def test_non_rate_sensitive_no_adjustment():
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, adj_factor=0.88)
    # adj_factor not applied (rate_sensitive=False); 14.0 in [11, 16] → FAIR
    assert s == "FAIR"


# ── Percentile-based signal ───────────────────────────────────────────────────

def test_percentile_high_overrides_absolute():
    # PE=14 would be FAIR by absolute thresholds (11-16), but 80th pct → HIGH
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=80.0)
    assert s == "HIGH"
    assert "80th" in b


def test_percentile_low_overrides_absolute():
    # PE=14 would be FAIR by absolute thresholds, but 20th pct → LOW
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=20.0)
    assert s == "LOW"
    assert "20th" in b


def test_percentile_mid_range_stays_fair():
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=50.0)
    assert s == "FAIR"


def test_percentile_at_boundary_high():
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=75.0)
    assert s == "HIGH"


def test_percentile_at_boundary_low():
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=25.0)
    assert s == "LOW"


def test_none_percentile_falls_back_to_absolute():
    r = ValuationReference("900001", "pe_ttm", 11.0, 16.0, 12.5, False)
    s, b = classify_signal("pe_ttm", 14.0, r, percentile=None)
    assert s == "FAIR"
    assert "pe_ttm" in b and "14.00" in b
