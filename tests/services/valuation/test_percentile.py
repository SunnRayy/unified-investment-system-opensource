from src.services.valuation.percentile import compute_percentile


def test_basic_mid_rank():
    series = [10, 20, 30, 40, 50]
    pct, years = compute_percentile(series, 30, date_range_days=3650)
    # count_below=2, count_equal=1, total=5 → (2 + 0.5) / 5 * 100 = 50
    assert abs(pct - 50.0) < 0.1
    assert years == 10


def test_insufficient_data():
    pct, years = compute_percentile([10, 20, 30], 20)
    assert pct is None
    assert years == 0


def test_nan_and_none_filtered():
    series = [10, None, 20, float("nan"), 30, 40, 50]
    pct, _ = compute_percentile(series, 30, date_range_days=3650)
    assert pct is not None


def test_at_minimum():
    series = [10, 20, 30, 40, 50]
    pct, _ = compute_percentile(series, 10, date_range_days=1000)
    # count_below=0, count_equal=1 → 0.5/5*100 = 10
    assert abs(pct - 10.0) < 0.1


def test_at_maximum():
    series = [10, 20, 30, 40, 50]
    pct, _ = compute_percentile(series, 50, date_range_days=1000)
    # count_below=4, count_equal=1 → 4.5/5*100 = 90
    assert abs(pct - 90.0) < 0.1
