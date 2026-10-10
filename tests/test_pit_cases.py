from datetime import date, datetime, timedelta, timezone

import pytest
from bar_factory import make_bars
from fund_fixtures import ACME

from athena.contracts import AthenaError
from athena.evaluation.pit_cases import (
    NSE_FILINGS_UNTIL, SOURCE_NONE, SOURCE_NSE, SOURCE_YAHOO, PitCaseBuilder, blind_packet, month_ends, shift_dates,
)
from athena.pit.nse_results import FilingRef
from athena.pit.xbrl import parse_filing
from athena.trading_calendar import ist_date

IST = timezone(timedelta(hours=5, minutes=30))
BARS = make_bars([100.0 + i * 0.1 + (i % 7) for i in range(520)], end=date(2026, 10, 2), symbol="ACME")
DAYS = [ist_date(bar.timestamp) for bar in BARS]
INDEX = {day: 1000.0 + i for i, day in enumerate(DAYS)}
VALUATION = {day: {"pe": 20.0 + (i % 5)} for i, day in enumerate(DAYS)}
LATE_BAR = BARS[-1]


def xbrl(end, start):
    def facts(ctx, items):
        return "".join(f'<in-bse-fin:{t} contextRef="{ctx}">{v}</in-bse-fin:{t}>' for t, v in items.items())

    cur = {"Symbol": "ACME", "NatureOfReportStandaloneConsolidated": "Consolidated", "DateOfEndOfReportingPeriod": end, "DateOfStartOfReportingPeriod": start,
           "RevenueFromOperations": "1000", "ProfitLossForPeriod": "100", "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "1.0",
           "PaidUpValueOfEquityShareCapital": "100", "FaceValueOfEquityShareCapital": "10"}
    return (
        '<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin">'
        + facts("OneD", cur) + facts("FourD", {"DateOfStartOfReportingPeriod": start, "DateOfEndOfReportingPeriod": end}) + "</xbrli:xbrl>"
    )


def builder(yahoo=True, filings=None, load=None):
    ref = FilingRef("ACME", date(2024, 12, 31), datetime(2025, 1, 15, 19, 0, tzinfo=IST), True, False, "https://x/q3.xml")
    listed = [ref] if filings is None else filings
    loader = load or (lambda r: parse_filing(xbrl("2024-12-31", "2024-10-01")))
    yahoo_payload = (lambda symbol: ACME) if yahoo else None
    return PitCaseBuilder(lambda s: BARS, lambda s: BARS, INDEX, VALUATION, lambda s: listed, loader, yahoo_payload, unadjusted_sources=None)


def test_a_case_reads_only_what_existed_on_the_date_and_records_what_happened_after():
    as_of = DAYS[300]
    case = builder().build("ACME", as_of)
    assert case.packets["quant_technical"]["as_of"].startswith(as_of.isoformat())
    assert (case.outcomes[63].start, case.outcomes[63].end) == (as_of, DAYS[363]) and case.blinded is False


def test_an_outcome_that_is_not_known_yet_is_none():
    case = builder().build("ACME", DAYS[-30])
    assert case.outcomes[63] is None and case.outcomes[252] is None


def test_filings_are_the_source_up_to_the_exchanges_cutoff_and_yahoo_after_it():
    early = builder().build("ACME", date(2025, 2, 14))
    assert early.statements_source == SOURCE_NSE and "fundamentals" in early.packets
    assert early.packets["fundamentals"]["latest_quarter"] == "2024-12-31"
    late = builder().build("ACME", date(2026, 5, 20))
    assert late.statements_source == SOURCE_YAHOO and late.packets["fundamentals"]["latest_quarter"] == "2026-03-31"
    assert NSE_FILINGS_UNTIL == date(2025, 3, 31)


def test_the_price_the_fundamentals_use_is_the_close_on_the_date_not_a_later_one():
    as_of = DAYS[300]
    packet = builder().build("ACME", as_of).packets["fundamentals"]
    closes = {ist_date(bar.timestamp): bar.close for bar in BARS}
    assert packet["metrics"]["last_price"]["value"] == pytest.approx(closes[as_of], abs=1e-3)
    assert closes[as_of] != closes[DAYS[-1]]


