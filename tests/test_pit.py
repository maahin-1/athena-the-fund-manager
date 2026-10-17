from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from athena.contracts import AthenaError
from athena.pit.nse_results import RETRY_WAITS, FilingRef, FilingStore, NseResults
from athena.pit.statements import statements_as_of
from athena.pit.xbrl import parse_filing

IST = timezone(timedelta(hours=5, minutes=30))


def xbrl(
    nature="Consolidated", end="2024-12-31", start="2024-10-01", year_start="2024-04-01", fy_start=None, current=None, whole=None,
    instant=None, dimension_fact=None,
):
    """A minimal results filing in the exchange's shape: contexts OneD (the quarter), FourD (the year to date), OneI (the balance sheet)."""
    def facts(ctx, items):
        return "".join(f'<in-bse-fin:{tag} contextRef="{ctx}" unitRef="INR">{value}</in-bse-fin:{tag}>' for tag, value in items.items())

    base = {"Symbol": "ACME", "NatureOfReportStandaloneConsolidated": nature, "WhetherResultsAreAuditedOrUnaudited": "Audited"}
    cur = {**base, "DateOfEndOfReportingPeriod": end, **({"DateOfStartOfReportingPeriod": start} if start else {}), **(current or {})}
    if fy_start:
        cur["DateOfStartOfFinancialYear"] = fy_start
    cum = {"DateOfEndOfReportingPeriod": end, **({"DateOfStartOfReportingPeriod": year_start} if year_start else {}), **(whole or {})}
    extra = ""
    if dimension_fact:
        extra = (
            '<xbrli:context id="Seg"><xbrli:entity/><xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>'
            '<xbrli:scenario/></xbrli:context>' + facts("Seg", dimension_fact)
        )
    return (
        '<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin">'
        '<xbrli:context id="OneD"><xbrli:entity/><xbrli:period><xbrli:startDate>2024-10-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
        + extra + facts("OneD", cur) + facts("FourD", cum) + facts("OneI", instant or {}) + "</xbrli:xbrl>"
    )


def test_a_non_bank_filing_gives_the_quarter_the_year_to_date_the_balance_sheet_and_the_share_count():
    text = xbrl(
        current={"RevenueFromOperations": "1000", "ProfitBeforeTax": "200", "FinanceCosts": "10", "Expenses": "810", "ProfitLossForPeriod": "150",
                 "ProfitOrLossAttributableToOwnersOfParent": "140", "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "7.5",
                 "PaidUpValueOfEquityShareCapital": "200", "FaceValueOfEquityShareCapital": "10"},
        whole={"RevenueFromOperations": "2900", "ProfitOrLossAttributableToOwnersOfParent": "420", "CashFlowsFromUsedInOperatingActivities": "300",
               "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "50", "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "5"},
        instant={"Assets": "5000", "EquityAttributableToOwnersOfParent": "2000", "BorrowingsNoncurrent": "400", "BorrowingsCurrent": "100", "CurrentAssets": "900"},
    )
    filing = parse_filing(text)
    assert (filing.symbol, filing.nature, filing.taxonomy, filing.audited) == ("ACME", "consolidated", "indas", True)
    assert (filing.quarter_start, filing.quarter_end, filing.year_start, filing.is_annual) == (date(2024, 10, 1), date(2024, 12, 31), date(2024, 4, 1), False)
    assert filing.income["Total Revenue"] == 1000 and filing.income["Net Income"] == 140  # profit to the owners, not the whole group's
    assert filing.income["Diluted EPS"] == 7.5 and filing.income["EBIT"] == 210 and filing.income["Operating Income"] == 1000 - (810 - 10)
    assert filing.cumulative["Total Revenue"] == 2900 and filing.cumulative["Net Income"] == 420
    assert filing.balance == {"Total Assets": 5000, "Stockholders Equity": 2000, "Current Assets": 900, "Total Debt": 500}
    assert filing.cashflow == {"Operating Cash Flow": 300, "Capital Expenditure": -55} and filing.shares == 20


def test_a_bank_filing_uses_the_banking_tags_and_equity_is_capital_plus_reserves():
    filing = parse_filing(xbrl(
        nature="Standalone",
        current={"InterestEarned": "800", "Income": "1200", "InterestExpended": "300", "ProfitLossFromOrdinaryActivitiesBeforeTax": "250",
                 "ProfitLossForThePeriod": "190", "DilutedEarningsPerShareAfterExtraordinaryItems": "9.5"},
        instant={"Capital": "100", "ReservesAndSurplus": "900", "Assets": "9000"},
    ))
    assert (filing.nature, filing.taxonomy) == ("standalone", "banking")
    assert filing.income == {"Total Revenue": 1200, "Net Income": 190, "Pretax Income": 250, "Diluted EPS": 9.5, "Interest Expense": 300}
    assert filing.balance["Stockholders Equity"] == 1000 and "EBIT" not in filing.income


