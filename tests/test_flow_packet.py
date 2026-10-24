from datetime import date

from athena.agents.base import Specialist
from athena.agents.flow import FLOW
from athena.flow import detectors as d
from athena.flow.packet import build_flow_packet

TODAY = date(2026, 10, 22)


def event(kind, days_ago=0):
    return d.Event("ABC", date(2026, 10, 22 - days_ago), kind, {"price_change": 0.03, "price_z": 2.0, "oi_change": 0.05, "oi_z": 3.0, "oi": 1000.0, "put_call_ratio": 1.8, "usual": 1.0, "z": 3.0,
                                                                 "max_pain": 105.0, "max_pain_before": 100.0, "drift_of_price": 0.05})


def test_the_packet_counts_recent_events_by_direction_and_lists_each_with_its_figures():
    events = [event(d.BUILD_UP, 1), event(d.SHORT_COVERING, 3), event(d.SHORT_BUILD_UP, 2), event(d.BUILD_UP, 20), event("oi_spike", 0)]
    packet = build_flow_packet("ABC", TODAY, events, 120, 1.2)
    m = packet["metrics"]
    assert m["flow_events"]["value"] == 4 and m["bullish_flow_events"]["value"] == 2 and m["bearish_flow_events"]["value"] == 1  # the one 20 days old is out; a spike has no side
    assert m["put_call_ratio"]["value"] == 1.2 and m["futures_days_held"]["value"] == 120
    assert [e["day"] for e in packet["events"]] == ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"] and "long build up" in packet["events"][2]["line"]


def test_a_stock_without_derivatives_history_lists_the_figures_as_missing_with_the_reason():
    packet = build_flow_packet("ABC", TODAY, [], 0, None)
    assert packet["metrics"] == {} and set(packet["missing"]) == {"futures_days_held", "put_call_ratio"} and "futures and options" in packet["missing_reasons"]["futures_days_held"]


class Narrator:
    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return '{"signal": "bullish", "confidence": 60, "reasoning": "There are 2 bullish flow events in the last 7 days."}'


def test_the_flow_analyst_reads_the_packet_and_abstains_without_derivatives_data():
    narrator = Narrator()
    out = Specialist(FLOW, narrator).analyze(build_flow_packet("ABC", TODAY, [event(d.BUILD_UP, 1), event(d.SHORT_COVERING, 3)], 120, 1.2))
    assert out["signal"] == "bullish" and out["data_coverage"] == "full" and "long build up" in narrator.prompts[0]
    assert Specialist(FLOW, narrator).analyze(build_flow_packet("ABC", TODAY, [], 0, None))["data_coverage"] == "insufficient"
    assert len(narrator.prompts) == 1  # the abstention never called the model
