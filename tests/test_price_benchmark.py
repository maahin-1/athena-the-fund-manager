"""The price-event benchmark: real frozen snapshots with their recorded events. A wrong adjustment, above all one that
hides a genuine fall, must never happen."""
import json

import pytest

from athena.evaluation.golden import GOLDEN_DIR, load_snapshot
from athena.price_integrity.reconcile import reconcile

CASES = json.loads((GOLDEN_DIR / "price_events.json").read_text(encoding="utf-8"))
ADJUSTED = [c["symbol"] for c in CASES if c["applied"]]


@pytest.mark.parametrize("case", CASES, ids=[c["symbol"] for c in CASES])
def test_each_known_event_is_adjusted_and_each_unexplained_break_is_flagged(case):
    snapshot = load_snapshot(case["symbol"])
    result = reconcile(snapshot.bars, snapshot.events)
    assert sorted(str(a.event.day) for a in result.applied) == case["applied"], case["why"]
    assert sorted(str(m.day) for m in result.unexplained) == case["unexplained"], case["why"]


@pytest.mark.parametrize("symbol", ADJUSTED)
def test_an_adjusted_series_has_no_break_left_where_the_event_was(symbol):
    snapshot = load_snapshot(symbol)
    closes = [b.close for b in reconcile(snapshot.bars, snapshot.events).bars]
    assert min(b / a for a, b in zip(closes, closes[1:])) > 0.7


@pytest.mark.parametrize("symbol", ADJUSTED)
def test_the_same_series_with_its_record_removed_is_left_alone_and_flagged(symbol):
    """The genuine-crash control: a halving with no record must never be adjusted away."""
    snapshot = load_snapshot(symbol)
    result = reconcile(snapshot.bars, ())
    assert [b.close for b in result.bars] == [b.close for b in snapshot.bars] and result.unexplained


def test_the_demerger_stays_in_the_prices_exactly_as_the_exchange_printed_it():
    snapshot = load_snapshot("VEDL")
    assert [b.close for b in reconcile(snapshot.bars, snapshot.events).bars] == [b.close for b in sorted(snapshot.bars, key=lambda b: b.timestamp)]
