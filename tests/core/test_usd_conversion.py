import pytest
pytestmark = pytest.mark.critical

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test USD to CNY currency conversion
"""

import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def test_usd_to_cny_conversion():
    """Test USD to CNY conversion uses correct rate"""
    
    # Import the currency service
    try:
        from src.data_manager.currency_converter import get_currency_service
        service = get_currency_service()
    except ImportError:
        print("⊘ Could not find currency service")
        return False

    # Test conversion
    amount_usd = 100.00
    # Use hardcoded date or now
    rate_date = pd.Timestamp.now()
    
    # Force use of fallback by ensuring google finance fails or isn't used?
    # The service tries Google Finance first.
    # We want to verify it handles USD.
    
    amount_cny = service.convert_amount(amount_usd, "USD", "CNY", rate_date)
    
    if amount_cny is None:
        print("✗ Conversion returned None")
        return False

    print(f"USD {amount_usd} -> CNY {amount_cny}")

    # Check range. 
    # If 7.0 rate: 700. If 7.25 rate: 725.
    # Allow a range since live rate might be used if Google Finance works.
    assert amount_cny > 600, f"Expected > 600 CNY, got {amount_cny}"
    assert amount_cny < 800, f"Expected < 800 CNY, got {amount_cny}"

    print(f"✓ USD converted to CNY successfully: {amount_cny}")
    return True

def test_currency_config_override():
    """Test that currency service accepts config override"""
    from src.data_manager.currency_converter import CurrencyConverterService
    import pandas as pd
    
    # Custom config with extreme rate to verify it's used
    config = {
        'currency': {
            'fallback_rates': {
                'USD_CNY': 500.0
            }
        }
    }
    
    # Initialize service with config (this init signature doesn't exist yet -> expected failure/exception or ignored)
    try:
        service = CurrencyConverterService(enable_google_finance=False) 
        # Attempt to inject config if supported, or pass to constructor if we change it.
        # Plan says: Update CurrencyConverterService.__init__ to accept config.
        # So I should test calling it with config.
        # If I call it now with config, it will raise TypeError (unexpected arg) -> RED state.
        service = CurrencyConverterService(enable_google_finance=False, config=config)
    except TypeError:
        print("✗ CurrencyConverterService does not accept config argument")
        return False
        
    amount = 1.0
    rate_date = pd.Timestamp.now()
    
    # Convert
    result = service.convert_amount(amount, "USD", "CNY", rate_date)
    
    # Verify
    if result == 500.0:
        print("✓ Config override working (USD -> 500 CNY)")
        return True
    else:
        print(f"✗ Config override failed. Got {result}")
        return False

def run_all_tests():
    """Run all tests"""
    print("Testing USD currency conversion...\n")

    try:
        success = True
        if not test_usd_to_cny_conversion():
            success = False
            
        print("\nTesting Config Override:")
        if not test_currency_config_override():
            success = False
            
        if success:
            print("\n✓ All currency tests passed!")
            return True
        else:
            print("\n✗ Some currency tests failed")
            return False
            
    except Exception as e:
        print(f"\n⚠ Currency test exception: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
