# B1a: Fundamentals statement store and ratio engine (phases 6a and 6b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest company statements from NSE's XBRL filings into a local DuckDB file, with every row Screener's company page shows, and compute the ratios from them, proven on the Nifty 50.

**Architecture:** A new Python package `athena.fundamentals` reuses the throttled NSE client in `athena.pit.nse_results`. A mapping module says which exchange tag feeds which row, per layout (industrial, lender, bank). A parser turns one filing into period-keyed rows, a store (DuckDB, short write bursts, read-only for the Rust server later) keeps them, an ingest job fetches resumably, and a pure ratio module computes the ratios. B1b (separate plan) adds the server routes, the Fundamentals page and the Chart Lab engine on top.

**Tech Stack:** Python 3.11+, DuckDB, defusedxml, requests (through the existing `NseResults`), pytest. Design: [parity master design](../specs/2026-10-17-parity-master-design.md) section 3.3, findings in [Screener breakdown](../specs/2026-10-17-screener-breakdown.md).

## Global Constraints

- Every figure is stored in **rupees** exactly as the exchange files it; presentation divides by 10,000,000 for crores. A tag the filing lacks leaves its row absent; **a figure is never guessed**.
- XML is parsed with `defusedxml` only.
- NSE requests go through `NseResults` (1.5 s gap, back-off 30 s and 90 s on 403, 429, 503). Tests never touch the network; live checks use the `--live` marker or a script.
- DuckDB is written in **short bursts** (open, write one filing, close) because the Rust server will open the file read-only; one writer at a time.
- Statement depth is fixed by the exchange (verified 2026-10-17 on real filings): income statement from FY2019, balance sheet and cash flow from FY2023 (March 2023 filings). Earlier balance sheets are B2's gap-fill; this plan must show them as absent, not as zero.
- Default fetch scope: consolidated filings (standalone only when a company has no consolidated filing, or with `--standalone`), all annual filings plus the latest 13 quarterly ones, newest first.
- No secrets are read or printed; no scheduled job is created.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| `src/athena/fundamentals/__init__.py` | package marker |
| `src/athena/fundamentals/lines.py` | which tag feeds which row, per layout; metadata tags |
| `src/athena/fundamentals/rows.py` | `parse_rows(xbrl_text) -> Statements`; derived rows; reconciliation notes |
| `src/athena/fundamentals/store.py` | `FundamentalsStore` (DuckDB schema, saves, reads, run state) |
| `src/athena/fundamentals/universe.py` | Nifty 50, Nifty 500 and full-market symbol lists, in build order |
| `src/athena/fundamentals/ingest.py` | per-company fetch and save, resumable CLI |
| `src/athena/fundamentals/ratios.py` | pure ratio and growth functions |
| `src/athena/fundamentals/show.py` | text tables of a stored company (the proof and a debugging aid) |
| `scripts/make_xbrl_fixtures.py` | trims real filings to test fixtures |
| `scripts/fund_crosscheck.py` | compares stored annual figures with Yahoo's |
| `tests/fixtures/xbrl/*.xml` | four trimmed real filings |
| `tests/test_fund_lines.py`, `test_fund_rows.py`, `test_fund_store.py`, `test_fund_universe.py`, `test_fund_ingest.py`, `test_fund_ratios.py`, `test_fund_show.py` | tests |

---

### Task 1: The row mapping

**Files:**
- Create: `src/athena/fundamentals/__init__.py` (empty), `src/athena/fundamentals/lines.py`
- Test: `tests/test_fund_lines.py`

**Interfaces:**
- Produces: `Line(key, label, tags, how)`; constants `INDAS, FINANCE, BANK` (layouts) and `INCOME, BALANCE, CASHFLOW, BANKING` (statements); `INCOME_LINES: dict[layout, tuple[Line, ...]]`, `BALANCE_LINES` likewise, `CASHFLOW_LINES`, `BANKING_LINES` (tuples); `METADATA: tuple[str, ...]`; `all_tags() -> frozenset[str]`.

- [ ] **Step 1: Write the failing test** `tests/test_fund_lines.py`

```python
from athena.fundamentals import lines as spec


def test_every_line_names_a_tag_and_no_key_repeats_inside_a_table():
    tables = [*spec.INCOME_LINES.values(), *spec.BALANCE_LINES.values(), spec.CASHFLOW_LINES, spec.BANKING_LINES]
    for table in tables:
        keys = [line.key for line in table]
        assert len(keys) == len(set(keys)), keys
        assert all(line.tags for line in table)
        assert all(line.how in ("first", "sum") for line in table)


def test_all_three_layouts_have_an_income_statement_and_a_balance_sheet():
    for layout in (spec.INDAS, spec.FINANCE, spec.BANK):
        assert any(line.key == "sales" for line in spec.INCOME_LINES[layout])
        assert any(line.key == "total_assets" for line in spec.BALANCE_LINES[layout])


def test_tags_seen_in_real_filings_are_covered():
    tags = spec.all_tags()
    for tag in (
        "RevenueFromOperations", "InterestEarned", "Advances", "CashFlowsFromUsedInOperatingActivities", "EquityShareCapital",
        "Symbol", "DateOfEndOfReportingPeriod", "Loans", "DebtSecurities", "InterestExpended",
    ):
        assert tag in tags
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_lines.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.fundamentals'`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/lines.py`

```python
"""Which exchange tag feeds which row of a company page. A tag the filing does not carry leaves its row absent; a figure is never guessed.

Three layouts cover the filings: `indas` (industrial and service companies), `finance` (Ind-AS lenders: the same income lines as `indas`, a balance
sheet of loans and borrowings) and `bank` (the banking taxonomy). Every figure is in rupees, as the exchange files it. Derived rows (operating
profit, other assets and so on) are added in `athena.fundamentals.rows`."""
from __future__ import annotations

from dataclasses import dataclass

from athena.pit.xbrl import INCOME_TAGS

INDAS, FINANCE, BANK = "indas", "finance", "bank"
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
INCOME_LINES: dict[str, tuple[Line, ...]] = {INDAS: _INDAS_INCOME, FINANCE: _INDAS_INCOME, BANK: _BANK_INCOME}

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
```

- [ ] **Step 4: Run it and see it pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_lines.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals tests/test_fund_lines.py
git commit -m "feat(fundamentals): the row mapping from exchange tags, per layout"
```

---

### Task 2: Test fixtures from real filings

**Files:**
- Create: `scripts/make_xbrl_fixtures.py`, `tests/fixtures/xbrl/{tcs_fy2024,tcs_fy2020,hdfcbank_fy2024,bajfinance_fy2024}.xml`

**Interfaces:**
- Consumes: `athena.fundamentals.lines.all_tags()`.
- Produces: four trimmed real filings (each keeps its root element, undimensioned contexts, and only the facts the mapping reads). The raw downloads sit in the session scratch directory `C:\Users\LEGION\AppData\Local\Temp\claude\d--projects-learn-project-athena-the-fund-manager\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\xbrl` (TCS_31-Mar-2024.xml, TCS_31-Mar-2020.xml, HDFCBANK_31-Mar-2024.xml, BAJFINANCE_31-Mar-2024.xml). If that folder is gone, re-fetch with the same four filings through `NseResults` (annual list, consolidated, those period ends).

- [ ] **Step 1: Write the trimming script** `scripts/make_xbrl_fixtures.py`

```python
"""Trim real NSE results filings to the facts the statement mapping reads, for use as test fixtures.

    python scripts/make_xbrl_fixtures.py SOURCE_DIR NAME=FILE.xml [NAME=FILE.xml ...]

Writes tests/fixtures/xbrl/NAME.xml: the root element (with its namespaces), every context without a dimension, and the facts whose tag the mapping
reads. The filings are public regulatory documents; only the figures the tests assert on are kept."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from athena.fundamentals.lines import all_tags  # noqa: E402

ROOT = re.compile(r"<xbrli:xbrl[^>]*>", re.S)
CONTEXT = re.compile(r"<xbrli:context\b.*?</xbrli:context>", re.S)
CONTEXT_ID = re.compile(r'<xbrli:context\s+id="([\w-]+)"')
FACT = re.compile(r"<([\w-]+):(\w+)\b([^>]*?\bcontextRef=\"([\w-]+)\"[^>]*)>([^<]*)</\1:\2>")
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xbrl"


def trim(text: str) -> str:
    keep = all_tags()
    contexts = [c for c in CONTEXT.findall(text) if "<xbrli:scenario" not in c]
    ids = {CONTEXT_ID.match(c).group(1) for c in contexts}
    facts = [m.group(0) for m in FACT.finditer(text) if m.group(2) in keep and m.group(4) in ids]
    head = ROOT.search(text).group(0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + head + "\n" + "\n".join(contexts + facts) + "\n</xbrli:xbrl>\n"


def main(argv: list[str]) -> int:
    source = Path(argv[0])
    OUT.mkdir(parents=True, exist_ok=True)
    for pair in argv[1:]:
        name, file = pair.split("=", 1)
        text = (source / file).read_text(encoding="utf-8")
        trimmed = trim(text)
        (OUT / f"{name}.xml").write_text(trimmed, encoding="utf-8")
        print(f"{name}: {len(text):,} -> {len(trimmed):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: Generate the fixtures**

Run:
```bash
S="C:/Users/LEGION/AppData/Local/Temp/claude/d--projects-learn-project-athena-the-fund-manager/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/xbrl"
.venv/Scripts/python.exe scripts/make_xbrl_fixtures.py "$S" tcs_fy2024=TCS_31-Mar-2024.xml tcs_fy2020=TCS_31-Mar-2020.xml hdfcbank_fy2024=HDFCBANK_31-Mar-2024.xml bajfinance_fy2024=BAJFINANCE_31-Mar-2024.xml
```
Expected: four lines, each trimmed file under 40,000 bytes. If a file is empty of facts (the regex missed its fact syntax), open the raw file's first fact element and adjust `FACT` before going on.

- [ ] **Step 3: Sanity check the fixtures parse and hold the known figures**

Run:
```bash
.venv/Scripts/python.exe -c "
from defusedxml import ElementTree as E
for n in ('tcs_fy2024','tcs_fy2020','hdfcbank_fy2024','bajfinance_fy2024'):
    t=open(f'tests/fixtures/xbrl/{n}.xml',encoding='utf-8').read(); r=E.fromstring(t.encode()); print(n, len(list(r)), 'elements')
