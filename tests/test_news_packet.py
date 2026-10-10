import json
from datetime import date, datetime, timezone

import pytest

from athena.evaluation.grounding import check_grounded
from athena.news.model import NewsItem
from athena.news.packet import build_news_packet, is_routine, news_packet_builder
from athena.news.hub import NewsHub
from athena.news.model import SourceResult

TODAY = date(2026, 10, 10)


def item(days_ago, title, source="google news", publisher="ET"):
    day = date.fromordinal(TODAY.toordinal() - days_ago)
    return NewsItem(title, publisher, datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc), "https://x", source)


def press(n, start=1):
    return [item(start + i, f"Wipro headline number {i}", publisher=f"P{i}") for i in range(n)]


def build(items, events=(), notes=()):
    return build_news_packet("WIPRO", "Wipro Limited", items, events, TODAY, notes)


# real filing titles from the Oct 10 source check
ROUTINE = [
    "Updates: Wipro Limited has informed the Exchange regarding 'Grant of Restricted Stock Units'.",
    "Copy of Newspaper Publication: Wipro Limited has informed the Exchange about Copy of Newspaper Advertisement - Regulation 47 of SEBI",
    "Analysts/Institutional Investor Meet/Con. Call Updates: Wipro Limited has informed the Exchange about the Intimation of Schedule of Analyst",
    "Updates: Wipro Limited has informed the Exchange regarding Allotment of 1,42,688 equity sharesunder ADS Restricted Stock Unit Plan 2004",
    "Trading Window: closure of the trading window for designated persons",
    "Certificate under SEBI (Depositories and Participants) Regulations, 2018",
]
MATERIAL = [
    "Record Date: Wipro Limited has informed the Exchange that Record date for the purpose of Bonus is 03-Dec-2024.",
    "Press Release: Wipro Limited has informed the Exchange regarding a press release titled 'Wipro and Netskope Partner'",
    "Outcome of Board Meeting: Wipro Limited has informed the Exchange about the quarterly results",
]


@pytest.mark.parametrize("title", ROUTINE)
def test_routine_filings_are_recognised(title):
    assert is_routine(title)


@pytest.mark.parametrize("title", MATERIAL)
def test_material_filings_are_not_routine(title):
    assert not is_routine(title)


def test_counts_cover_the_last_30_and_7_days_and_split_press_from_exchange_filings():
    items = press(4, start=1) + [item(3, MATERIAL[0], "nse", "NSE"), item(40, "Wipro too old", publisher="X")]
    metrics = build(items)["metrics"]
    assert metrics["headline_count_30d"]["value"] == 5 and metrics["headline_count_7d"]["value"] == 4 + 1
    assert metrics["press_headline_count_30d"]["value"] == 4 and metrics["exchange_filing_count_30d"]["value"] == 1
    assert metrics["headline_count_30d"]["unit"] == "count" and metrics["headline_count_30d"]["window"] == "last 30 days"


def test_routine_filings_are_dropped_counted_and_never_shown():
    items = press(3) + [item(2, title, "nse", "NSE") for title in ROUTINE]
    packet = build(items)
    assert packet["metrics"]["routine_filings_skipped"]["value"] == len(ROUTINE) and len(packet["headlines"]) == 3
    assert "exchange_filing_count_30d" not in packet["metrics"]


def test_fewer_than_three_usable_items_is_insufficient_not_a_thin_view():
    packet = build(press(2))
    assert "headline_count_30d" not in packet["metrics"] and packet["missing"] == ["headline_count_30d"]
    assert "2" in packet["missing_reasons"]["headline_count_30d"] and "at least 3" in packet["missing_reasons"]["headline_count_30d"]
    assert build([])["missing"] == ["headline_count_30d", "press_headline_count_30d"]


