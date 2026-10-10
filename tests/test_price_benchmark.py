"""The price-event benchmark: real frozen snapshots with their recorded events and NSE rows. A wrong adjustment, above all
one that hides a genuine fall, must never happen."""
import json
from dataclasses import replace

import pytest

from athena.evaluation.golden import GOLDEN_DIR, load_snapshot, reconcile_snapshot
from athena.price_integrity.merge import merge_events
from athena.price_integrity.reconcile import reconcile

CASES = json.loads((GOLDEN_DIR / "price_events.json").read_text(encoding="utf-8"))
ADJUSTED = [c["symbol"] for c in CASES if c["applied"]]
SPLITS = [s for s in ADJUSTED if s != "VEDL"]


@pytest.mark.parametrize("case", CASES, ids=[c["symbol"] for c in CASES])
def test_each_known_event_is_adjusted_each_other_row_is_noted_and_each_unexplained_break_is_flagged(case):
    result = reconcile_snapshot(load_snapshot(case["symbol"]))
    assert sorted(str(a.event.day) for a in result.applied) == case["applied"], case["why"]
    assert sorted(f"{a.day} {a.kind}" for a in result.noted) == sorted(case["noted"]), case["why"]
    assert sorted(str(m.day) for m in result.unexplained) == case["unexplained"], case["why"]


@pytest.mark.parametrize("symbol", [c["symbol"] for c in CASES])
def test_yahoo_and_nse_never_disagree_about_a_split_or_bonus_on_the_benchmark(symbol):
    snapshot = load_snapshot(symbol)
    merged = merge_events(snapshot.events, snapshot.actions)
    assert not merged.conflicts and not merged.notes


@pytest.mark.parametrize("symbol", ADJUSTED)
def test_an_adjusted_series_has_no_break_left_where_the_event_was(symbol):
    closes = [b.close for b in reconcile_snapshot(load_snapshot(symbol)).bars]
    assert min(b / a for a, b in zip(closes, closes[1:])) > 0.7


@pytest.mark.parametrize("symbol", ADJUSTED)
def test_the_same_series_with_every_record_removed_is_left_alone_and_flagged(symbol):
    """The genuine-crash control: a fall with no record must never be adjusted away."""
    snapshot = load_snapshot(symbol)
    result = reconcile(snapshot.bars, (), ())
    assert [b.close for b in result.bars] == [b.close for b in snapshot.bars] and result.unexplained


def test_the_demerger_is_adjusted_only_because_nse_records_it():
    snapshot = load_snapshot("VEDL")
    assert reconcile_snapshot(snapshot).applied and not reconcile_snapshot(replace(snapshot, actions=())).applied
    assert reconcile_snapshot(replace(snapshot, actions=())).unexplained


@pytest.mark.parametrize("symbol", SPLITS)
def test_a_split_still_adjusts_from_yahoo_alone_or_from_nse_alone(symbol):
    snapshot = load_snapshot(symbol)
    assert reconcile_snapshot(replace(snapshot, actions=())).applied
    assert reconcile_snapshot(replace(snapshot, events=())).applied


def test_unadjusted_rows_change_no_price():
    for symbol in ("ITC", "HINDUNILVR", "ADANIENT", "INFY", "TCS"):
        snapshot = load_snapshot(symbol)
        assert [b.close for b in reconcile_snapshot(snapshot).bars] == [b.close for b in sorted(snapshot.bars, key=lambda b: b.timestamp)]