t=open('tests/fixtures/xbrl/tcs_fy2024.xml',encoding='utf-8').read()
assert '2408930000000' in t and '1464490000000' in t and '443380000000' in t
print('known TCS figures present')
"
```
Expected: four element counts above 60 and `known TCS figures present`.

- [ ] **Step 4: Commit**

```bash
git add scripts/make_xbrl_fixtures.py tests/fixtures/xbrl
git commit -m "test(fundamentals): trimmed real filings as fixtures (industrial, older, bank, lender)"
```

---

### Task 3: Parse a filing into period-keyed rows

**Files:**
- Create: `src/athena/fundamentals/rows.py`
- Test: `tests/test_fund_rows.py`

**Interfaces:**
- Consumes: Task 1's `lines`; fixtures from Task 2.
- Produces: `Statements` dataclass `(symbol, basis, layout, audited, shares, income, balance, cashflow, banking, notes)` where `income`, `cashflow`, `banking` are `dict[tuple[date, date], dict[str, float]]` keyed `(period_start, period_end)` and `balance` is `dict[date, dict[str, float]]` keyed by balance-sheet date; `parse_rows(text: str) -> Statements` (raises `ValueError` for anything that is not a results filing); `derive_income`, `derive_balance`, `derive_cashflow`, `reconcile`.

Facts the parser relies on (verified on real filings): context `OneD` is the reporting quarter (in an annual filing, the last quarter), `FourD` the year to date (the whole year in an annual filing), `OneI` the balance sheet date; the real period of each is stated by the `DateOfStartOfReportingPeriod` and `DateOfEndOfReportingPeriod` facts, not by the context's own dates.

- [ ] **Step 1: Write the failing tests** `tests/test_fund_rows.py`

```python
from datetime import date
from pathlib import Path

import pytest

from athena.fundamentals import lines as spec
from athena.fundamentals import rows
from athena.fundamentals.rows import parse_rows

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
FY24 = (date(2023, 4, 1), date(2024, 3, 31))
Q4_24 = (date(2024, 1, 1), date(2024, 3, 31))
BS24 = date(2024, 3, 31)


def load(name: str):
    return parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))


def test_an_industrial_annual_filing_gives_the_year_the_last_quarter_the_balance_sheet_and_the_cash_flow():
    st = load("tcs_fy2024")
    assert (st.symbol, st.basis, st.layout) == ("TCS", "consolidated", "indas")
    assert st.income[FY24]["sales"] == 2408930000000.0
    assert st.income[FY24]["pbt"] == 619970000000.0
    assert st.income[Q4_24]["sales"] == 612370000000.0
    assert st.balance[BS24]["total_assets"] == 1464490000000.0
    assert st.cashflow[FY24]["cfo"] == 443380000000.0
    assert 3.5e9 < st.shares < 3.8e9


def test_derived_rows_follow_from_the_filed_lines():
    st = load("tcs_fy2024")
    inc = st.income[FY24]
    assert inc["expenses"] == pytest.approx(inc["total_expenses"] - inc["interest"] - inc["depreciation"])
    assert inc["operating_profit"] == pytest.approx(inc["sales"] - inc["expenses"])
    assert inc["opm"] == pytest.approx(100 * inc["operating_profit"] / inc["sales"])
    assert inc["tax_pct"] == pytest.approx(100 * inc["tax"] / inc["pbt"])
    bal = st.balance[BS24]
    assert bal["equity_capital"] + bal["reserves"] + bal["borrowings"] + bal["other_liabilities"] == pytest.approx(bal["total_assets"])
    assert bal["fixed_assets"] + bal.get("cwip", 0.0) + bal["investments"] + bal["other_assets"] == pytest.approx(bal["total_assets"])
    cf = st.cashflow[FY24]
    assert cf["fcf"] == pytest.approx(cf["cfo"] - cf["capex"])


def test_the_industrial_filing_reconciles_to_its_own_profit_before_tax():
    assert load("tcs_fy2024").notes == ()


def test_a_filing_from_the_results_only_era_has_income_and_no_balance_sheet_or_cash_flow():
    st = load("tcs_fy2020")
    assert (date(2019, 4, 1), date(2020, 3, 31)) in st.income
    assert st.balance == {} and st.cashflow == {}


def test_a_bank_filing_uses_the_banking_layout():
    st = load("hdfcbank_fy2024")
    assert st.layout == "bank"
    assert st.income[FY24]["sales"] == 2836490200000.0  # interest earned
    assert st.income[FY24]["pbt"] == 751841400000.0
    bal = st.balance[BS24]
    assert bal["total_assets"] == 40301942600000.0
    assert bal["deposits"] > 0 and bal["advances"] > 0
    assert bal["equity_capital"] + bal["reserves"] + bal["borrowings"] + bal["deposits"] + bal["other_liabilities"] == pytest.approx(bal["total_assets"])
    inc = st.income[FY24]
    assert inc["operating_profit"] == pytest.approx(inc["sales"] - inc["interest"] - inc["expenses"])


def test_a_lender_filing_under_ind_as_gets_the_finance_layout():
    st = load("bajfinance_fy2024")
    assert st.layout == "finance"
    assert st.income[FY24]["sales"] == 549694900000.0
    assert st.income[FY24]["pbt"] == 193019300000.0
    assert st.cashflow[FY24]["cfo"] == -727601400000.0
    bal = st.balance[BS24]
    assert bal["total_assets"] == 3757416200000.0
    assert bal["loans"] > 0 and bal["borrowings"] > 0


def test_text_that_is_not_a_results_filing_is_refused():
    with pytest.raises(ValueError):
        parse_rows("<html>error page</html>")
    with pytest.raises(ValueError):
        parse_rows("not xml at all")


def test_a_zero_in_the_first_tag_does_not_hide_the_tag_that_holds_the_figure():
    line = spec.Line("eps", "EPS", ("Diluted", "Basic"))
    assert rows._pick({"Diluted": "0.00", "Basic": "7.5"}, line) == 7.5
    assert rows._pick({"Diluted": "0.00"}, line) == 0.0
    assert rows._pick({}, line) is None
    total = spec.Line("debt", "Debt", ("A", "B"), "sum")
    assert rows._pick({"A": "10", "B": "5"}, total) == 15.0
    assert rows._pick({"B": "5"}, total) == 5.0


def test_a_profit_that_does_not_follow_from_the_lines_is_noted():
    period = (date(2024, 1, 1), date(2024, 3, 31))
    assert rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "pbt": 500.0}})
    assert not rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "pbt": 900.0}})
    assert not rows.reconcile({period: {"sales": 1000.0, "expenses": 100.0, "exceptional": -50.0, "pbt": 850.0}})
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_rows.py -q`
Expected: FAIL, `ImportError: cannot import name 'rows'`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/rows.py`