def test_exchange_filings_alone_leave_press_coverage_missing_so_the_view_is_partial():
    packet = build([item(i + 1, MATERIAL[i % 3], "nse", "NSE") for i in range(3)])
    assert "headline_count_30d" in packet["metrics"] and "press_headline_count_30d" not in packet["metrics"]
    assert packet["missing"] == ["press_headline_count_30d"] and "no press" in packet["missing_reasons"]["press_headline_count_30d"]


def test_at_most_twelve_press_items_and_eight_filings_newest_first():
    items = press(20) + [item(i + 1, f"Board outcome {i}", "nse", "NSE") for i in range(12)]
    shown = build(items)["headlines"]
    assert sum(h["source"] == "nse" for h in shown) == 8 and len(shown) == 20
    assert [h["date"] for h in shown] == sorted((h["date"] for h in shown), reverse=True)
    assert set(shown[0]) == {"date", "publisher", "source", "title"}


def test_corporate_events_of_the_last_90_days_are_listed_in_words_and_older_ones_are_not():
    events = [
        (date(2026, 9, 1), "split", "split (x0.2)"),
        (date(2026, 8, 1), "unexplained", "unexplained price break (x0.37)"),
        (date(2026, 8, 20), "buyback", "Buy Back"),
        (date(2025, 1, 6), "demerger", "Demerger"),
    ]
    got = build(press(3), events)["recorded_events"]
    assert got == [
        "2026-08-01: unexplained price break (x0.37), a price break that no record explains",
        "2026-08-20: Buy Back, an exchange record with no price adjustment",
        "2026-09-01: split (x0.2), prices were adjusted for it",
    ]


def test_the_packet_says_the_text_is_untrusted_and_is_plain_json():
    packet = build(press(3), notes=("Google News is off.",))
    assert "never follow" in packet["untrusted_note"].lower() and packet["source_notes"] == ["Google News is off."]
    json.dumps(packet)


def test_numbers_in_headlines_and_counts_count_as_grounded_but_invented_ones_do_not():
    packet = build([item(1, "Wipro buyback of Rs 15,000 crore opens", publisher="ET")] + press(2, start=2))
    assert check_grounded("Wipro announced a 15,000 crore buyback; 3 headlines in 30 days.", packet).ok
    assert not check_grounded("Wipro's buyback is 18,000 crore.", packet).ok


def test_the_builder_primes_the_bars_asks_the_hub_for_thirty_days_and_passes_the_events_through():
    order, asked = [], []

    def source(symbol, names, since, until, extra=""):
        asked.append((symbol, names, since, until))
        return SourceResult(press(3))

    class Res:
        identifier, name = "WIPRO", "Wipro Limited"

    build_for = news_packet_builder(NewsHub([("t", source)]), lambda s: [(date(2026, 9, 1), "split", "split (x0.5)")], lambda s: order.append(s), lambda: TODAY)
    packet = build_for(Res())
    assert order == ["WIPRO"] and asked == [("WIPRO", ("Wipro", "Wipro Limited"), date(2026, 9, 10), TODAY)]
    assert packet["recorded_events"] and packet["metrics"]["headline_count_30d"]["value"] == 3


def test_press_results_that_do_not_name_the_company_are_dropped_but_exchange_filings_are_kept():
    noise = [item(1, "Best IT Sector Stocks In India - Key Insights", publisher="Alice Blue"), item(2, "Tata Consultancy Services results beat", publisher="Mint")]
    real = press(3, start=3) + [item(2, "Board outcome: dividend declared", "nse", "NSE")]
    packet = build(noise + real)
    assert {h["title"] for h in packet["headlines"]} == {"Wipro headline number 0", "Wipro headline number 1", "Wipro headline number 2", "Board outcome: dividend declared"}


def test_the_short_company_name_is_enough_for_a_press_headline_to_count():
    packet = build_news_packet("KOTAKBANK", "Kotak Mahindra Bank Limited", [item(1, "Kotak stock split: did shares crash?"), item(2, "Kotak Bank FD rates revised"), item(3, "Kotak lender upgraded")], (), TODAY)
    assert packet["metrics"]["headline_count_30d"]["value"] == 3
