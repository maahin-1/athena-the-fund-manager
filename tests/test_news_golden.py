"""The evidence layer of the news golden cases: real headlines frozen around real corporate events, rebuilt into the news
packet. The model layer (what the specialist concludes) is in tests/live/test_live_golden.py."""
import json

import pytest

from athena.agents.base import Specialist
from athena.agents.news_sentiment import NEWS_SENTIMENT
from athena.evaluation.news_cases import case_from_json, case_to_json, load_news_cases
from athena.news.model import company_names, loose_names, mentions
from athena.news.packet import is_routine

CASES = load_news_cases()
SCORED = [c for c in CASES if c.signals]


@pytest.mark.parametrize("case", CASES, ids=[f"{c.symbol}-{c.as_of}" for c in CASES])
def test_each_case_builds_a_packet_that_json_round_trips_and_every_label_says_why(case):
    packet = case.packet()
    json.dumps(packet)
    assert case.why and case_from_json(case_to_json(case)) == case
    assert packet["as_of"] == case.as_of.isoformat() and packet["instrument"] == case.symbol


@pytest.mark.parametrize("case", [c for c in CASES if c.items], ids=lambda c: f"{c.symbol}-{c.as_of}")
def test_a_case_with_news_has_enough_of_it_and_no_routine_filing_or_off_topic_headline_reaches_the_model(case):
    packet = case.packet()
    assert packet["metrics"]["headline_count_30d"]["value"] >= 3 and "press_headline_count_30d" in packet["metrics"]
    names = loose_names(company_names(case.company))
    by_title = {i.title: i for i in case.items}
    for shown in packet["headlines"]:
        item = by_title[shown["title"]]
        assert not (item.source == "nse" and is_routine(item.title))
        assert item.source == "nse" or mentions(item, names), shown["title"]
    assert len(packet["headlines"]) <= 20


def test_the_recorded_corporate_events_are_listed_so_a_mechanical_drop_is_not_read_as_news():
    by_case = {(c.symbol, str(c.as_of)): c.packet() for c in CASES}
    assert any("split (x0.2), prices were adjusted for it" in line for line in by_case[("KOTAKBANK", "2026-01-16")]["recorded_events"])
    assert any("Buy Back, an exchange record" in line for line in by_case[("WIPRO", "2026-06-08")]["recorded_events"])
    assert any("demerger (x0.3742)" in line for line in by_case[("VEDL", "2026-05-04")]["recorded_events"])


def test_the_case_with_no_news_abstains_without_calling_a_model():
    empty = next(c for c in CASES if not c.items)
    calls = []

    class Spy:
        def complete(self, system, user):
            calls.append(user)
            return "{}"

    out = Specialist(NEWS_SENTIMENT, Spy()).analyze(empty.packet())
    assert out["data_coverage"] == "insufficient" and calls == [] and empty.signals == ("abstain",)


def test_the_labels_are_one_of_the_three_signals_or_abstain_and_scored_cases_exist():
    assert SCORED and all(set(c.signals) <= {"bullish", "bearish", "neutral", "abstain"} for c in CASES)
    assert not any(c.signals == ("bearish",) for c in CASES)  # no case is labelled so strictly that one model quirk fails it
