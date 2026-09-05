import pytest
from src.services.valuation.rate_adjust import adjusted_factor


@pytest.mark.parametrize("us10y,expected", [
    (2.0, 1.0),
    (3.5, 0.94),
    (4.5, 0.88),
    (5.0, 0.85),
    (6.7, 0.75),
    (8.0, 0.75),
])
def test_adjusted_factor(us10y, expected):
    assert abs(adjusted_factor(us10y) - expected) < 0.005


def test_rejects_decimal_input():
    with pytest.raises(ValueError):
        adjusted_factor(0.045)


def test_rejects_zero():
    with pytest.raises(ValueError):
        adjusted_factor(0.0)
