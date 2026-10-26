"""Current Circular 1835 public layouts beyond the original I/A/F/C pilot.

Field order transcribed from the consolidated CMF specification, B.4–B.14.
Names ending in mclp mean thousands of CLP; UF and original-currency amounts
remain separate. Padding is explicit and must be blank. X/P have multiple
detail types. R (loans) and V (rental arrears) are not in the public archives.
"""
from .layouts import layout


PROPERTY_FLAGS = """
    property_shop:1 property_office:1 property_warehouse:1 property_parking:1
    property_land:1 property_house:1 property_building:1 property_full_floor:1
    property_partial_floor:1 property_apartment:1 property_construction:1 property_other:1
"""

B = layout("""
    record:1 property_roll:11
""" + PROPERTY_FLAGS + """
    development_progress:2 management_type:1 property_location_type:2 property_purpose:2
    property_use:1 own_use_percent:5:2 instrument_type:10 identifier:30 tenant_name:60
    related:2 remaining_lease_months:3:0 monthly_rent_uf:6:2 address:40 municipality:3 city:30
    purchase_date:8 related_purchase:1 adjusted_cost_mclp:11:0 accumulated_depreciation_mclp:11:0
    remaining_useful_life_months:4:0 depreciated_cost_mclp:11:0 appraisal_1_mclp:11:0
    appraisal_2_mclp:11:0 appraiser_1:40 appraiser_2:40 appraisal_1_date:8 appraisal_2_date:8
    land_area_m2:7:0 built_area_m2:7:0 land_appraisal_uf_m2:6:2 building_appraisal_uf_m2:6:2
    impairment_mclp:11:0 final_value_mclp:12:0 ownership_percent:4:2 prohibition:1
    reserve_backing:5 insurance_plan:30 portfolio_name:30 tsa:2
""")

X2 = layout("""
    record:1 operation_folio:10 item:3 instrument_type:10 nominal:15:4 country:2 parent_country:2
    guarantor_country:2 issuer_name:40 identifier:30 currency:6 issue_date:8 purchase_date:8
    maturity_date:8 months_to_maturity:3:0 duration:4:2 amortization_type:2 nominal_type:1
    discount_treatment:2 issue_rate:8:4 variable_rate_reference:20 base_rate:12
    purchase_value_clp:13:0 purchase_yield:8:4 amortized_cost_mclp:13:0 market_yield:8:4
    fair_value_mclp:13:0 impairment_mclp:14:0 final_value_mclp:12:0 prohibition:1 rating:15
    increased_credit_risk:1 margin_type:10 matching_asset:3 reserve_backing:5
    insurance_plan:30 portfolio_name:30 related:2 investment_classification:1 tsa:2 hedge:5
    financial_classification:6 valuation_basis:2 equity_effect:2 own_valuation:2 own_impairment:2
    custody:1 custodian_name:60 country_rating:4 regulator:40 filler:109
""")

X3 = layout("""
    record:1 instrument_type:10 country:2 parent_country:2 stock_exchange:20
    administrator_rut:9:0 administrator_dv:1 fund_run:9:0 fund_dv:1 issuer_name:60
    private_fund_rut:9:0 private_fund_dv:1 private_fund_name:30 identifier:30 series:30 currency:6
    fund_type:2 fund_segment:2 real_estate_type:2 underlying_assets:10 units:17:4 subscribed_units:12:0
    fund_participation:5:2 presence_requirement:10 stock_unit_value:15:4 book_value_mclp:11:0
    stock_value_mclp:11:0 historical_cost_mclp:11:0 unit_value:13:2 fair_value_mclp:13:0
    final_value_mclp:12:0 prohibition:1 rating:15 margin_type:10 margin_units:16:3 matching_asset:3
    reserve_backing:5 insurance_plan:30 portfolio_name:30 related:2 subsidiary_type:2
    investment_classification:1 tsa:2 hedge:5 equity_effect:2 financial_classification:6
    valuation_basis:2 own_valuation:2 own_impairment:2 custody:1 custodian_name:60
    country_rating:4 regulator:40 simultaneous:3
""")

