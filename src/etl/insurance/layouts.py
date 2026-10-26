"""Circular 1835 field order, lengths, and units for the pilot.

Source: https://www.cmfchile.cl/sitio/seil/software-manual/sgsci/Anexos_Tecnicos_Circular_N1835.pdf
Current I/A/F/C layouts, verified against August/September 2026 public exports.
The older individual PDFs linked by SGSCI_circ_1835.doc have obsolete offsets.

Do not infer offsets from whitespace or copy monetary units across file families.
The actual archive format is validated against these layouts by the parser.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    width: int
    scale: int | None = None  # None means text; otherwise numeric decimal places.


def layout(spec: str) -> tuple[Field, ...]:
    fields = []
    for token in spec.split():
        parts = token.split(":")
        fields.append(Field(parts[0], int(parts[1]), int(parts[2]) if len(parts) == 3 else None))
    return tuple(fields)


IDENTITY = layout("record:1 insurer_rut:9:0 insurer_dv:1 insurer_name:60 period:6")

LAYOUTS = {
    "F": layout("""
        record:1 administrator_rut:9:0 administrator_dv:1 fund_run:9:0 fund_dv:1
        instrument_type:10 identifier:10 fund_type:2 series:30 units:17:4
        currency:6 unit_value_clp:17:4 purchase_value_clp:13:0 fair_value_clp:13:0
        impairment_clp:14:0 final_value_mclp:12:0 prohibition:1 margin_type:10
        margin_units:17:4 reserve_backing:5 insurance_plan:30 portfolio_name:30
        custody:3 custodian_name:60 custody_eligible:1 related:2 rating:15 tsa:2
        hedge:5 simultaneous:3 financial_classification:6 valuation_basis:2 own_valuation:2 own_impairment:2
    """),
    "A": layout("""
        record:1 issuer_or_admin_rut:9:0 issuer_or_admin_dv:1 fund_run:9:0 fund_dv:1
        private_fund_rut:9:0 private_fund_dv:1 private_fund_name:30
        instrument_type:10 identifier:60 series:30 units:17:4 market_presence:5:2 economic_activity:6
        purchase_value_clp:13:0 book_value_clp:13:0 stock_value_clp:13:0 fair_value_mclp:13:0
        impairment_mclp:14:0 equity_effect:2 voluntary_provision_clp:14:0 final_value_mclp:13:0
        currency:6 economic_adjustment:1 prohibition:1 margin_type:10 margin_units:17:4
        lent:1 lent_units:13:0 reserve_backing:5 insurance_plan:30 portfolio_name:30
        custody:3 custodian_name:60 custody_eligible:1 related:2 subsidiary_type:2
        subsidiary_participation:5:2 subsidiary_equity_mclp:14:0 subsidiary_result_mclp:14:0
        rating:15 simultaneous:3 tsa:2 hedge:5 financial_classification:6 valuation_basis:2
        own_valuation:2 own_impairment:2 underlying_assets:10 fund_type:2 fund_segment:2
        real_estate_type:2 subscribed_units:12:0 fund_participation:5:2 development_progress:2 management_type:1
    """),
    "I": layout("""
        record:1 operation_code:3 operation_folio:10 item:3 purchase_date:8 payment_date:8
        issuer_rut:9:0 issuer_dv:1 instrument_type:10 identifier:30 issue_date:8
        registration:5 registration_date:8 series:10 country:2 nominal:17:4
        current_nominal:17:4 currency:6 prohibition:1 margin_type:10 matured:1 state_guarantee:1
        prepayment_option:1 prepayment_date:8 reserve_backing:5 insurance_plan:30
        portfolio_name:30 matching_asset:3 descriptor:1 related:2 rating:15
        increased_credit_risk:1 investment_classification:1 maturity_date:8 months_to_maturity:3:0
        duration:4:2 amortization_type:2 issue_rate:8:4 base_rate:12 tsa:2
        hedge:5 underlying_assets:7 financial_classification:6 valuation_basis:2 own_valuation:2 equity_effect:2 own_impairment:2
        economic_activity:6 economic_group:3 price_source:13 price_provider_rut:9:0
        price_provider_dv:1 nominal_type:1 discount_treatment:2 repo_initial_clp:13:0
        repo_agreed_value:17:4 repo_currency:6 repo_maturity_date:8 repo_rate:8:4
        repo_rate_basis:12 repo_nominal_type:1 repo_value_mclp:13:0 mortgage_agent:4 mortgage_purpose:2
        mortgage_use:1 leasing_use:1 debtor_type:1 mortgage_debtor_rut:9:0 mortgage_debtor_dv:1
        first_payment_date:8 instalment:17:4 final_instalment:17:4 unpaid_count:3:0
        unpaid_amount:17:4 unpaid_mclp:13:0 collateral_value:17:4 collateral_mclp:13:0
        appraisal_date:8 outstanding_debt:17:4 outstanding_debt_mclp:13:0
        issue_spread:7:4 endorsement:2 remaining_months:3:0 original_loan_to_value:8:4 current_loan_to_value:8:4 syndicated_debtor_rut:9:0
        syndicated_debtor_dv:1 syndicated_debtor_name:60 syndicated_participation:5:2
        syndicated_total_mclp:13:0 lead_rut:9:0 lead_dv:1 lead_name:60 bank_classification:2
        purchase_par_percent:7:4 purchase_value_clp:13:0 yield_without_cost:8:4
        purchase_yield:8:4 amortized_cost_original:15:4 amortized_cost_clp:13:0
        market_yield:8:4 fair_value_clp:13:0 impairment_clp:14:0 final_value_mclp:13:0
        custody:3 custodian_name:60 custody_eligible:1
    """),
    "C": layout("""
        record:1 investment_type:3 final_value_mclp:12:0 representative_mclp:12:0
        nonrepresentative_mclp:12:0 amortized_cost_mclp:14:0 fair_value_mclp:14:0
        cash_equivalent_mclp:14:0 cui_apv_mclp:14:0 other_classification_mclp:14:0
        subsidiaries_mclp:14:0 associates_mclp:14:0
    """),
}