```python
"""One results filing as rows keyed by period. The exchange files three contexts: `OneD` the reporting quarter (in an annual filing, the last
quarter), `FourD` the year to date (the whole year in an annual filing) and `OneI` the balance sheet date; the true period of each is stated by
the `DateOfStartOfReportingPeriod` and `DateOfEndOfReportingPeriod` facts, not by the context's own dates. Every figure is in rupees."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from defusedxml import ElementTree

from athena.fundamentals import lines as spec

_XBRLI = "{http://www.xbrl.org/2003/instance}"
CURRENT, CUMULATIVE, INSTANT = "OneD", "FourD", "OneI"
RECONCILE_TOLERANCE = 0.005  # of sales


@dataclass(frozen=True)
class Statements:
    symbol: str
    basis: str  # "consolidated" | "standalone" | ""
    layout: str  # spec.INDAS | spec.FINANCE | spec.BANK
    audited: bool | None
    shares: float | None  # paid-up equity capital over face value
    income: dict[tuple[date, date], dict[str, float]]
    balance: dict[date, dict[str, float]]
    cashflow: dict[tuple[date, date], dict[str, float]]
    banking: dict[tuple[date, date], dict[str, float]]
    notes: tuple[str, ...]


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _quarter_start(end: date) -> date:
    month, year = end.month - 2, end.year
    if month < 1:
        month, year = month + 12, year - 1
    return date(year, month, 1)


def _pick(facts: dict[str, str], line: spec.Line) -> float | None:
    """The line's value from one context's facts. "first": the first tag that is not zero (filers fill the tag they do not use with 0.00, and that
    must not hide the tag that holds the figure), a zero only when every tag present is zero; "sum": the total of the tags present."""
    if line.how == "sum":
        values = [v for tag in line.tags if (v := _number(facts.get(tag))) is not None]
        return sum(values) if values else None
    found = None
    for tag in line.tags:
        value = _number(facts.get(tag))
        if value:
            return value
        if value is not None:
            found = value
    return found


def _lines(facts: dict[str, str], table: tuple[spec.Line, ...]) -> dict[str, float]:
    return {line.key: v for line in table if (v := _pick(facts, line)) is not None}


def derive_income(got: dict[str, float], layout: str) -> dict[str, float]:
    """Operating expenses, operating profit, OPM % and tax % from the filed lines. For industrial and lender filings operating expenses are total
    expenses without interest and depreciation; for banks they are operating expenses plus provisions, and operating profit is interest earned
    less interest expended less those expenses (Screener's "financing profit")."""
    out = dict(got)
    sales = got.get("sales")
    if layout == spec.BANK:
        interest, opex = got.get("interest"), got.get("operating_expenses")
        if None not in (sales, interest, opex):
            out["expenses"] = opex + got.get("provisions", 0.0)
            out["operating_profit"] = sales - interest - out["expenses"]
    else:
        total = got.get("total_expenses")
        if None not in (sales, total):
            out["expenses"] = total - got.get("interest", 0.0) - got.get("depreciation", 0.0)
            out["operating_profit"] = sales - out["expenses"]
    if sales and "operating_profit" in out:
        out["opm"] = out["operating_profit"] / sales * 100
    pbt, tax = got.get("pbt"), got.get("tax")
    if pbt and tax is not None:
        out["tax_pct"] = tax / pbt * 100
    return out


def derive_balance(got: dict[str, float], layout: str) -> dict[str, float]:
    """Other assets and other liabilities as the remainder of the total, as Screener shows them (minority interest sits in other liabilities)."""
    out = dict(got)
    total = got.get("total_assets")
    if total is None:
        return out
    out["other_assets"] = total - got.get("fixed_assets", 0.0) - got.get("cwip", 0.0) - got.get("investments", 0.0)
    funded = got.get("equity_capital", 0.0) + got.get("reserves", 0.0) + got.get("borrowings", 0.0) + got.get("deposits", 0.0)
    out["other_liabilities"] = total - funded
    return out


def derive_cashflow(got: dict[str, float]) -> dict[str, float]:
    """Free cash flow: operating cash flow less capital expenditure (our definition; the page says so)."""
    out = dict(got)
    if "cfo" in got and "capex" in got:
        out["fcf"] = got["cfo"] - got["capex"]
    return out


def reconcile(income: dict[tuple[date, date], dict[str, float]]) -> tuple[str, ...]:
    """A note for each period whose profit before tax does not follow from its lines (within half a percent of sales, with or without the
    exceptional items). A note is information, not an error: associates and one-offs can explain it."""
    notes = []
    for (_, end), got in income.items():
        sales, pbt = got.get("sales"), got.get("pbt")
        if sales is None or pbt is None:
            continue
        base = sales + got.get("other_income", 0.0) - got.get("interest", 0.0) - got.get("depreciation", 0.0) - got.get("expenses", 0.0)
        exceptional = got.get("exceptional", 0.0)
        if min(abs(pbt - base), abs(pbt - base - exceptional), abs(pbt - base + exceptional)) > RECONCILE_TOLERANCE * abs(sales):
            notes.append(f"{end}: profit before tax does not follow from the lines (difference {pbt - base:,.0f})")
    return tuple(notes)


def parse_rows(text: str) -> Statements:
    """A results filing as `Statements`. Raises ValueError for anything that is not one (the exchange answers with an error page for filings that
    predate XBRL)."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"not an XBRL filing: {exc}") from exc
    dimensioned = {c.get("id") for c in root.iter(f"{_XBRLI}context") if c.find(f"{_XBRLI}scenario") is not None}
    by_context: dict[str, dict[str, str]] = {}
    for element in root:
        ref = element.get("contextRef")
        if ref is None or ref in dimensioned or not (element.text or "").strip():
            continue
        by_context.setdefault(ref, {})[element.tag.rsplit("}", 1)[-1]] = element.text.strip()
    current, whole, instant = (by_context.get(name, {}) for name in (CURRENT, CUMULATIVE, INSTANT))
    end = _day(current.get("DateOfEndOfReportingPeriod"))
    if end is None:
        raise ValueError("the filing does not say which period it covers")
    quarter = (_day(current.get("DateOfStartOfReportingPeriod")) or _quarter_start(end), end)
    year = (
        _day(whole.get("DateOfStartOfReportingPeriod")) or _day(current.get("DateOfStartOfFinancialYear")) or quarter[0],
        _day(whole.get("DateOfEndOfReportingPeriod")) or end,
    )
    present = set(current) | set(whole) | set(instant)
    if "InterestExpended" in present:
        layout = spec.BANK
    elif {"Loans", "DebtSecurities"} <= present:
        layout = spec.FINANCE
    else:
        layout = spec.INDAS

    income: dict[tuple[date, date], dict[str, float]] = {}
    for period, facts in ((quarter, current), (year, whole)):
        got = derive_income(_lines(facts, spec.INCOME_LINES[layout]), layout)
        if got and period not in income:  # the quarter's own context wins when both name one period
            income[period] = got
    balance_lines = derive_balance(_lines(instant, spec.BALANCE_LINES[layout]), layout)
    balance = {end: balance_lines} if balance_lines else {}
    cash = derive_cashflow(_lines(whole, spec.CASHFLOW_LINES))
    cashflow = {year: cash} if cash else {}
    banking: dict[tuple[date, date], dict[str, float]] = {}
    for period, facts in ((quarter, current), (year, whole)):
        got = _lines(facts, spec.BANKING_LINES)
        if got and period not in banking:
            banking[period] = got

    nature = (current.get("NatureOfReportStandaloneConsolidated") or "").strip().lower()
    audited = current.get("WhetherResultsAreAuditedOrUnaudited")
    paid_up, face_value = _number(current.get("PaidUpValueOfEquityShareCapital")), _number(current.get("FaceValueOfEquityShareCapital"))
    return Statements(
        symbol=(current.get("Symbol") or "").strip(),
        basis=nature if nature in ("consolidated", "standalone") else "",
        layout=layout,
        audited=None if audited is None else audited.strip().lower() == "audited",
        shares=paid_up / face_value if paid_up and face_value else None,
        income=income,
        balance=balance,
        cashflow=cashflow,
        banking=banking,
        notes=reconcile(income),
    )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_rows.py -q`
Expected: `9 passed`. If a fixture assertion fails, **read the actual value first** (print the `Statements`) and decide whether the mapping or the test's expectation is wrong; do not loosen a tolerance to make it pass. Likely places: a missing tag in a layout (add it to `lines.py` and rerun Task 2's generator so the fixture keeps it), or `notes` non-empty for TCS (inspect the difference: associates' share of profit or an exceptional item explains it, and the rule in `reconcile` may need to subtract `share of associates`).

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals/rows.py tests/test_fund_rows.py
git commit -m "feat(fundamentals): parse a results filing into period-keyed statement rows"
```

---

### Task 4: The store

**Files:**
- Create: `src/athena/fundamentals/store.py`
- Test: `tests/test_fund_store.py`

**Interfaces:**
- Consumes: `Statements` (Task 3).
- Produces: `FundamentalsStore(path=DEFAULT_PATH, read_only=False)` (context manager) with `save_company(symbol, name, isin, industry, layout, shares)`, `save_statements(st, url, filed, source="nse-xbrl") -> int`, `record_filing(url, symbol, basis, period_end, annual, filed, status, error="")`, `filing_status(url) -> str | None`, `lines(symbol, basis, statement) -> dict[tuple[date, date], dict[str, float]]` (balance keyed `(end, end)`), `bases(symbol) -> list[str]`, `company(symbol) -> dict | None`, `set_run(symbol, status, filings=0, error="", blocked_until=None)`, `run(symbol) -> dict | None`, `blocked_until() -> datetime | None`, `coverage() -> list[dict]`. `DEFAULT_PATH = Path(".athena") / "fundamentals.duckdb"`. Times are stored as naive UTC.

- [ ] **Step 1: Write the failing tests** `tests/test_fund_store.py`

```python
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
FY24 = (date(2023, 4, 1), date(2024, 3, 31))
UTC = timezone.utc


def tcs():
    return parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))


def test_statements_round_trip_by_period_and_statement():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        assert store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC)) > 0
        income = store.lines("TCS", "consolidated", "income")
        balance = store.lines("TCS", "consolidated", "balance")
        cash = store.lines("TCS", "consolidated", "cashflow")
        assert income[FY24]["sales"] == st.income[FY24]["sales"]
        assert balance[(date(2024, 3, 31), date(2024, 3, 31))]["total_assets"] == 1464490000000.0
        assert cash[FY24]["cfo"] == 443380000000.0
        assert store.bases("TCS") == ["consolidated"]


def test_saving_the_same_filing_twice_does_not_duplicate_rows():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        first = store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC))
        store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC))
        total = store._con.execute("SELECT count(*) FROM statement_lines").fetchone()[0]
        assert total == first


def test_a_later_filing_replaces_an_earlier_one_and_an_earlier_one_never_replaces_a_later_one():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        store.save_statements(st, "old", datetime(2024, 4, 12, tzinfo=UTC))
        revised = {**st.income[FY24], "sales": 1.0}
        newer = type(st)(**{**st.__dict__, "income": {FY24: revised}, "balance": {}, "cashflow": {}, "banking": {}})
        store.save_statements(newer, "new", datetime(2024, 5, 1, tzinfo=UTC))
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 1.0
        store.save_statements(st, "old2", datetime(2024, 4, 13, tzinfo=UTC))  # filed before the revision: ignored
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 1.0


def test_filing_status_run_state_and_the_block_marker():
    with FundamentalsStore(":memory:") as store:
        assert store.filing_status("u1") is None
        store.record_filing("u1", "TCS", "consolidated", date(2024, 3, 31), True, datetime(2024, 4, 12, tzinfo=UTC), "ok")
        store.record_filing("u2", "TCS", "consolidated", date(2023, 3, 31), True, datetime(2023, 4, 12, tzinfo=UTC), "failed", "unreadable")
        assert store.filing_status("u1") == "ok" and store.filing_status("u2") == "failed"
        assert store.run("TCS") is None and store.blocked_until() is None
        until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
        store.set_run("TCS", "blocked", filings=3, error="HTTP 403", blocked_until=until)
        assert store.run("TCS")["status"] == "blocked"
        assert store.blocked_until() == until
        store.set_run("TCS", "ok", filings=62)
        assert store.blocked_until() is None


def test_company_row_and_coverage():
    with FundamentalsStore(":memory:") as store:
        store.save_company("TCS", "Tata Consultancy Services", "INE467B01029", "IT - Software", "indas", 3.6e9)
        assert store.company("TCS")["industry"] == "IT - Software"
        store.save_statements(tcs(), "u1", datetime(2024, 4, 12, tzinfo=UTC))
        cover = store.coverage()
        assert cover and cover[0]["symbol"] == "TCS" and cover[0]["periods"] >= 2
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_store.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'athena.fundamentals.store'`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/store.py`

