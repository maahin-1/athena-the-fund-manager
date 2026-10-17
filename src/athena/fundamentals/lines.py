"""Which exchange tag feeds which row of a company page. A tag the filing does not carry leaves its row absent; a figure is never guessed.

Four layouts cover the filings: `indas` (industrial and service companies), `finance` (Ind-AS lenders: the same income lines as `indas`, a balance
sheet of loans and borrowings), `bank` (the banking taxonomy) and `insurer` (life insurers' revenue account lines). Every figure is in rupees, as the exchange files it. Derived rows (operating
profit, other assets and so on) are added in `athena.fundamentals.rows`."""
from __future__ import annotations

from dataclasses import dataclass

from athena.pit.xbrl import INCOME_TAGS

INDAS, FINANCE, BANK, INSURER = "indas", "finance", "bank", "insurer"
INCOME, BALANCE, CASHFLOW, BANKING = "income", "balance", "cashflow", "banking"


@dataclass(frozen=True)
class Line:
    key: str
    label: str
    tags: tuple[str, ...]
    how: str = "first"  # "first": the first tag whose value is not zero; "sum": the total of the tags the filing carries


def _l(key: str, label: str, *tags: str, how: str = "first") -> Line:
    return Line(key, label, tags, how)


_INDAS_INCOME = (
    _l("sales", "Sales", "RevenueFromOperations"),
    _l("other_income", "Other Income", "OtherIncome"),
    _l("total_expenses", "Total expenses", "Expenses"),
    _l("interest", "Interest", "FinanceCosts"),
    _l("depreciation", "Depreciation", "DepreciationDepletionAndAmortisationExpense"),
    _l("exceptional", "Exceptional items", "ExceptionalItemsBeforeTax"),
    _l("pbt", "Profit before tax", "ProfitBeforeTax"),
    _l("tax", "Tax", "TaxExpense"),
    _l("net_profit", "Net Profit", "ProfitOrLossAttributableToOwnersOfParent", "ProfitLossForPeriod"),
    _l("eps", "EPS in Rs", *INCOME_TAGS["Diluted EPS"]),
)
_BANK_INCOME = (
    _l("sales", "Interest earned", "InterestEarned"),
    _l("other_income", "Other Income", "OtherIncome"),
    _l("total_income", "Total income", "Income"),
    _l("interest", "Interest expended", "InterestExpended"),
    _l("operating_expenses", "Operating expenses", "OperatingExpenses"),
    _l("provisions", "Provisions and contingencies", "ProvisionsOtherThanTaxAndContingencies"),
    _l("pre_provision_profit", "Operating profit before provisions", "OperatingProfitBeforeProvisionAndContingencies"),
    _l("exceptional", "Exceptional items", "ExceptionalItems"),
    _l("pbt", "Profit before tax", "ProfitLossFromOrdinaryActivitiesBeforeTax", "ProfitBeforeTax"),
    _l("tax", "Tax", "TaxExpense"),
    _l(
        "net_profit", "Net Profit", "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates", "ProfitLossForThePeriod",
        "ProfitLossFromOrdinaryActivitiesAfterTax",
    ),
    _l(
        "eps", "EPS in Rs", "DilutedEarningsPerShareAfterExtraordinaryItems", "DilutedEarningsPerShareBeforeExtraordinaryItems",
        "BasicEarningsPerShareAfterExtraordinaryItems", "BasicEarningsPerShareBeforeExtraordinaryItems",
    ),
)
_INSURER_INCOME = (
    _l("gross_premium", "Gross premium", "GrossPremiumIncome"),
    _l("net_premium", "Net premium", "NetPremiumIncome"),
    _l("investment_income", "Income from investments", "IncomeFromInvestmentsNet"),
    _l("sales", "Total income", "Income"),
    _l("benefits_paid", "Benefits paid", "BenefitsPaidNet"),
    _l("actuarial_liability", "Change in actuarial liability", "ChangeInActuarialLiability"),
    _l("commission", "Commission", "NetCommission", "Commission"),
    _l("operating_expenses", "Operating expenses", "OperatingExpensesRelatedToInsuranceBusiness"),
    _l("total_expenses", "Total expenses", "Expenses"),
    _l("pbt", "Profit before tax", "ProfitLossBeforeTax"),
    _l("tax", "Tax", "ProvisionsForTaxes"),
    _l("net_profit", "Net Profit", "ProfitLossAfterTaxAndExtraordinaryItems", "ProfitLossAfterTaxBeforeExtraordinaryItems"),
    _l(
        "eps", "EPS in Rs", "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",
        "BasicAndDilutedEPSBeforeExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized",
    ),
)
INCOME_LINES: dict[str, tuple[Line, ...]] = {INDAS: _INDAS_INCOME, FINANCE: _INDAS_INCOME, BANK: _BANK_INCOME, INSURER: _INSURER_INCOME}