X4 = layout("""
    record:1 instrument_type:10 country:2 country_rating:4 regulator:40
""" + PROPERTY_FLAGS + """
    property_location_type:2 property_purpose:2 property_use:1 own_use_percent:5:2 address:40
    remaining_lease_months:3:0 monthly_rent_uf:6:2 purchase_date:8 currency:6
    adjusted_cost_mclp:11:0 accumulated_depreciation_mclp:11:0 book_value_mclp:11:0
    appraisal_mclp:11:0 appraisal_date:8 land_area_m2:7:0 built_area_m2:7:0
    land_appraisal_per_m2:6:2 building_appraisal_per_m2:6:2 impairment_mclp:12:0
    final_value_mclp:12:0 reserve_backing:5 insurance_plan:30 portfolio_name:30 tsa:2 filler:300
""")

X5 = layout("""
    record:1 instrument_type:10 country:2 country_rating:4 regulator:40 subsidiary_name:30
    purchase_date:8 subsidiary_participation:5:2 subsidiary_equity_mclp:14:0
    subsidiary_result_mclp:14:0 financial_statements_date:8 market_value_mclp:13:0
    goodwill_mclp:12:0 final_value_mclp:12:0 tsa:2 filler:436
""")

O = layout("""
    record:1 asset_code:20 identifier:30 country:2 instrument_type:10 currency:6
    historical_cost_mclp:11:0 accumulated_depreciation_mclp:11:0 fair_value_mclp:13:0
    impairment_mclp:12:0 final_value_mclp:12:0 prohibition:1 rating:15 reserve_backing:5
    insurance_plan:30 portfolio_name:30 custody:3 custodian_name:60 custody_eligible:1 margin_type:10
    tsa:2 financial_classification:6 valuation_basis:2 own_valuation:2 own_impairment:2
    risk_report_date:8 asset_type_description:60 real_estate_subsidiary:1 asset_description:100
    country_rating:4 regulator:40
""")

# Counterparty and margin fields deliberately retain contract identifiers and
# separate assets/liabilities; derivative notionals are not portfolio AUM.
COUNTERPARTY = """
    counterparty_rut:9:0 counterparty_dv:1 counterparty_lei:20 counterparty_name:60
    counterparty_country:2 related:2 counterparty_type:2 clearing_house:2 documentation_type:2
    collateral_annex:2 rating:15
"""
BACKING = "reserve_backing:5 insurance_plan:30 portfolio_name:30"
DERIVATIVE_END = """
    collateral_assets_mclp:13:0 collateral_identifier:14 asset_value_mclp:13:0
    liability_value_mclp:13:0 realized_result_mclp:14:0 tsa:2 financial_classification:6
"""
P2 = layout("""
    record:1 contract_objective:3 instrument_type:10 contract_type:2 operation_folio:10 item:3
    operation_date:8 maturity_date:8
""" + COUNTERPARTY + """
    underlying_name:30 underlying_identifier:30 underlying_units:16:3 currency:6 strike_price:13:3
    spot_price:13:3 option_premium:16:3 option_premium_currency:6 underlying_fair_value_mclp:13:0
    contract_count:6:0 contract_fair_value_mclp:13:0 price_source:30
""" + BACKING + DERIVATIVE_END + " filler:93")

P3 = layout("""
    record:1 contract_objective:5 instrument_type:10 contract_type:2 operation_folio:10 item:3
    operation_date:8 maturity_date:8
""" + COUNTERPARTY + BACKING + """
    long_underlying_identifier:30 short_underlying_identifier:30 long_units:16:3 short_units:16:3
    long_notional_mclp:13:0 short_notional_mclp:13:0 currency:6 forward_price:15:5
    underlying_market_value_at_inception:16:3 spot_price:13:3 market_forward_price:15:5
    discount_rate:14:3 contract_fair_value_mclp:14:0 contract_fair_value_ec_mclp:14:0 price_source:30
""" + DERIVATIVE_END + " filler:28")