```python
"""The fundamentals database: statements by company, basis, statement and period, the filings seen and the state of each company's ingest.

One DuckDB file. Python writes in short bursts (open, write one filing, close); the terminal's server opens it read-only. Times are naive UTC."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.fundamentals import lines as spec
from athena.fundamentals.rows import Statements

DEFAULT_PATH = Path(".athena") / "fundamentals.duckdb"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS companies (symbol TEXT PRIMARY KEY, name TEXT, isin TEXT, industry TEXT, layout TEXT, shares DOUBLE, updated TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS filings (url TEXT PRIMARY KEY, symbol TEXT, basis TEXT, period_end DATE, annual BOOLEAN, filed TIMESTAMP,"
    " status TEXT, error TEXT, fetched TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS statement_lines (symbol TEXT, basis TEXT, statement TEXT, period_start DATE, period_end DATE, key TEXT,"
    " value DOUBLE, filing_url TEXT, filed TIMESTAMP, source TEXT)",
    "CREATE TABLE IF NOT EXISTS ingest_runs (symbol TEXT PRIMARY KEY, status TEXT, finished TIMESTAMP, filings INTEGER, error TEXT,"
    " blocked_until TIMESTAMP)",
)
KEY = "symbol = ? AND basis = ? AND statement = ? AND period_start = ? AND period_end = ? AND key = ?"


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FundamentalsStore:
    def __init__(self, path: str | Path = DEFAULT_PATH, read_only: bool = False):
        if str(path) != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            for statement in SCHEMA:
                self._con.execute(statement)

    def __enter__(self) -> "FundamentalsStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    # ---- writes
    def save_company(self, symbol: str, name: str, isin: str, industry: str, layout: str, shares: float | None) -> None:
        self._con.execute("INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?, ?, ?, ?)", [symbol, name, isin, industry, layout, shares, now()])

    def save_statements(self, st: Statements, url: str, filed: datetime, source: str = "nse-xbrl") -> int:
        """Store a filing's rows. A row already held from a filing made later is kept; one from an earlier filing is replaced. Returns the number of rows offered."""
        filed = _utc(filed)
        rows: list[tuple[Any, ...]] = []
        for statement, periods in ((spec.INCOME, st.income), (spec.CASHFLOW, st.cashflow), (spec.BANKING, st.banking)):
            for (start, end), got in periods.items():
                rows += [(st.symbol, st.basis, statement, start, end, key, value) for key, value in got.items()]
        for end, got in st.balance.items():
            rows += [(st.symbol, st.basis, spec.BALANCE, end, end, key, value) for key, value in got.items()]
        if not rows:
            return 0
        self._con.executemany(f"DELETE FROM statement_lines WHERE {KEY} AND filed < ?", [(*row[:6], filed) for row in rows])
        self._con.executemany(
            f"INSERT INTO statement_lines SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM statement_lines WHERE {KEY})",
            [(*row, url, filed, source, *row[:6]) for row in rows],
        )
        return len(rows)

    def record_filing(
        self, url: str, symbol: str, basis: str, period_end: date, annual: bool, filed: datetime, status: str, error: str = ""
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO filings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [url, symbol, basis, period_end, annual, _utc(filed), status, error, now()],
        )

    def set_run(self, symbol: str, status: str, filings: int = 0, error: str = "", blocked_until: datetime | None = None) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO ingest_runs VALUES (?, ?, ?, ?, ?, ?)",
            [symbol, status, now(), filings, error, blocked_until and _utc(blocked_until)],
        )

    # ---- reads
    def filing_status(self, url: str) -> str | None:
        row = self._con.execute("SELECT status FROM filings WHERE url = ?", [url]).fetchone()
        return row[0] if row else None

    def lines(self, symbol: str, basis: str, statement: str) -> dict[tuple[date, date], dict[str, float]]:
        out: dict[tuple[date, date], dict[str, float]] = {}
        for start, end, key, value in self._con.execute(
            "SELECT period_start, period_end, key, value FROM statement_lines WHERE symbol = ? AND basis = ? AND statement = ?"
            " ORDER BY period_end, period_start",
            [symbol, basis, statement],
        ).fetchall():
            out.setdefault((start, end), {})[key] = value
        return out

    def bases(self, symbol: str) -> list[str]:
        return [r[0] for r in self._con.execute("SELECT DISTINCT basis FROM statement_lines WHERE symbol = ? ORDER BY basis", [symbol]).fetchall()]

    def company(self, symbol: str) -> dict[str, Any] | None:
        row = self._con.execute("SELECT symbol, name, isin, industry, layout, shares, updated FROM companies WHERE symbol = ?", [symbol]).fetchone()
        return dict(zip(("symbol", "name", "isin", "industry", "layout", "shares", "updated"), row)) if row else None

    def run(self, symbol: str) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT symbol, status, finished, filings, error, blocked_until FROM ingest_runs WHERE symbol = ?", [symbol]
        ).fetchone()
        return dict(zip(("symbol", "status", "finished", "filings", "error", "blocked_until"), row)) if row else None

    def blocked_until(self) -> datetime | None:
        row = self._con.execute("SELECT max(blocked_until) FROM ingest_runs WHERE status = 'blocked'").fetchone()
        return row[0] if row else None

    def coverage(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT symbol, basis, count(DISTINCT period_end) AS periods, min(period_end), max(period_end) FROM statement_lines"
            " GROUP BY symbol, basis ORDER BY symbol, basis"
        ).fetchall()
        return [dict(zip(("symbol", "basis", "periods", "first", "last"), r)) for r in rows]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_store.py -q`
Expected: `5 passed`. If DuckDB rejects `executemany` with a repeated parameter list in the INSERT, replace that call with a loop of `execute` calls (same SQL); correctness first.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals/store.py tests/test_fund_store.py
git commit -m "feat(fundamentals): the DuckDB store with newest-filing-wins rows and ingest state"
```

---

### Task 5: The universe, in build order

**Files:**
- Create: `src/athena/fundamentals/universe.py`
- Test: `tests/test_fund_universe.py`

**Interfaces:**
- Produces: `Member(symbol, name, industry, isin)`; `parse_index_csv(text) -> list[Member]` (NSE index files: `Company Name,Industry,Symbol,Series,ISIN Code`); `parse_equity_master(text) -> list[Member]` (`SYMBOL,NAME OF COMPANY, SERIES,... ISIN NUMBER`, series EQ, BE, BZ only); `ordered(nifty50, nifty500, equities) -> list[Member]` (Nifty 50, then the rest of Nifty 500, then the rest, no duplicates); `fetch_text(url, get=requests.get) -> str`; `universe(name, get=requests.get) -> list[Member]` where `name` is `nifty50`, `nifty500`, `all` or comma-separated symbols. URLs: `https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv`, `.../ind_nifty500list.csv`, `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv`.

- [ ] **Step 1: Write the failing tests** `tests/test_fund_universe.py`

```python
import pytest

from athena.fundamentals import universe as u

NIFTY50 = "Company Name,Industry,Symbol,Series,ISIN Code\nTata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
NIFTY500 = NIFTY50 + "Zomato Ltd.,Consumer Services,ZOMATO,EQ,INE758T01015\n"
EQUITY = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\n"
    "ZOMATO,Zomato Limited,EQ,23-JUL-2021,1,1,INE758T01015,1\n"
    "TINYCO,Tiny Co Limited,BE,01-JAN-2010,10,1,INE000A01010,10\n"
    "SMECO,Sme Co Limited,SM,01-JAN-2020,10,1,INE000B01010,10\n"
    "BONDX,Bond X,N1,01-JAN-2020,1000,1,INE000C01010,1000\n"
)


def test_index_and_master_files_parse_to_members():
    nifty = u.parse_index_csv(NIFTY50)
    assert [m.symbol for m in nifty] == ["TCS", "HDFCBANK"]
    assert nifty[0].industry == "Information Technology" and nifty[0].isin == "INE467B01029"
    master = u.parse_equity_master(EQUITY)
    assert [m.symbol for m in master] == ["TCS", "ZOMATO", "TINYCO"]  # equity series only: no SME board, no bonds


def test_the_universe_is_ordered_nifty_50_then_nifty_500_then_the_rest_without_repeats():
    order = u.ordered(u.parse_index_csv(NIFTY50), u.parse_index_csv(NIFTY500), u.parse_equity_master(EQUITY))
    assert [m.symbol for m in order] == ["TCS", "HDFCBANK", "ZOMATO", "TINYCO"]
    assert order[0].industry == "Information Technology"  # the index file's industry is kept
    assert order[3].industry == ""  # a name outside the indices has none until its filings say


def test_named_universes_fetch_the_right_files_and_a_symbol_list_needs_no_fetch():
    calls = []

    def get(url, **kwargs):
        calls.append(url)

        class Response:
            status_code = 200
            text = {"nifty50": NIFTY50, "nifty500": NIFTY500, "EQUITY_L": EQUITY}[next(k for k in ("nifty500", "nifty50", "EQUITY_L") if k in url)]

        return Response()

    assert [m.symbol for m in u.universe("nifty50", get)] == ["TCS", "HDFCBANK"]
    assert [m.symbol for m in u.universe("all", get)] == ["TCS", "HDFCBANK", "ZOMATO", "TINYCO"]
    calls.clear()
    assert [m.symbol for m in u.universe("tcs, sbin", get)] == ["TCS", "SBIN"]
    assert calls == []


def test_a_refused_fetch_is_an_error():
    class Refused:
        status_code = 403
        text = ""

    with pytest.raises(RuntimeError):
        u.fetch_text("https://example.test/x.csv", lambda url, **kw: Refused())
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_universe.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/universe.py`

```python
"""Which companies to ingest, in the order that makes the first hour useful: Nifty 50, the rest of Nifty 500, then every other equity-series name."""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass

import requests

from athena.price_integrity.nse_actions import HEADERS, TIMEOUT

NIFTY50_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
EQUITY_SERIES = ("EQ", "BE", "BZ")


@dataclass(frozen=True)
class Member:
    symbol: str
    name: str
    industry: str
    isin: str


def _rows(text: str) -> list[dict[str, str]]:
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def parse_index_csv(text: str) -> list[Member]:
    return [Member(r["Symbol"].upper(), r["Company Name"], r.get("Industry", ""), r.get("ISIN Code", "")) for r in _rows(text) if r.get("Symbol")]


def parse_equity_master(text: str) -> list[Member]:
    return [
        Member(r["SYMBOL"].upper(), r.get("NAME OF COMPANY", ""), "", r.get("ISIN NUMBER", ""))
        for r in _rows(text)
        if r.get("SYMBOL") and r.get("SERIES") in EQUITY_SERIES
    ]


def ordered(nifty50: list[Member], nifty500: list[Member], equities: list[Member]) -> list[Member]:
    seen: set[str] = set()
    out: list[Member] = []
    for group in (nifty50, nifty500, equities):
        for member in group:
            if member.symbol not in seen:
                seen.add(member.symbol)
                out.append(member)
    return out


def fetch_text(url: str, get: Callable = requests.get) -> str:
    response = get(url, headers=HEADERS, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"{url} answered HTTP {response.status_code}")
    return response.text


def universe(name: str, get: Callable = requests.get) -> list[Member]:
    """`nifty50`, `nifty500`, `all`, or a comma-separated list of symbols."""
    key = name.strip().lower()
    if key == "nifty50":
        return parse_index_csv(fetch_text(NIFTY50_URL, get))
    if key == "nifty500":
        return ordered(parse_index_csv(fetch_text(NIFTY50_URL, get)), parse_index_csv(fetch_text(NIFTY500_URL, get)), [])
    if key == "all":
        return ordered(
            parse_index_csv(fetch_text(NIFTY50_URL, get)), parse_index_csv(fetch_text(NIFTY500_URL, get)), parse_equity_master(fetch_text(EQUITY_URL, get))
        )
    return [Member(s.strip().upper(), "", "", "") for s in name.split(",") if s.strip()]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_universe.py -q`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals/universe.py tests/test_fund_universe.py
git commit -m "feat(fundamentals): the universe lists, Nifty 50 then Nifty 500 then the rest"
```