BALANCE_LINES: dict[str, tuple[Line, ...]] = {
    INDAS: (
        _l("equity_capital", "Equity Capital", "EquityShareCapital"),
        _l("reserves", "Reserves", "OtherEquity"),
        _l("minority", "Minority interest", "NonControllingInterest"),
        _l("borrowings", "Borrowings", "BorrowingsCurrent", "BorrowingsNoncurrent", how="sum"),
        _l("total_assets", "Total Assets", "Assets", "EquityAndLiabilities"),
        _l("fixed_assets", "Fixed Assets", "PropertyPlantAndEquipment", "OtherIntangibleAssets", "Goodwill", how="sum"),
        _l("cwip", "CWIP", "CapitalWorkInProgress", "IntangibleAssetsUnderDevelopment", how="sum"),
        _l(
            "investments", "Investments", "NoncurrentInvestments", "CurrentInvestments", "InvestmentsAccountedForUsingEquityMethod",
            "InvestmentProperty", how="sum",
        ),
        _l("receivables", "Trade receivables", "TradeReceivablesCurrent", "TradeReceivablesNoncurrent", how="sum"),
        _l("inventory", "Inventories", "Inventories"),
        _l("payables", "Trade payables", "TradePayablesCurrent", "TradePayablesNoncurrent", how="sum"),
        _l("cash", "Cash and equivalents", "CashAndCashEquivalents"),
        _l("bank_balances", "Other bank balances", "BankBalanceOtherThanCashAndCashEquivalents"),
        _l("current_assets", "Current assets", "CurrentAssets"),
        _l("current_liabilities", "Current liabilities", "CurrentLiabilities"),
    ),
    FINANCE: (
        _l("equity_capital", "Equity Capital", "EquityShareCapital"),
        _l("reserves", "Reserves", "OtherEquity"),
        _l("minority", "Minority interest", "NonControllingInterest"),
        _l("borrowings", "Borrowings", "Borrowings", "DebtSecurities", "SubordinatedLiabilities", "Deposits", how="sum"),
        _l("total_assets", "Total Assets", "Assets", "EquityAndLiabilities"),
        _l("fixed_assets", "Fixed Assets", "PropertyPlantAndEquipment", "OtherIntangibleAssets", "Goodwill", how="sum"),
        _l("cwip", "CWIP", "CapitalWorkInProgress", "IntangibleAssetsUnderDevelopment", how="sum"),
        _l("investments", "Investments", "Investments"),
        _l("loans", "Loans", "Loans"),
        _l("receivables", "Receivables", "TradeReceivables", "OtherReceivables", how="sum"),
        _l("cash", "Cash and equivalents", "CashAndCashEquivalents"),
        _l("bank_balances", "Other bank balances", "BankBalanceOtherThanCashAndCashEquivalents"),
    ),
    INSURER: (
        _l("equity_capital", "Equity Capital", "ShareCapital", "PaidUpEquityShareCapital"),
        _l("reserves", "Reserves", "ReservesAndSurplusExcludingRevaluationReserve", "ReservesAndSurplus"),
        _l("borrowings", "Borrowings", "Borrowings"),
        _l("policyholders_funds", "Policyholders' funds", "PolicyholdersFunds"),
        _l("total_assets", "Total Assets", "Assets", "SourcesOfFunds"),
        _l("fixed_assets", "Fixed Assets", "FixedAssets"),
        _l("investments", "Investments", "Investments", "InvestmentsShareholders", "InvestmentsPolicyholders", "AssetsHeldToCoverLinkLiabilities"),
        _l("loans", "Loans", "Loans"),
    ),
    BANK: (
        _l("equity_capital", "Capital", "Capital"),
        _l("reserves", "Reserves and surplus", "ReservesAndSurplus"),
        _l("deposits", "Deposits", "Deposits"),
        _l("borrowings", "Borrowings", "Borrowings"),
        _l("total_assets", "Total Assets", "Assets", "CapitalAndLiabilities"),
        _l("fixed_assets", "Fixed Assets", "FixedAssets"),
        _l("investments", "Investments", "Investments"),
        _l("advances", "Advances", "Advances"),
        _l("cash_rbi", "Cash and balances with RBI", "CashAndBalancesWithReserveBankOfIndia"),
        _l("bank_balances", "Balances with banks", "BalancesWithBanksAndMoneyAtCallAndShortNotice"),
    ),
}

CASHFLOW_LINES: tuple[Line, ...] = (
    _l("cfo", "Cash from Operating Activity", "CashFlowsFromUsedInOperatingActivities"),
    _l("cfi", "Cash from Investing Activity", "CashFlowsFromUsedInInvestingActivities"),
    _l("cff", "Cash from Financing Activity", "CashFlowsFromUsedInFinancingActivities"),
    _l(
        "net_cash", "Net Cash Flow", "IncreaseDecreaseInCashAndCashEquivalents",
        "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges",
    ),
    _l(
        "capex", "Capital expenditure", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", how="sum",
    ),
    _l("dividends_paid", "Dividends paid", "DividendsPaidClassifiedAsFinancingActivities"),
)

BANKING_LINES: tuple[Line, ...] = (  # as filed: the exchange does not say whether a ratio is a fraction or a percentage
    _l("cet1", "CET1 ratio", "CET1Ratio"),
    _l("at1", "Additional Tier 1 ratio", "AdditionalTier1Ratio"),
    _l("roa", "Return on assets", "ReturnOnAssets"),
    _l("gross_npa", "Gross NPA", "GrossNonPerformingAssets"),
)

METADATA: tuple[str, ...] = (
    "Symbol", "ISIN", "NatureOfReportStandaloneConsolidated", "WhetherResultsAreAuditedOrUnaudited", "DateOfStartOfReportingPeriod",
    "DateOfEndOfReportingPeriod", "DateOfStartOfFinancialYear", "PaidUpValueOfEquityShareCapital", "FaceValueOfEquityShareCapital",
    "LevelOfRoundingUsedInFinancialStatements", "ReportingQuarter",
)


def all_tags() -> frozenset[str]:
    """Every exchange tag the mapping reads, with the metadata tags: what a test fixture must keep."""
    tables = [*INCOME_LINES.values(), *BALANCE_LINES.values(), CASHFLOW_LINES, BANKING_LINES]
    return frozenset(tag for table in tables for line in table for tag in line.tags) | frozenset(METADATA)