P4 = layout("""
    record:1 contract_objective:3 instrument_type:10 contract_type:2 operation_folio:10 item:3
    operation_date:8 maturity_date:8
""" + COUNTERPARTY + BACKING + """
    long_underlying_identifier:30 short_underlying_identifier:30 long_units:16:3 short_units:16:3
    long_notional_mclp:13:0 short_notional_mclp:13:0 currency:6 contract_count:6:0
    underlying_market_value_mclp:13:0 spot_price:15:5 future_price_at_purchase:15:5
    future_price_at_reporting:15:5 price_source:30
""" + DERIVATIVE_END + " filler:67")

P5 = layout("""
    record:1 contract_objective:5 instrument_type:10 contract_type:2 operation_folio:10 item:3
    operation_date:8 maturity_date:8
""" + COUNTERPARTY + """
    long_notional_mclp:13:0 short_notional_mclp:13:0 long_amount:16:3 short_amount:16:3
    long_currency:6 short_currency:6 long_contract_rate:30 short_contract_rate:30
    long_rate_type:2 short_rate_type:2 contract_fx_rate:13:3 long_market_rate:13:3
    short_market_rate:13:3 spot_fx_rate:13:3 long_present_value_mclp:13:0 short_present_value_mclp:13:0
    underlying_fair_value_mclp:13:0 contract_fair_value_mclp:14:0 contract_fair_value_ec_mclp:14:0
    price_source:30
""" + BACKING + DERIVATIVE_END)

P6 = layout("""
    record:1 instrument_type:10 operation_folio:10 item:3 operation_date:8 maturity_date:8
    repo_rate:13:3 counterparty_name:60 counterparty_country:2 related:2 underlying_identifier:30
    series:30 underlying_issuer_rut:9:0 underlying_issuer_dv:1 nominal:16:3 purchase_yield:14:3
    issue_rate:14:3 underlying_book_value_mclp:13:0 currency:6 market_rate_or_price:14:3
    underlying_market_value_at_inception:16:3 repo_initial_value:16:3 repo_agreed_value:16:3
    accrued_interest_mclp:13:0 repo_value_mclp:13:0 underlying_market_value_mclp:13:0
    tsa:2 financial_classification:6 filler:228
""")

D = layout("""
    record:1 issuer_rut:9:0 issuer_dv:1 identifier:30 instrument_type:10 issue_date:8 issue_rate:9:4
    discount_rate:8:4 currency:6 rate_reference_days:3:0 discount_treatment:2 flow_count:3:0
    months_between_flows:4:0 interest_type:1 nominal_type:1 prepayment_option:1 prepayment_date:8
    grace_months:3:0
""")
T = layout("""
    record:1 issuer_rut:9:0 issuer_dv:1 identifier:30 coupon_number:3:0 maturity_date:8
    interest_percent:9:6 amortization_percent:10:6 outstanding_percent:9:6
""")
E = layout("record:1 circular:4 information_type:3 instrument_type:10 " +
           " ".join(f"bucket_{i}_uf:16:3" for i in range(1, 11)))
G = layout("""
    record:1 counterparty_rut:9:0 counterparty_dv:1 counterparty_lei:20 counterparty_name:100
    counterparty_country:2 related:2 company_position:2 collateral_type:2 collateral_identifier:14
    instrument_type:10 operation_folio:10 item:3 nominal:17:4 identifier:30 currency:6 country_rating:4
    book_value_original:17:4 book_value_mclp:13:0 fair_value_original:17:4 fair_value_mclp:13:0
    financial_statement_account:7
""")

EXTENDED_LAYOUTS = {
    "B": {"2": B}, "X": {"2": X2, "3": X3, "4": X4, "5": X5},
    "O": {"2": O}, "P": {"2": P2, "3": P3, "4": P4, "5": P5, "6": P6},
    "D": {"2": D}, "T": {"2": T}, "E": {"2": E}, "G": {"2": G},
}
WIDTHS = {"B": 506, "X": 611, "O": 510, "P": 587, "D": 108, "T": 80, "E": 178, "G": 300}
TRAILERS = {"X": "6", "P": "7"}

for _family, _records in EXTENDED_LAYOUTS.items():
    for _record, _fields in _records.items():
        assert sum(f.width for f in _fields) == WIDTHS[_family], (_family, _record)
        assert len({f.name for f in _fields}) == len(_fields), (_family, _record)