---

### Task 6: The resumable ingest

**Files:**
- Create: `src/athena/fundamentals/ingest.py`
- Test: `tests/test_fund_ingest.py`

**Interfaces:**
- Consumes: `NseResults` (`filings(symbol) -> list[FilingRef]`, `text(ref) -> str`), `FilingRef`, `CACHE_DIR` from `athena.pit.nse_results`; `parse_rows`; `FundamentalsStore`; `Member`.
- Produces: `Report(symbol, fetched, skipped, failed, notes)`; `Blocked(Exception)`; `wanted(refs, quarters=13, standalone=False) -> list[FilingRef]`; `ingest_symbol(member, results, store_path, *, quarters=13, standalone=False, cache_dir=CACHE_DIR) -> Report`; `run(members, results, store_path, *, resume, max_age_days, limit, quarters, standalone, out) -> int` (0 done, 3 blocked); `main(argv) -> int` with flags `--universe`, `--resume`, `--limit`, `--quarters`, `--standalone`, `--store`, `--max-age-days`.

- [ ] **Step 1: Write the failing tests** `tests/test_fund_ingest.py`

```python
import calendar
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.contracts import AthenaError
from athena.fundamentals import ingest
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member
from athena.pit.nse_results import FilingRef

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
IST = timezone(timedelta(hours=5, minutes=30))
TCS = Member("TCS", "Tata Consultancy Services", "Information Technology", "INE467B01029")


def ref(end, annual, consolidated=True, filed=None, url=None):
    return FilingRef("TCS", end, filed or datetime(end.year, end.month, 28, 12, 0, tzinfo=IST), consolidated, annual, url or f"u-{end}-{annual}-{consolidated}")


def quarter_ends(n):
    year, month, out = 2025, 3, []
    for _ in range(n):
        out.append(date(year, month, calendar.monthrange(year, month)[1]))
        month -= 3
        if month < 1:
            month, year = month + 12, year - 1
    return out


class FakeResults:
    def __init__(self, refs, texts):
        self.refs, self.texts, self.fetched = refs, texts, []

    def filings(self, symbol):
        value = self.refs
        if isinstance(value, Exception):
            raise value
        return list(value)

    def text(self, r):
        self.fetched.append(r.url)
        value = self.texts[r.url]
        if isinstance(value, Exception):
            raise value
        return value


def test_wanted_takes_consolidated_annuals_and_the_latest_quarters_newest_first():
    refs = [ref(date(y, 3, 31), True) for y in (2022, 2023, 2024)] + [ref(e, False) for e in quarter_ends(15)] + [ref(date(2024, 3, 31), True, False)]
    chosen = ingest.wanted(refs, quarters=13)
    assert len(chosen) == 3 + 13
    assert all(r.consolidated for r in chosen)
    assert [r.period_end for r in chosen] == sorted((r.period_end for r in chosen), reverse=True)
    assert len(ingest.wanted(refs, quarters=13, standalone=True)) > len(chosen)


def test_a_company_with_no_consolidated_filings_falls_back_to_standalone():
    refs = [ref(date(2024, 3, 31), True, False), ref(date(2023, 3, 31), True, False)]
    assert len(ingest.wanted(refs)) == 2


def test_a_revised_filing_for_the_same_period_wins_over_the_original():
    original = ref(date(2024, 3, 31), True, filed=datetime(2024, 4, 10, tzinfo=IST), url="orig")
    revised = ref(date(2024, 3, 31), True, filed=datetime(2024, 5, 10, tzinfo=IST), url="rev")
    assert [r.url for r in ingest.wanted([original, revised])] == ["rev"]


def test_ingest_saves_a_filing_and_a_second_run_does_not_fetch_it_again(tmp_path):
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    filing = ref(date(2024, 3, 31), True, url="tcs24")
    results = FakeResults([filing], {"tcs24": text})
    path = tmp_path / "f.duckdb"
    report = ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    assert (report.fetched, report.failed) == (1, 0)
    with FundamentalsStore(path) as store:
        assert store.filing_status("tcs24") == "ok"
        assert store.company("TCS")["layout"] == "indas"
        assert store.lines("TCS", "consolidated", "income")
    again = ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    assert (again.fetched, again.skipped) == (0, 1)
    assert results.fetched == ["tcs24"]


def test_an_unreadable_filing_is_recorded_and_the_rest_go_on(tmp_path):
    good = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="good"), ref(date(2023, 3, 31), True, url="bad")], {"good": good, "bad": "<html>error</html>"})
    report = ingest.ingest_symbol(TCS, results, tmp_path / "f.duckdb", cache_dir=tmp_path / "cache")
    assert (report.fetched, report.failed) == (1, 1)
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        assert store.filing_status("bad") == "failed"


def test_a_refusal_from_the_exchange_stops_the_run_and_records_the_block(tmp_path):
    results = FakeResults([ref(date(2024, 3, 31), True, url="u")], {"u": AthenaError("HTTP 403")})
    path = tmp_path / "f.duckdb"
    with pytest.raises(ingest.Blocked):
        ingest.ingest_symbol(TCS, results, path, cache_dir=tmp_path / "cache")
    out = io.StringIO()
    code = ingest.run([TCS], results, path, resume=False, max_age_days=30, limit=None, quarters=13, standalone=False, out=out)
    assert code == 3
    with FundamentalsStore(path) as store:
        assert store.run("TCS")["status"] == "blocked"
        assert store.blocked_until() > datetime.now(timezone.utc).replace(tzinfo=None)
    # while the block lasts, a new run refuses to start
    assert ingest.run([TCS], results, path, resume=True, max_age_days=30, limit=None, quarters=13, standalone=False, out=io.StringIO()) == 3


def test_resume_skips_a_company_finished_recently(tmp_path):
    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    results = FakeResults([ref(date(2024, 3, 31), True, url="tcs24")], {"tcs24": text})
    path = tmp_path / "f.duckdb"
    args = dict(resume=True, max_age_days=30, limit=None, quarters=13, standalone=False)
    assert ingest.run([TCS], results, path, out=io.StringIO(), **args) == 0
    listed = []
    results.filings = lambda symbol: listed.append(symbol) or []  # a resumed run must not even ask for the list
    out = io.StringIO()
    assert ingest.run([TCS], results, path, out=out, **args) == 0
    assert listed == [] and "skipped" in out.getvalue()
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_ingest.py -q`
Expected: FAIL, `ImportError: cannot import name 'ingest'`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/ingest.py`

```python
"""Fetch each company's results filings from NSE and keep their statements.

    python -m athena.fundamentals.ingest --universe nifty50 --resume
    python -m athena.fundamentals.ingest --universe TCS,SBIN --standalone

Resumable and gentle: it goes through `NseResults` (1.5 s between requests, back-off when the exchange refuses), keeps every downloaded filing on
disk, skips filings already stored and companies finished recently (`--resume`), and stops cleanly when the exchange blocks it, recording when it may
try again. It is started by hand; nothing schedules it. Default scope per company: consolidated filings (standalone only when there are none, or
with `--standalone`), every annual filing and the latest 13 quarterly ones, newest first."""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from athena.contracts import AthenaError
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, now
from athena.fundamentals.universe import Member, universe
from athena.pit.nse_results import CACHE_DIR, FilingRef, NseResults

BLOCK_MARKERS = ("HTTP 403", "HTTP 429", "HTTP 503")
BLOCK_PAUSE = timedelta(minutes=30)


class Blocked(Exception):
    """The exchange refused after the client's own retries: stop and let it cool down."""


@dataclass
class Report:
    symbol: str
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)


def _blocked(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in BLOCK_MARKERS)


def wanted(refs: Sequence[FilingRef], quarters: int = 13, standalone: bool = False) -> list[FilingRef]:
    """The filings worth fetching, newest first: consolidated ones (every annual, the latest `quarters` quarterly), or standalone ones when the
    company has no consolidated filing or `standalone` is set. For one period and basis only the latest filing is kept (a revision replaces its original)."""
    latest: dict[tuple[Any, ...], FilingRef] = {}
    for r in refs:
        key = (r.period_end, r.annual, r.consolidated)
        if key not in latest or r.filed > latest[key].filed:
            latest[key] = r
    pool = list(latest.values())
    consolidated = [r for r in pool if r.consolidated]
    use = pool if standalone or not consolidated else consolidated
    annual = [r for r in use if r.annual]
    quarterly = sorted((r for r in use if not r.annual), key=lambda r: r.period_end, reverse=True)[:quarters]
    return sorted(annual + quarterly, key=lambda r: r.period_end, reverse=True)


def _text(results: Any, ref: FilingRef, cache_dir: Path) -> str:
    path = cache_dir / (hashlib.sha1(ref.url.encode()).hexdigest()[:20] + ".xml")  # the same cache the point-in-time code uses
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = results.text(ref)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def ingest_symbol(
    member: Member, results: Any, store_path: str | Path, *, quarters: int = 13, standalone: bool = False, cache_dir: Path = CACHE_DIR
) -> Report:
    """Fetch and store one company's filings. Raises `Blocked` when the exchange refuses; any other failure is counted and the run goes on."""
    report = Report(member.symbol)
    try:
        refs = results.filings(member.symbol)
    except AthenaError as exc:
        if _blocked(exc):
            raise Blocked(str(exc)) from exc
        report.notes.append(str(exc))
        report.failed += 1
        return report
    for ref in wanted(refs, quarters, standalone):
        basis = "consolidated" if ref.consolidated else "standalone"
        with FundamentalsStore(store_path) as store:
            if store.filing_status(ref.url) == "ok":
                report.skipped += 1
                continue
        try:
            text = _text(results, ref, cache_dir)
        except AthenaError as exc:
            if _blocked(exc):
                raise Blocked(str(exc)) from exc
            report.failed += 1
            report.notes.append(f"{ref.period_end}: {exc}")
            continue
        try:
            statements = parse_rows(text)
        except ValueError as exc:
            with FundamentalsStore(store_path) as store:
                store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "failed", str(exc)[:200])
            report.failed += 1
            report.notes.append(f"{ref.period_end}: {exc}")
            continue
        with FundamentalsStore(store_path) as store:
            store.save_statements(statements, ref.url, ref.filed)
            store.record_filing(ref.url, member.symbol, basis, ref.period_end, ref.annual, ref.filed, "ok")
            store.save_company(member.symbol, member.name, member.isin, member.industry, statements.layout, statements.shares)
        report.fetched += 1
        report.notes.extend(statements.notes)
    return report