def test_a_tag_filled_with_zero_does_not_hide_the_tag_that_holds_the_figure():
    filing = parse_filing(xbrl(current={
        "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "0.00", "DilutedEarningsLossPerShareFromContinuingOperations": "31.48",
    }))
    assert filing.income["Diluted EPS"] == 31.48
    assert parse_filing(xbrl(current={"DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "0.00"})).income["Diluted EPS"] == 0.0


def test_older_filings_that_give_only_the_end_and_the_financial_year_start_still_place_the_period():
    filing = parse_filing(xbrl(end="2023-03-31", start=None, year_start=None, fy_start="2022-04-01"))
    assert (filing.quarter_start, filing.quarter_end, filing.year_start, filing.is_annual) == (date(2023, 1, 1), date(2023, 3, 31), date(2022, 4, 1), True)


def test_facts_with_a_dimension_are_ignored_and_a_page_that_is_not_a_filing_is_refused():
    filing = parse_filing(xbrl(current={"RevenueFromOperations": "1000"}, dimension_fact={"RevenueFromOperations": "999999"}))
    assert filing.income["Total Revenue"] == 1000
    for text in ("<html><body>Resource not found</body></html>", "not xml at all", xbrl(end="")):
        with pytest.raises(ValueError):
            parse_filing(text)


# ---- statements as of a date

def ref(end, filed, consolidated=True, annual=False, url=None):
    return FilingRef("ACME", end, filed, consolidated, annual, url or f"https://x/{end}-{filed.isoformat()}-{consolidated}.xml")


def at(day, hour=19, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def filing_for(end, revenue, profit, eps=1.0, annual=False, nature="Consolidated"):
    start = date(end.year - 1, 4, 1) if annual else date(end.year, end.month, 1)
    fy_start = f"{end.year - 1}-04-01"
    return parse_filing(xbrl(
        nature=nature, end=end.isoformat(), start=(end - timedelta(days=89)).isoformat(),
        year_start=fy_start if annual else start.isoformat(),
        current={"RevenueFromOperations": revenue, "ProfitLossForPeriod": profit, "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": eps,
                 "PaidUpValueOfEquityShareCapital": "100", "FaceValueOfEquityShareCapital": "10"},
        whole={"RevenueFromOperations": revenue * 4, "ProfitLossForPeriod": profit * 4, "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": eps * 4},
        instant={"Assets": "5000", "Equity": "2000"},
    ))


def library(entries):
    """refs and a loader over (ref, filing) pairs."""
    table = {r.url: f for r, f in entries}
    return [r for r, _ in entries], lambda r: table[r.url]


def test_only_filings_public_by_the_close_of_the_day_are_used_and_one_filed_after_the_close_is_not():
    q3 = ref(date(2024, 12, 31), at(date(2025, 1, 15), 10))
    q4_after_close = ref(date(2025, 3, 31), at(date(2025, 4, 20), 18), annual=True)
    refs, load = library([(q3, filing_for(date(2024, 12, 31), 100, 10)), (q4_after_close, filing_for(date(2025, 3, 31), 120, 12, annual=True))])
    before = statements_as_of(refs, load, date(2025, 4, 20))
    assert list(before["quarterly"]["income"]["Total Revenue"]) == ["2024-12-31"] and before["annual"]["income"] == {}
    after = statements_as_of(refs, load, date(2025, 4, 21))
    assert sorted(after["quarterly"]["income"]["Total Revenue"]) == ["2024-12-31", "2025-03-31"] and "2025-03-31" in after["annual"]["income"]["Total Revenue"]


def test_a_revision_replaces_the_period_only_from_the_day_it_was_filed():
    first = ref(date(2024, 12, 31), at(date(2025, 1, 15)))
    revised = ref(date(2024, 12, 31), at(date(2025, 3, 1)))
    refs, load = library([(first, filing_for(date(2024, 12, 31), 100, 10)), (revised, filing_for(date(2024, 12, 31), 90, 9))])
    assert statements_as_of(refs, load, date(2025, 2, 1))["quarterly"]["income"]["Total Revenue"]["2024-12-31"] == 100
    assert statements_as_of(refs, load, date(2025, 3, 2))["quarterly"]["income"]["Total Revenue"]["2024-12-31"] == 90


def test_consolidated_is_used_when_the_company_has_it_and_the_two_are_never_mixed():
    standalone = ref(date(2024, 9, 30), at(date(2024, 10, 20)), consolidated=False)
    consolidated = ref(date(2024, 12, 31), at(date(2025, 1, 15)))
    refs, load = library([(standalone, filing_for(date(2024, 9, 30), 70, 7, nature="Standalone")), (consolidated, filing_for(date(2024, 12, 31), 100, 10))])
    assert list(statements_as_of(refs, load, date(2025, 2, 1))["quarterly"]["income"]["Total Revenue"]) == ["2024-12-31"]
    only_standalone = statements_as_of(refs[:1], load, date(2025, 2, 1))
    assert list(only_standalone["quarterly"]["income"]["Total Revenue"]) == ["2024-09-30"]


def test_the_payload_carries_no_description_of_today_and_says_what_it_cannot_know():
    q = ref(date(2024, 12, 31), at(date(2025, 1, 15)))
    refs, load = library([(q, filing_for(date(2024, 12, 31), 100, 10))])
    payload = statements_as_of(refs, load, date(2025, 2, 1), {"sector": "Technology", "currentPrice": 999.0, "marketCap": 1e12, "dividendRate": 5.0})
    assert payload["info"] == {"sector": "Technology", "sharesOutstanding": 10.0, "financialCurrency": "INR"}
    assert payload["earnings_dates"] == [] and payload["quality_flags"] == []


def test_a_filing_that_cannot_be_read_is_named_and_the_rest_still_load():
    good, bad = ref(date(2024, 12, 31), at(date(2025, 1, 15))), ref(date(2024, 9, 30), at(date(2024, 10, 15)))

    def load(r):
        if r is bad:
            raise AthenaError("unreadable filing")
        return filing_for(date(2024, 12, 31), 100, 10)

    payload = statements_as_of([good, bad], load, date(2025, 2, 1))
    assert list(payload["quarterly"]["income"]["Total Revenue"]) == ["2024-12-31"]
    assert "period ended 2024-09-30 could not be read" in payload["quality_flags"][0]


def test_no_filings_gives_an_empty_payload_the_packet_can_read():
    payload = statements_as_of([], lambda r: None, date(2025, 2, 1))
    assert payload["annual"]["income"] == {} and payload["quarterly"]["income"] == {} and payload["info"]["sharesOutstanding"] is None


# ---- the exchange's list and the cache

class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, replies):
        self.replies, self.headers, self.calls = list(replies), {}, []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append(url)
        if url.endswith("nseindia.com"):
            return FakeResponse(403)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


ROWS = [
    {"toDate": "31-Dec-2024", "filingDate": "15-Jan-2025 19:20", "consolidated": "Consolidated", "xbrl": "https://x/a.xml"},
    {"toDate": "30-Sep-2024", "filingDate": "-", "consolidated": "Consolidated", "xbrl": "https://x/b.xml"},  # no filing time: cannot be placed
    {"toDate": "30-Jun-2024", "filingDate": "15-Jul-2024 10:00", "consolidated": "Non-Consolidated", "xbrl": "https://x/-"},  # no XBRL
    {"toDate": "31-Mar-2018", "filingDate": "15-May-2018 10:00", "consolidated": "Consolidated", "xbrl": "https://x/old.xml"},  # before XBRL
    {"toDate": "30-Jun-2024", "filingDate": "15-Jul-2024 10:00", "consolidated": "Non-Consolidated", "xbrl": "https://x/c.xml"},
]


def client(replies):
    sleeps = []
    session = FakeSession(replies)
    clock = iter(range(0, 10_000, 10))
    return NseResults(session, sleep=sleeps.append, clock=lambda: next(clock)), session, sleeps


def test_only_filings_with_a_filing_time_and_an_xbrl_file_in_the_xbrl_era_are_listed_oldest_first():
    results, _, _ = client([FakeResponse(payload=ROWS), FakeResponse(payload=[])])  # the quarterly list, then the annual list
    refs = results.filings("ACME")
    assert [(r.period_end, r.consolidated, r.annual, r.url) for r in refs[:2]] == [
        (date(2024, 6, 30), False, False, "https://x/c.xml"), (date(2024, 12, 31), True, False, "https://x/a.xml"),
    ]
    assert refs[0].filed == datetime(2024, 7, 15, 10, 0, tzinfo=IST) and all(r.url != "https://x/old.xml" for r in refs)


def test_a_refused_request_is_retried_after_the_waits_then_gives_up_with_an_error():
    results, session, sleeps = client([FakeResponse(503), FakeResponse(503), FakeResponse(payload=ROWS), FakeResponse(payload=[])])
    assert len(results.filings("ACME")) == 2
    assert [s for s in sleeps if s in RETRY_WAITS] == list(RETRY_WAITS)
    blocked, _, waits = client([FakeResponse(503)])
    with pytest.raises(AthenaError, match="NSE results unavailable for ACME: HTTP 503"):
        blocked.filings("ACME")
    assert [s for s in waits if s in RETRY_WAITS] == list(RETRY_WAITS)


def test_an_error_page_that_is_not_a_refusal_is_not_retried():
    results, session, sleeps = client([FakeResponse(404)])
    with pytest.raises(AthenaError, match="NSE results unavailable for ACME: HTTP 404"):
        results.filings("ACME")
    assert not [s for s in sleeps if s in RETRY_WAITS]


def test_each_filing_is_downloaded_once_and_then_read_from_disk(tmp_path):
    fetched = []

    def fetch(r):
        fetched.append(r.url)
        return xbrl(current={"RevenueFromOperations": "1000"})

    store = FilingStore(NseResults(FakeSession([FakeResponse()])), tmp_path, fetch)
    target = ref(date(2024, 12, 31), at(date(2025, 1, 15)))
    assert store.filing(target).income["Total Revenue"] == 1000 and store.filing(target).income["Total Revenue"] == 1000
    assert fetched == [target.url] and len(list(tmp_path.iterdir())) == 1


def test_a_downloaded_page_that_is_not_a_filing_is_an_error_not_a_crash(tmp_path):
    store = FilingStore(NseResults(FakeSession([FakeResponse()])), tmp_path, lambda r: "<html>Resource not found</html>")
    with pytest.raises(AthenaError, match="unreadable filing"):
        store.filing(ref(date(2024, 12, 31), at(date(2025, 1, 15))))


def test_a_results_list_is_fetched_once_and_then_read_from_disk(tmp_path):
    from athena.pit.nse_results import CachedFilings

    ref = FilingRef("ACME", date(2024, 12, 31), datetime(2025, 1, 15, 19, 0, tzinfo=IST), True, False, "https://x/q3.xml")
    calls = []
    cached = CachedFilings(None, tmp_path, fetch=lambda symbol: calls.append(symbol) or [ref])
    assert cached.filings("ACME") == [ref]
    assert cached.filings("ACME") == [ref]
    assert calls == ["ACME"]
    (tmp_path / "index_ACME.json").write_text("not json", encoding="utf-8")
    assert cached.filings("ACME") == [ref] and calls == ["ACME", "ACME"]  # a damaged cache is fetched again


def test_a_failed_fetch_leaves_nothing_cached(tmp_path):
    from athena.pit.nse_results import CachedFilings

    def refuse(symbol):
        raise AthenaError("HTTP 403")

    with pytest.raises(AthenaError):
        CachedFilings(None, tmp_path, fetch=refuse).filings("ACME")
    assert not list(tmp_path.glob("index_*"))


def test_a_diluted_line_left_at_zero_falls_back_to_the_basic_line():
    filing = parse_filing(xbrl(
        current={
            "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "0.00", "DilutedEarningsLossPerShareFromContinuingOperations": "0.00",
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "31.14",
        },
        whole={"DilutedEarningsLossPerShareFromContinuingOperations": "0.00", "BasicEarningsLossPerShareFromContinuingOperations": "115.19"},
    ))
    assert filing.income["Diluted EPS"] == 31.14
    assert filing.cumulative["Diluted EPS"] == 115.19


def test_a_timeout_is_retried_after_the_waits_and_a_lasting_one_is_an_error_not_a_crash():
    results, _, sleeps = client([requests.ReadTimeout("slow"), FakeResponse(payload=ROWS), FakeResponse(payload=[])])
    assert len(results.filings("ACME")) == 2
    assert [s for s in sleeps if s in RETRY_WAITS] == [RETRY_WAITS[0]]
    dead, _, waits = client([requests.ReadTimeout("slow")])
    with pytest.raises(AthenaError, match="NSE results unavailable for ACME: ReadTimeout"):
        dead.filings("ACME")
    assert [s for s in waits if s in RETRY_WAITS] == list(RETRY_WAITS)