def test_no_filings_and_no_yahoo_means_no_fundamentals_packet_and_a_note_not_an_error():
    case = builder(yahoo=False, filings=[]).build("ACME", date(2025, 2, 14))
    assert "fundamentals" not in case.packets and case.statements_source == SOURCE_NONE and "lists no filings" in case.notes[0]
    after = builder(yahoo=False).build("ACME", date(2026, 5, 20))
    assert "fundamentals" not in after.packets and "quant_technical" in after.packets


def test_a_failing_filing_source_is_a_note_and_the_technical_packet_is_still_built():
    def down(symbol):
        raise AthenaError("blocked")

    case = PitCaseBuilder(lambda s: BARS, lambda s: BARS, INDEX, VALUATION, down, lambda r: None).build("ACME", date(2025, 2, 14))
    assert "exchange filings unavailable: blocked" in case.notes[0] and "quant_technical" in case.packets and "fundamentals" not in case.packets


def test_too_little_price_history_gives_no_technical_packet_and_says_so():
    case = builder().build("ACME", DAYS[20])
    assert "quant_technical" not in case.packets and "only 21 closes" in case.notes[0]


def test_a_blinded_case_has_no_symbol_and_no_real_year_in_either_packet():
    case = builder().build("ACME", date(2025, 2, 14), blind=True)
    assert case.blinded and case.packets["quant_technical"]["instrument"] == "ASSET" and case.packets["fundamentals"]["instrument"] == "ASSET"
    for packet in case.packets.values():
        text = repr(packet)
        assert "ACME" not in text and "2025-02" not in text and "2024-12-31" not in text
    assert "1997-" in repr(case.packets["fundamentals"])


def test_dates_inside_a_packet_move_back_28_years_and_nothing_else_changes():
    moved = shift_dates({"as_of": "2025-02-14T16:00:00+05:30", "metrics": {"x": {"window": "last 4 quarters to 2024-12-31", "value": 3.5}}, "missing": ["a"]})
    assert moved == {"as_of": "1997-02-14T16:00:00+05:30", "metrics": {"x": {"window": "last 4 quarters to 1996-12-31", "value": 3.5}}, "missing": ["a"]}
    assert blind_packet({"instrument": "TCS", "company": "Tata", "as_of": "2025-01-01"}) == {"instrument": "ASSET", "as_of": "1997-01-01"}


def test_month_ends_are_a_regular_grid_inside_the_range():
    assert month_ends(date(2025, 1, 15), date(2025, 4, 30)) == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30)]
    assert month_ends(date(2025, 12, 1), date(2026, 1, 31)) == [date(2025, 12, 31), date(2026, 1, 31)]
    assert month_ends(date(2025, 2, 1), date(2025, 2, 10)) == []


def test_prices_from_a_source_that_is_already_split_adjusted_are_not_used_for_per_share_ratios():
    ref = FilingRef("ACME", date(2024, 12, 31), datetime(2025, 1, 15, 19, 0, tzinfo=IST), True, False, "https://x/q3.xml")
    adjusted_only = PitCaseBuilder(
        lambda s: BARS, lambda s: BARS, INDEX, VALUATION, lambda s: [ref], lambda r: parse_filing(xbrl("2024-12-31", "2024-10-01")), lambda s: ACME,
    )  # the default source is the exchange's, and these bars say they came from "t"
    case = adjusted_only.build("ACME", date(2025, 2, 14))
    assert "fundamentals" not in case.packets and "quant_technical" in case.packets
    assert "already adjusted for later splits" in case.notes[0] and "t" in case.notes[0]


def test_prices_from_the_exchange_files_count_as_unadjusted():
    from athena.evaluation.pit_cases import UNADJUSTED_SOURCES

    assert {"jugaad", "bhavcopy"} <= UNADJUSTED_SOURCES and "yahoo" not in UNADJUSTED_SOURCES
