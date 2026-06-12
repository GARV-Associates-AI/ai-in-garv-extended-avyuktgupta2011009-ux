# rules.py
# ============================================================
# ALL TAX RULES IN ONE PLACE
# If budget changes rates, update ONLY this file
# ============================================================

from datetime import date

TAX_RULES = {

    # --- Dates ---
    "effective_from"    : date(2026, 4, 1),    # New rules apply from
    "grandfather_date"  : date(2018, 1, 31),   # Section 112A cutoff

    # --- Holding Period ---
    # 365 days or more = Long Term
    # Less than 365 days = Short Term
    "ltcg_holding_days" : 365,

    # --- Tax Rates ---
    "ltcg_rate"         : 0.125,   # 12.5% on Long Term Gains
    "stcg_rate"         : 0.20,    # 20.0% on Short Term Gains

    # --- Exemptions ---
    # First 1,25,000 of LTCG is tax free (Section 112A)
    "ltcg_exemption"    : 125000,

    # --- Cess ---
    "cess_rate"         : 0.04,    # 4% Health & Education Cess

    # --- Surcharge ---
    # Maximum surcharge on equity gains is capped at 15%
    "max_surcharge_equity" : 0.15,

    # --- Loss Rules ---
    # Losses can be carried forward for 8 Assessment Years
    "loss_carryforward_years" : 8,

    # --- Buyback Treatment ---
    # After 1 Apr 2026 — buyback is capital gain, NOT deemed dividend
    "buyback_mode" : "CAPITAL_GAINS",

    # --- STT ---
    # STT is NOT deductible from cost or sale price
    "stt_deductible" : False,
}


def get_rule(rule_name):
    """
    Use this function anywhere in the app to fetch a rule.
    Example: get_rule("ltcg_rate") returns 0.125
    """
    value = TAX_RULES.get(rule_name, None)
    if value is None:
        raise KeyError(f"Rule '{rule_name}' not found in TAX_RULES")
    return value


def get_all_rules():
    """Returns all rules — used for displaying rules on screen"""
    return TAX_RULES.copy()