def run(
    members: Sequence[Member], results: Any, store_path: str | Path, *, resume: bool, max_age_days: int, limit: int | None,
    quarters: int, standalone: bool, out: TextIO = sys.stdout,
) -> int:
    """Ingest each member in turn. Returns 0 when done, 3 when the exchange blocked it (or a block is still running)."""
    with FundamentalsStore(store_path) as store:
        until = store.blocked_until()
    if until and until > now():
        print(f"blocked by the exchange until {until:%H:%M} UTC; not starting", file=out)
        return 3
    done = 0
    for member in members:
        if limit is not None and done >= limit:
            break
        with FundamentalsStore(store_path) as store:
            state = store.run(member.symbol)
        if resume and state and state["status"] == "ok" and state["finished"] > now() - timedelta(days=max_age_days):
            print(f"{member.symbol:<12} skipped (finished {state['finished']:%Y-%m-%d})", file=out)
            continue
        started = now()
        try:
            report = ingest_symbol(member, results, store_path, quarters=quarters, standalone=standalone)
        except Blocked as exc:
            with FundamentalsStore(store_path) as store:
                store.set_run(member.symbol, "blocked", error=str(exc)[:200], blocked_until=now() + BLOCK_PAUSE)
            print(f"{member.symbol:<12} BLOCKED ({exc}); stopping, try again after {BLOCK_PAUSE.seconds // 60} minutes", file=out)
            return 3
        with FundamentalsStore(store_path) as store:
            store.set_run(member.symbol, "ok" if report.failed == 0 or report.fetched + report.skipped else "failed", filings=report.fetched + report.skipped)
        seconds = (now() - started).total_seconds()
        print(f"{member.symbol:<12} fetched {report.fetched:>3} skipped {report.skipped:>3} failed {report.failed:>3}  {seconds:5.0f}s", file=out)
        for note in report.notes[:3]:
            print(f"{'':<12} note: {note}", file=out)
        done += 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50", help="nifty50, nifty500, all, or SYMBOL,SYMBOL")
    parser.add_argument("--resume", action="store_true", help="skip companies finished within --max-age-days")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many companies")
    parser.add_argument("--quarters", type=int, default=13)
    parser.add_argument("--standalone", action="store_true", help="also fetch standalone filings")
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    members = universe(args.universe)
    print(f"{len(members)} companies in {args.universe}", flush=True)
    return run(
        members, NseResults(), args.store, resume=args.resume, max_age_days=args.max_age_days, limit=args.limit, quarters=args.quarters,
        standalone=args.standalone,
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_ingest.py -q`
Expected: `7 passed`. The block test relies on `Blocked` propagating from `ingest_symbol` and `run` recording `blocked_until` 30 minutes ahead.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals/ingest.py tests/test_fund_ingest.py
git commit -m "feat(fundamentals): resumable ingest through the throttled NSE client, stopping cleanly on a block"
```

---

### Task 7: The ratio engine

**Files:**
- Create: `src/athena/fundamentals/ratios.py`
- Test: `tests/test_fund_ratios.py`

**Interfaces:**
- Consumes: the row dictionaries of `Statements` / `store.lines` (`income` keyed `(start, end)`, `balance` keyed by date or `(end, end)`).
- Produces: `annual(income) -> dict[date, dict]`, `quarterly(income) -> dict[date, dict]`, `ttm(quarters, key) -> float | None`, `cagr(latest, earlier, years) -> float | None`, `compounded(annual_map, key, years) -> float | None`, `year_ratios(layout, income, balance, cashflow) -> dict[str, float]`, `market_ratios(price, shares, quarters, fy, balance) -> dict[str, float | str]`, `crore(x) -> float`. Each formula is in its docstring and is shown on the page. All inputs and outputs in rupees except percentages and days.

Definitions used (stated so a difference from Screener is explainable): EBIT = profit before tax + interest; capital employed = total assets − current liabilities (closing); ROCE = EBIT / capital employed; ROE = net profit / (equity capital + reserves), closing; debtor days = trade receivables / sales × 365; inventory days = inventories / sales × 365; days payable = trade payables / sales × 365; cash conversion cycle = debtor + inventory − payable days; working-capital days = (current assets − current liabilities) / sales × 365; CFO to EBITDA = operating cash flow / operating profit; market cap = price × shares; P/E = market cap / TTM net profit (latest year's when fewer than four quarters); book value = (equity capital + reserves) / shares; EV = market cap + borrowings − cash − other bank balances; EV/EBITDA = EV / TTM operating profit.

- [ ] **Step 1: Write the failing tests** `tests/test_fund_ratios.py`

```python
from datetime import date

import pytest

from athena.fundamentals import ratios as r


def fy(year):
    return (date(year - 1, 4, 1), date(year, 3, 31))


def quarter(end):
    return (date(end.year, end.month - 2, 1), end)


def test_periods_are_sorted_into_years_and_quarters_by_length():
    income = {fy(2024): {"sales": 1.0}, quarter(date(2024, 3, 31)): {"sales": 2.0}, (date(2023, 4, 1), date(2023, 9, 30)): {"sales": 3.0}}
    assert list(r.annual(income)) == [date(2024, 3, 31)]
    assert list(r.quarterly(income)) == [date(2024, 3, 31)]  # the half year is neither


def test_ttm_sums_four_consecutive_quarters_and_refuses_gaps():
    ends = [date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30)]
    quarters = {e: {"net_profit": 25.0} for e in ends}
    assert r.ttm(quarters, "net_profit") == 100.0
    assert r.ttm({e: quarters[e] for e in ends[:3]}, "net_profit") is None
    gappy = {**{e: {"net_profit": 25.0} for e in ends[:3]}, date(2023, 3, 31): {"net_profit": 25.0}, date(2022, 12, 31): {"net_profit": 25.0}}
    assert r.ttm({date(2024, 3, 31): {"net_profit": 1.0}, date(2023, 12, 31): {"net_profit": 1.0}, date(2023, 6, 30): {"net_profit": 1.0}, date(2023, 3, 31): {"net_profit": 1.0}}, "net_profit") is None
    assert r.ttm(gappy, "missing") is None


def test_compound_growth():
    assert r.cagr(200.0, 100.0, 5) == pytest.approx(14.8698, abs=1e-3)
    assert r.cagr(100.0, 0.0, 5) is None and r.cagr(-5.0, 100.0, 5) is None and r.cagr(None, 100.0, 5) is None
    annual = {date(2024, 3, 31): {"sales": 200.0}, date(2021, 3, 31): {"sales": 100.0}, date(2019, 3, 31): {"sales": 50.0}}
    assert r.compounded(annual, "sales", 3) == pytest.approx(r.cagr(200.0, 100.0, 3))
    assert r.compounded(annual, "sales", 5) == pytest.approx(r.cagr(200.0, 50.0, 5))
    assert r.compounded(annual, "sales", 10) is None  # not enough history is not a zero


INCOME = {"sales": 1000.0, "pbt": 200.0, "interest": 20.0, "net_profit": 150.0, "operating_profit": 260.0}
BALANCE = {
    "equity_capital": 100.0, "reserves": 400.0, "borrowings": 250.0, "total_assets": 1000.0, "current_liabilities": 300.0, "current_assets": 500.0,
    "receivables": 100.0, "inventory": 50.0, "payables": 80.0,
}
CASH = {"cfo": 220.0, "capex": 60.0, "fcf": 160.0}


def test_industrial_year_ratios():
    out = r.year_ratios("indas", INCOME, BALANCE, CASH)
    assert out["ebit"] == 220.0
    assert out["roce"] == pytest.approx(220 / 700 * 100)
    assert out["roe"] == pytest.approx(30.0)
    assert out["interest_cover"] == pytest.approx(11.0)
    assert out["debt_to_equity"] == pytest.approx(0.5)
    assert out["debtor_days"] == pytest.approx(36.5)
    assert out["inventory_days"] == pytest.approx(18.25)
    assert out["days_payable"] == pytest.approx(29.2)
    assert out["cash_conversion_cycle"] == pytest.approx(36.5 + 18.25 - 29.2)
    assert out["working_capital_days"] == pytest.approx(73.0)
    assert out["fcf"] == 160.0
    assert out["cfo_to_ebitda"] == pytest.approx(220 / 260 * 100)


def test_a_ratio_with_a_missing_input_is_absent_and_other_layouts_skip_what_does_not_apply():
    thin = r.year_ratios("indas", {"sales": 1000.0, "pbt": 200.0}, {"total_assets": 1000.0}, {})
    assert "roce" not in thin and "debtor_days" not in thin and thin["ebit"] == 200.0
    bank = r.year_ratios("bank", {"net_profit": 150.0}, {"equity_capital": 100.0, "reserves": 400.0, "advances": 800.0, "deposits": 1000.0}, {})
    assert bank == {"roe": pytest.approx(30.0), "credit_deposit": pytest.approx(80.0)}
    lender = r.year_ratios("finance", INCOME, BALANCE, CASH)
    assert "roce" not in lender and "debtor_days" not in lender and lender["roe"] == pytest.approx(30.0)


def test_market_ratios_use_ttm_earnings_when_four_quarters_exist():
    ends = [date(2024, 3, 31), date(2023, 12, 31), date(2023, 9, 30), date(2023, 6, 30)]
    quarters = {e: {"net_profit": 25.0, "operating_profit": 40.0} for e in ends}
    balance = {"equity_capital": 100.0, "reserves": 400.0, "borrowings": 250.0, "cash": 30.0, "bank_balances": 20.0}
    out = r.market_ratios(100.0, 10.0, quarters, {}, balance)
    assert out["market_cap"] == 1000.0
    assert out["pe"] == pytest.approx(10.0) and out["pe_period"] == "TTM"
    assert out["book_value"] == pytest.approx(50.0) and out["pb"] == pytest.approx(2.0)
    assert out["ev"] == pytest.approx(1200.0) and out["ev_ebitda"] == pytest.approx(7.5)


def test_market_ratios_fall_back_to_the_latest_year_and_say_so():
    fy_income = {date(2024, 3, 31): {"net_profit": 80.0, "operating_profit": 100.0}}
    out = r.market_ratios(100.0, 10.0, {}, fy_income, {"equity_capital": 100.0, "reserves": 400.0})
    assert out["pe"] == pytest.approx(12.5) and out["pe_period"] == "FY2024"
    assert "ev" not in out or out["ev"] > 0
    assert r.market_ratios(100.0, None, {}, fy_income, {}) == {}


def test_crore():
    assert r.crore(2408930000000.0) == 240893.0
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_ratios.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Write the implementation** `src/athena/fundamentals/ratios.py`

```python
"""Ratios and growth from the stored statements. Pure functions over plain dictionaries, all in rupees (percentages and days excepted). Each formula
is in its docstring and is shown beside the number on the page, so a difference from another site is explainable.

Period dictionaries are keyed `(start, end)` as in `Statements`; `annual` and `quarterly` re-key them by end date."""
from __future__ import annotations

from datetime import date
from typing import Any

DAYS = 365
CRORE = 10_000_000


def crore(value: float) -> float:
    return value / CRORE


def _length(period: tuple[date, date]) -> int:
    return (period[1] - period[0]).days


def annual(income: dict[tuple[date, date], dict[str, float]]) -> dict[date, dict[str, float]]:
    """Financial years (periods of 330 to 380 days), by year end."""
    return {p[1]: got for p, got in income.items() if 330 <= _length(p) <= 380}


def quarterly(income: dict[tuple[date, date], dict[str, float]]) -> dict[date, dict[str, float]]:
    """Quarters (periods of 80 to 100 days), by quarter end."""
    return {p[1]: got for p, got in income.items() if 80 <= _length(p) <= 100}


def ttm(quarters: dict[date, dict[str, float]], key: str) -> float | None:
    """Trailing twelve months: the sum of the latest four quarters, which must be consecutive (each 80 to 100 days after the one before)."""
    ends = sorted(quarters, reverse=True)[:4]
    if len(ends) < 4 or any(not 80 <= (a - b).days <= 100 for a, b in zip(ends, ends[1:])):
        return None
    values = [quarters[e].get(key) for e in ends]
    return None if None in values else float(sum(values))


def cagr(latest: float | None, earlier: float | None, years: int) -> float | None:
    """Compound annual growth, in percent: (latest / earlier) ^ (1 / years) - 1. None when either value is missing or not positive."""
    if latest is None or earlier is None or earlier <= 0 or latest <= 0:
        return None
    return ((latest / earlier) ** (1 / years) - 1) * 100


def compounded(annual_map: dict[date, dict[str, float]], key: str, years: int) -> float | None:
    """CAGR of `key` over `years` financial years ending at the latest year held; None when the year `years` earlier is not held."""
    ends = sorted(annual_map)
    if not ends:
        return None
    last = ends[-1]
    prior = next((e for e in ends if e.year == last.year - years and e.month == last.month), None)
    if prior is None:
        return None
    return cagr(annual_map[last].get(key), annual_map[prior].get(key), years)


def year_ratios(layout: str, income: dict[str, float], balance: dict[str, float], cashflow: dict[str, float]) -> dict[str, float]:
    """The ratios of one financial year from its income, closing balance sheet and cash flow.

    EBIT = profit before tax + interest. ROCE = EBIT / (total assets - current liabilities). ROE = net profit / (equity capital + reserves).
    Interest cover = EBIT / interest. Debt to equity = borrowings / (equity capital + reserves). Debtor, inventory and payable days = trade
    receivables, inventories, trade payables / sales x 365. Cash conversion cycle = debtor + inventory - payable days. Working-capital days =
    (current assets - current liabilities) / sales x 365. CFO to EBITDA = operating cash flow / operating profit. Banks: ROE and advances / deposits
    only; lenders: ROE and debt to equity only (working-capital measures do not describe them)."""
    out: dict[str, float] = {}
    equity = balance["equity_capital"] + balance["reserves"] if "equity_capital" in balance and "reserves" in balance else None
    net_profit, sales, pbt, interest = income.get("net_profit"), income.get("sales"), income.get("pbt"), income.get("interest")
    if equity and net_profit is not None:
        out["roe"] = net_profit / equity * 100
    if layout == "bank":
        if balance.get("advances") and balance.get("deposits"):
            out["credit_deposit"] = balance["advances"] / balance["deposits"] * 100
        return out
    if layout == "finance":
        if equity and balance.get("borrowings") is not None:
            out["debt_to_equity"] = balance["borrowings"] / equity
        return out
    ebit = pbt + (interest or 0.0) if pbt is not None else None
    if ebit is not None:
        out["ebit"] = ebit
    if "total_assets" in balance and "current_liabilities" in balance and ebit is not None:
        capital_employed = balance["total_assets"] - balance["current_liabilities"]
        if capital_employed:
            out["roce"] = ebit / capital_employed * 100
    if ebit is not None and interest:
        out["interest_cover"] = ebit / interest
    if equity and balance.get("borrowings") is not None:
        out["debt_to_equity"] = balance["borrowings"] / equity
    if sales:
        for key, source in (("debtor_days", "receivables"), ("inventory_days", "inventory"), ("days_payable", "payables")):
            if balance.get(source) is not None:
                out[key] = balance[source] / sales * DAYS
        if {"debtor_days", "inventory_days", "days_payable"} <= out.keys():
            out["cash_conversion_cycle"] = out["debtor_days"] + out["inventory_days"] - out["days_payable"]
        if balance.get("current_assets") is not None and balance.get("current_liabilities") is not None:
            out["working_capital_days"] = (balance["current_assets"] - balance["current_liabilities"]) / sales * DAYS
    if "fcf" in cashflow:
        out["fcf"] = cashflow["fcf"]
    if cashflow.get("cfo") is not None and income.get("operating_profit"):
        out["cfo_to_ebitda"] = cashflow["cfo"] / income["operating_profit"] * 100
    return out


def market_ratios(
    price: float, shares: float | None, quarters: dict[date, dict[str, float]], fy_income: dict[date, dict[str, float]], balance: dict[str, float]
) -> dict[str, Any]:
    """The price-dependent figures. Market cap = price x shares. P/E = market cap / TTM net profit (the latest year's when four consecutive quarters
    are not held; `pe_period` says which). Book value = (equity capital + reserves) / shares; P/B = price / book value. EV = market cap + borrowings -
    cash - other bank balances. EV/EBITDA = EV / TTM operating profit (the latest year's when there is no TTM). Empty when the share count is unknown."""
    if not shares:
        return {}
    out: dict[str, Any] = {"market_cap": price * shares}
    latest = max(fy_income) if fy_income else None
    profit, profit_period = ttm(quarters, "net_profit"), "TTM"
    operating = ttm(quarters, "operating_profit")
    if profit is None and latest is not None:
        profit, profit_period = fy_income[latest].get("net_profit"), f"FY{latest.year}"
    if operating is None and latest is not None:
        operating = fy_income[latest].get("operating_profit")
    if profit and profit > 0:
        out["pe"], out["pe_period"] = out["market_cap"] / profit, profit_period
    if "equity_capital" in balance and "reserves" in balance:
        out["book_value"] = (balance["equity_capital"] + balance["reserves"]) / shares
        out["pb"] = price / out["book_value"] if out["book_value"] > 0 else None
        if out["pb"] is None:
            del out["pb"]
    if "borrowings" in balance:
        out["ev"] = out["market_cap"] + balance["borrowings"] - balance.get("cash", 0.0) - balance.get("bank_balances", 0.0)
        if operating and operating > 0:
            out["ev_ebitda"] = out["ev"] / operating
    return out
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_ratios.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/athena/fundamentals/ratios.py tests/test_fund_ratios.py
git commit -m "feat(fundamentals): the ratio engine with every formula written out"
```

---

### Task 8: Show a stored company, and prove it on real filings

**Files:**
- Create: `src/athena/fundamentals/show.py`, `scripts/fund_crosscheck.py`
- Test: `tests/test_fund_show.py`

**Interfaces:**
- Produces: `render(store, symbol, basis) -> str` (annual income, balance sheet and cash flow in crores, newest year last, Screener's row order, with the layout's own row set), CLI `python -m athena.fundamentals.show SYMBOL [--basis consolidated|standalone]`.

- [ ] **Step 1: Write the failing test** `tests/test_fund_show.py`

```python
from datetime import datetime, timezone
from pathlib import Path

from athena.fundamentals import show
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def test_a_stored_company_renders_in_crores_with_screener_row_names():
    with FundamentalsStore(":memory:") as store:
        store.save_statements(parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")), "u", datetime(2024, 4, 12, tzinfo=timezone.utc))
        text = show.render(store, "TCS", "consolidated")
    assert "Profit & Loss" in text and "Balance Sheet" in text and "Cash Flows" in text
    assert "Sales" in text and "240,893" in text  # 2,408,930,000,000 rupees
    assert "Mar 2024" in text
    assert "Total Assets" in text and "146,449" in text


def test_a_company_with_no_rows_says_so():
    with FundamentalsStore(":memory:") as store:
        assert "no statements" in show.render(store, "NOPE", "consolidated").lower()
```

- [ ] **Step 2: Run it and see it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_show.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Write** `src/athena/fundamentals/show.py`

```python
"""A stored company as text tables, the way Screener lays them out: annual columns, newest last, rows in its order, figures in crores.

    python -m athena.fundamentals.show TCS --basis consolidated"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date

from athena.fundamentals import ratios
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore

INCOME_ROWS = (
    ("sales", "Sales"), ("expenses", "Expenses"), ("operating_profit", "Operating Profit"), ("opm", "OPM %"), ("other_income", "Other Income"),
    ("interest", "Interest"), ("depreciation", "Depreciation"), ("pbt", "Profit before tax"), ("tax_pct", "Tax %"), ("net_profit", "Net Profit"),
    ("eps", "EPS in Rs"),
)
BALANCE_ROWS = (
    ("equity_capital", "Equity Capital"), ("reserves", "Reserves"), ("deposits", "Deposits"), ("borrowings", "Borrowings"),
    ("other_liabilities", "Other Liabilities"), ("total_assets", "Total Assets"), ("fixed_assets", "Fixed Assets"), ("cwip", "CWIP"),
    ("investments", "Investments"), ("advances", "Advances"), ("loans", "Loans"), ("other_assets", "Other Assets"),
)
CASH_ROWS = (
    ("cfo", "Cash from Operating Activity"), ("cfi", "Cash from Investing Activity"), ("cff", "Cash from Financing Activity"),
    ("net_cash", "Net Cash Flow"), ("fcf", "Free Cash Flow"),
)
PLAIN = {"opm", "tax_pct", "eps"}  # percentages and per-share figures are not rupees


def _cell(key: str, value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}" if key in PLAIN else f"{ratios.crore(value):,.0f}"


def _table(title: str, by_end: dict[date, dict[str, float]], rows: Sequence[tuple[str, str]]) -> list[str]:
    ends = sorted(by_end)
    present = [(key, label) for key, label in rows if any(key in by_end[e] for e in ends)]
    if not ends or not present:
        return [f"{title}: not available", ""]
    head = f"{title}  (Rs crore)"
    lines = [head, f"{'':<32}" + "".join(f"{e:%b %Y}".rjust(12) for e in ends)]
    for key, label in present:
        lines.append(f"{label:<32}" + "".join(_cell(key, by_end[e].get(key)).rjust(12) for e in ends))
    return [*lines, ""]


def render(store: FundamentalsStore, symbol: str, basis: str) -> str:
    income = store.lines(symbol, basis, "income")
    if not income and not store.lines(symbol, basis, "balance"):
        return f"{symbol} ({basis}): no statements stored"
    balance = {end: got for (_, end), got in store.lines(symbol, basis, "balance").items()}
    cash = {p[1]: got for p, got in store.lines(symbol, basis, "cashflow").items() if 330 <= (p[1] - p[0]).days <= 380}
    company = store.company(symbol) or {}
    out = [f"{symbol}  {company.get('name', '')}  [{basis}, layout {company.get('layout', '?')}]", ""]
    out += _table("Profit & Loss", ratios.annual(income), INCOME_ROWS)
    out += _table("Balance Sheet", balance, BALANCE_ROWS)
    out += _table("Cash Flows", cash, CASH_ROWS)
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with FundamentalsStore(args.store, read_only=True) as store:
        print(render(store, args.symbol.upper(), args.basis))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fund_show.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Write the cross-check script** `scripts/fund_crosscheck.py`

```python
"""Compare stored annual figures with Yahoo's for the same companies and years: evidence, not a test (it needs the network).

    python scripts/fund_crosscheck.py TCS SBIN RELIANCE
Prints, per company and year, our figure over Yahoo's for revenue and net profit; a ratio outside 0.99 to 1.01 is flagged. Banks are compared on net
profit only (Yahoo's revenue for a bank is a different construct from interest earned)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import yfinance  # noqa: E402

from athena.fundamentals import ratios  # noqa: E402
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore  # noqa: E402


def main(symbols: list[str]) -> int:
    flagged = 0
    with FundamentalsStore(DEFAULT_PATH, read_only=True) as store:
        for symbol in symbols:
            company = store.company(symbol) or {}
            ours = ratios.annual(store.lines(symbol, "consolidated", "income") or store.lines(symbol, "standalone", "income"))
            frame = yfinance.Ticker(f"{symbol}.NS").financials
            print(f"{symbol} (layout {company.get('layout', '?')})")
            for column in frame.columns:
                end = column.date()
                mine = ours.get(end)
                if not mine:
                    continue
                for key, row in (("sales", "Total Revenue"), ("net_profit", "Net Income")):
                    if key == "sales" and company.get("layout") == "bank":
                        continue
                    theirs = frame.loc[row, column] if row in frame.index else None
                    if mine.get(key) is None or theirs is None or theirs != theirs:
                        continue
                    ratio = mine[key] / theirs
                    flag = "" if 0.99 <= ratio <= 1.01 else "   <-- check"
                    flagged += bool(flag)
                    print(f"  {end}  {key:<11} ours {ratios.crore(mine[key]):>12,.0f} cr  yahoo {ratios.crore(theirs):>12,.0f} cr  ratio {ratio:.4f}{flag}")
    print(f"{flagged} figure(s) outside 1% of Yahoo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 6: Run the proof on real data** (needs the network; about 3 minutes for five companies at NSE's throttle; run in the background if the tool allows, and do not rerun it while a run holds the file)

```bash
.venv/Scripts/python.exe -m athena.fundamentals.ingest --universe TCS,HDFCBANK,BAJFINANCE,RELIANCE,SBIN --resume
.venv/Scripts/python.exe -m athena.fundamentals.show TCS
.venv/Scripts/python.exe -m athena.fundamentals.show HDFCBANK
.venv/Scripts/python.exe scripts/fund_crosscheck.py TCS HDFCBANK BAJFINANCE RELIANCE SBIN
```
Expected acceptance: each company ends `fetched >= 15`; TCS shows income for FY2019 to the latest year and a balance sheet and cash flow from FY2023, with FY2019 to FY2022 balance-sheet columns **absent (not zero)**; the cross-check flags no more than one figure per company, and every flagged figure is explained in the commit message (restatement, standalone vs consolidated, a lender's revenue definition). A mismatch with no explanation is a mapping bug: fix `lines.py`, rerun Task 2's generator if a tag moved, and add the case to `tests/test_fund_rows.py`.

- [ ] **Step 7: Commit**

```bash
git add src/athena/fundamentals/show.py scripts/fund_crosscheck.py tests/test_fund_show.py
git commit -m "feat(fundamentals): text tables of a stored company and a cross-check against Yahoo, proven on five real companies"
```

---

### Task 9: Run the Nifty 50, document, rebuild the graph

**Files:**
- Modify: `TRD.md` (section 2.20 gets a Fundamentals subsection), `docs/superpowers/specs/2026-10-17-parity-master-design.md` (result notes), `docs/superpowers/specs/2026-10-17-screener-breakdown.md` (a correction note: balance sheet depth)

- [ ] **Step 1: Run the Nifty 50 in the background** (about 30 minutes; the file is written in bursts, so leave it alone until it ends)

```bash
.venv/Scripts/python.exe -m athena.fundamentals.ingest --universe nifty50 --resume
```
Expected: 50 lines, each with `fetched` or `skipped`, exit code 0. On `BLOCKED`, wait the printed minutes and rerun with `--resume`; the run continues where it stopped.

- [ ] **Step 2: Coverage report**

Run: `.venv/Scripts/python.exe -c "from athena.fundamentals.store import FundamentalsStore as S; s=S('.athena/fundamentals.duckdb', read_only=True); rows=s.coverage(); print(len(rows),'company-basis pairs'); print([r['symbol'] for r in rows if r['periods']<10])"`
Expected: about 50 pairs; the second list names companies with fewer than 10 periods (new listings, or filings that failed): each is explained in the result notes.

- [ ] **Step 3: Documentation** — add to `TRD.md` section 2.20 a paragraph "Fundamentals (Phase 6a, 6b)" stating: the package, the three layouts, the depth facts (income FY2019+, balance sheet and cash flow FY2023+), the store schema, the default fetch scope and measured cost, the ratio definitions, and that nothing is scheduled. Add result notes (counts, timings, cross-check findings, companies with gaps) to the master design under a heading "B1a results". Add a one-line correction at the top of the Screener breakdown: "Balance sheet and cash flow exist in NSE XBRL only from FY2023; see the master design D3."

- [ ] **Step 4: Full Python suite, then graphify**

Run: `.venv/Scripts/python.exe -m pytest tests -q` — Expected: all pass (previous count plus the new `test_fund_*` tests; live tests skipped).
Then rebuild the graph with the project's usual command (`graphify update .`) and confirm it reports the new `athena.fundamentals` nodes.

- [ ] **Step 5: Commit**

```bash
git add TRD.md docs graphify-out
git commit -m "docs: fundamentals statement store and ratio engine, with the Nifty 50 run's results"
```

---

## Self-review

**Spec coverage** (master design section 3.3 and decision D2/D3 for 6a, 6b): schema and store (Task 4), parser for every row on Screener's statements with bank and lender layouts (Tasks 1 and 3), resumable throttled ingest with block handling (Task 6), universe order Nifty 50 then 500 then all (Task 5), ratio engine with documented formulas (Task 7), proof on real filings and a cross-check against an independent source (Task 8), Nifty 50 run and docs (Task 9). Not in this plan, by design: shareholding (6d), documents (6e), Yahoo gap-fill and PDFs (6e2, B2), server routes and page (B1b), insurers (HDFCLIFE has no results XBRL in the list; the layout is reported unsupported, not guessed), dividend yield (needs dividend per share, which arrives with the corporate-actions feed in 6d/6e).

**Placeholder scan:** none; every step has its code or command.

**Type consistency:** `Statements` fields (`income`, `cashflow`, `banking` keyed `(date, date)`; `balance` keyed `date`) are used as such in `store.save_statements`; `store.lines` returns balance keyed `(end, end)`, which `show.render` and the page layer re-key by end date; `FilingRef` fields match `athena.pit.nse_results`; ratio inputs use the keys defined in `lines.py` (`equity_capital`, `reserves`, `borrowings`, `total_assets`, `current_liabilities`, `current_assets`, `receivables`, `inventory`, `payables`, `cash`, `bank_balances`, `advances`, `deposits`, income `sales`, `pbt`, `interest`, `net_profit`, `operating_profit`, cash `cfo`, `capex`, `fcf`).

## As built (2026-10-17): deviations from the steps above

- Task 2: the fixture trimmer keeps every fact whose context is not dimensioned (some filings never define `OneD`, `FourD`, `OneI`), not only facts of defined contexts.
- Task 3: `derive_income` for banks uses the filed `OperatingProfitBeforeProvisionAndContingencies` (new line `pre_provision_profit`), expenses are operating expenses without provisions, OPM is over total income; `derive_cashflow(got, layout)` gives no free cash flow for banks or when capex is zero; unreported (0.00) bank ratios are not stored; `reconcile` subtracts provisions; `derive_balance` lost its unused `layout` parameter.
- Task 4: `coverage()` counts distinct `(period_start, period_end)` periods.
- Task 6: the quarter and year of one annual filing share an end date, so a filing counts as one fetch but two periods.
- Task 8: the proof ran on five real companies. It exposed that NSE's legacy results listing stops at December 2024; the fix is `athena.fundamentals.integrated` (new listing, same parser), added after this plan and tested in `tests/test_fund_integrated.py`. Task 9 step 1 (the full Nifty 50 run, about 30 minutes) is **still to run** and is now worth running.
