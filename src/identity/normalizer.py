"""Asset ID normalization logic."""

import re


def normalize_asset_id(source_id: str, source_system: str) -> str:
    """
    Normalize a source asset ID to standard format.

    Args:
        source_id: Original ID from source system
        source_system: Source system name (PIS, DSA)

    Returns:
        Normalized ID (still without canonical prefix)
    """
    # Remove whitespace
    source_id = source_id.strip().replace(" ", "")

    # Check for special asset types first (preserve as-is)
    if source_id.startswith(('Ins_', 'Insurance_', 'Property_', 'Pension_')):
        return source_id

    # Numeric codes - pad to 6 digits
    if re.match(r'^\d+$', source_id):
        return source_id.zfill(6)

    # Stock codes with suffix - preserve them!
    # Previously we stripped them, but that loses critical info for 00xxxx ambiguity.

    return source_id


def get_canonical_id(source_id: str, source_system: str) -> str:
    """
    Convert a source asset ID to canonical ID format.

    Args:
        source_id: Original ID from source system
        source_system: Source system name

    Returns:
        Canonical ID (e.g., CN_FUND_110020, CN_STK_600519.SH)
    """
    # Special asset types — convert to canonical prefix form
    if source_id.startswith('Ins_'):
        return 'INS_' + source_id[4:]
    if source_id.startswith('Insurance_'):
        return 'INS_' + source_id[10:]
    if source_id.startswith('INS_'):
        return source_id
    if source_id.startswith('Property_'):
        return source_id
    if source_id.startswith('Pension_'):
        return source_id

    # Normalize first
    normalized = normalize_asset_id(source_id, source_system)

    # Handle Cash/Deposit/RSU/Gold patterns
    if 'Cash' in source_id or 'Deposit' in source_id:
        return f"CASH_{normalized}"
    if source_id.startswith('RSU_'):
        return source_id  # Already canonical (RSU_AMZN → RSU_AMZN, not RSU_RSU_AMZN)
    if 'RSU' in source_id:
        return f"RSU_{normalized}"
    if 'Gold' in source_id or '黄金' in source_id:
        return f"ALTS_{normalized}"

    # Analyze 6-digit codes (with or without suffix)
    # Strip suffix for analysis
    code_body = normalized
    suffix = ""
    if re.match(r'^\d{6}\.(SH|SZ)$', normalized):
        code_body = normalized[:6]
        suffix = normalized[6:] # .SH or .SZ

    if re.match(r'^\d{6}$', code_body):
        # Heuristics for CN Stocks vs Funds
        # Funds:
        # 15xxxx, 16xxxx (SZ Funds/LOF)
        # 50xxxx, 51xxxx, 52xxxx, 56xxxx, 58xxxx (SH ETFs/Funds)
        # 00xxxx (Open ended funds - but conflicts with SZ Stocks 00xxxx)
        # 01xxxx (Funds)
        # 11xxxx, 12xxxx (Bonds)
        # 66xxxx (SH Funds?)
        
        # Explicit Fund Patterns
        if code_body.startswith(('15', '16', '50', '51', '52', '56', '58', '01', '02', '66')):
             return f"CN_FUND_{code_body}"
             
        # Stocks Patterns
        # SH: 60xxxx, 68xxxx
        if code_body.startswith(('60', '68')):
            return f"CN_STK_{code_body}.SH"
            
        # SZ: 00xxxx, 30xxxx
        if code_body.startswith(('00', '30')):
            # Ambiguity: 00xxxx can be Stock or Fund.
            
            # If 00xxxx and NO suffix -> Treat as Open Ended Fund
            if not suffix and code_body.startswith('00'):
                 return f"CN_FUND_{code_body}"
            
            # Otherwise (Has Suffix .SZ OR starts with 30) -> Stock
            return f"CN_STK_{code_body}.SZ"

        # Bonds (11, 12, etc) - Map to FUND for now or BOND? 
        # Requirement was STK vs FUND.
        # 11xxxx codes are ambiguous. 11xxxx can be convertible bonds or funds.
        # User said "all ... are funds". Defaulting to FUND is safer for now.
        # if code_body.startswith(('10', '11', '12')):
        #    return f"CN_BOND_{code_body}"
            
        # Fallback for other numeric
        return f"CN_FUND_{code_body}"

    # US stock pattern (letters only, 1-5 chars)
    if re.match(r'^[A-Z]{1,5}$', source_id.upper()):
        return f"US_STK_{source_id.upper()}"

    # Fallback
    return f"UNKNOWN_{normalized}"
