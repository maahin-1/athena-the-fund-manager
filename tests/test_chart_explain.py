import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from athena.charting import explain as ex

IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 1, 1, tzinfo=IST)


def bar(i, close, high=None, low=None, volume=1000.0):
    return {"t": (START + timedelta(days=i)).timestamp(), "o": close, "h": high if high is not None else close + 1, "l": low if low is not None else close - 1, "c": close, "v": volume}


def rising(n=60):
    return [bar(i, 100.0 + i) for i in range(n)]  # one rupee up a day


def fact(facts, name, nth=0):
    return [f for f in facts if f.name == name][nth]


def test_the_facts_are_worked_out_from_the_window_and_numbered_in_order():
    facts = ex.build_facts({"symbol": "ABC", "interval": "1D", "bars": rising()})
    assert [f.id for f in facts] == [f"F{i}" for i in range(1, len(facts) + 1)]
    assert fact(facts, "bars shown").value == 60.0
    assert fact(facts, "last close").value == 159.0
    assert fact(facts, "return over the window").value == pytest.approx((159 / 100 - 1) * 100, abs=1e-3)
    assert fact(facts, "window high").value == 160.0 and fact(facts, "window low").value == 99.0
    assert fact(facts, "window high").window == "2026-03-01"  # 59 days after 1 Jan
    assert fact(facts, "last close below the window high").value == pytest.approx((1 - 159 / 160) * 100, abs=1e-3)
    assert fact(facts, "20-bar average").value == pytest.approx(sum(100.0 + i for i in range(40, 60)) / 20)
    assert fact(facts, "50-bar average").value == pytest.approx(sum(100.0 + i for i in range(10, 60)) / 50)
    assert not [f for f in facts if f.name == "200-bar average"]  # too few bars for it
    assert fact(facts, "RSI (14)").value == 100.0  # every day up
    assert fact(facts, "last bar's volume against its usual").value == pytest.approx(1.0)
    assert all(f.unit for f in facts[:2])


def test_swing_highs_and_lows_give_the_nearest_levels_around_the_close():
    closes = [100, 102, 105, 103, 101, 99, 100, 104, 108, 110, 107, 104, 102, 101, 103, 105, 106, 104, 103, 102, 103, 104, 105, 104, 103]
    bars = [bar(i, float(c), high=float(c) + 0.5, low=float(c) - 0.5) for i, c in enumerate(closes)]
    facts = ex.build_facts({"interval": "1D", "bars": bars})
    # the last close is 103; the swing highs are 105.5 (bar 2), 110.5 (bar 9) and 106.5 (bar 16): the nearest above is 106.5
    assert fact(facts, "nearest swing high above the close").value == 106.5
    assert fact(facts, "distance to it").value == pytest.approx((106.5 / 103 - 1) * 100, abs=1e-3)
    # the swing lows are 98.5 (bar 5), 100.5 (bar 13) and 101.5 (bar 19): the nearest below is 101.5
    assert fact(facts, "nearest swing low below the close").value == 101.5
    assert fact(facts, "distance to it", 1).value == pytest.approx((1 - 101.5 / 103) * 100, abs=1e-3)


def test_a_line_the_person_drew_is_read_at_the_last_bar_and_against_the_close():
    bars = rising()
    first, second = bars[10], bars[30]
    drawings = [
        {"tool": "trend", "points": [{"t": first["t"], "p": 110.0}, {"t": second["t"], "p": 130.0}]},  # one rupee a day: 159 at the last bar
        {"tool": "hline", "points": [{"t": first["t"], "p": 150.0}], "text": "resistance"},
        {"tool": "fib", "points": [{"t": first["t"], "p": 200.0}, {"t": second["t"], "p": 100.0}]},
        {"tool": "rect", "points": [{"t": first["t"], "p": 1.0}, {"t": second["t"], "p": 2.0}]},  # a tool with nothing to read at the last bar is left out
    ]
    facts = ex.build_facts({"interval": "1D", "bars": bars, "drawings": drawings})
    assert fact(facts, "the trend line you drew, at the last bar").value == pytest.approx(159.0)
    assert fact(facts, "the horizontal line you drew (resistance)").value == 150.0
    against = [f for f in facts if f.name == "last close against that line"]
    assert against[0].value == pytest.approx(0.0, abs=1e-3) and against[1].value == pytest.approx((159 / 150 - 1) * 100, abs=1e-3)
    assert fact(facts, "Fibonacci 0.618 level you drew").value == pytest.approx(100 + 100 * 0.618)
    assert len([f for f in facts if "rect" in f.name]) == 0


def test_too_few_bars_and_malformed_bars_give_no_facts():
    assert ex.build_facts({"bars": rising(10)}) == []
    assert ex.build_facts({"bars": [{"t": 1}] * 40}) == []
    assert ex.build_facts({}) == []


class Model:
    def __init__(self, *replies):
        self.replies, self.calls, self.prompts = list(replies), 0, []

    def complete(self, system, user):
        self.calls += 1
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def request():
    return {"symbol": "ABC", "interval": "1D", "bars": rising()}


def test_a_reading_that_cites_real_facts_and_only_their_figures_is_accepted():
    facts = ex.build_facts(request())
    last = fact(facts, "last close")
    ret = fact(facts, "return over the window")
    reply = json.dumps({"read": f"The last close of {last.value:g} [{last.id}] is up {ret.value:.1f}% over the window [{ret.id}], and RSI is stretched [F{[f.name for f in facts].index('RSI (14)') + 1}].", "cited": [last.id]})
    out = ex.explain(request(), Model(reply))
    assert out["read"].startswith("The last close of 159")
    assert out["cited"][0] == last.id and out["notes"] == [] and len(out["facts"]) == len(facts)


def test_an_invented_figure_or_an_unknown_fact_sends_the_model_back_once_and_a_second_failure_leaves_the_sheet_alone():
    bad_number = json.dumps({"read": "The stock will reach 400 soon [F2].", "cited": ["F2"]})
    unknown = json.dumps({"read": "Looks strong [F99].", "cited": ["F99"]})
    model = Model(bad_number, unknown)
    out = ex.explain(request(), model)
    assert out["read"] is None and model.calls == 2
    assert "Your previous reply was rejected" in model.prompts[1] and "not on the sheet" in model.prompts[1]
    assert out["notes"] and "facts sheet is the answer" in out["notes"][0] and out["facts"]
    good = json.dumps({"read": "The last close is 159 [F2].", "cited": ["F2"]})
    recovered = ex.explain(request(), Model(bad_number, good))
    assert recovered["read"] == "The last close is 159 [F2]." and recovered["notes"] == []


def test_a_reply_that_cites_nothing_or_is_not_json_or_a_model_that_errors_is_not_trusted():
    assert ex.explain(request(), Model(json.dumps({"read": "No citations at all here."}), "not json at all"))["read"] is None
    assert ex.explain(request(), Model(RuntimeError("down"), RuntimeError("down")))["read"] is None
    fenced = '```json\n{"read": "The last close is 159 [F2].", "cited": ["F2"]}\n```'
    assert ex.explain(request(), Model(fenced))["read"] == "The last close is 159 [F2]."


def test_without_a_model_the_facts_sheet_is_the_answer_and_too_few_bars_say_so():
    out = ex.explain(request(), None)
    assert out["read"] is None and out["facts"] and "no model is available" in out["notes"][0]
    few = ex.explain({"bars": rising(5)}, Model())
    assert few["facts"] == [] and "too few bars" in few["notes"][0]


def test_the_command_reads_a_request_from_stdin_and_always_answers_in_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request())))
    monkeypatch.setattr("athena.llm.router.build_router", lambda **kw: (_ for _ in ()).throw(RuntimeError("no keys")))
    assert ex.main([]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["read"] is None and doc["facts"] and doc["symbol"] == "ABC"
    monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2]"))
    assert ex.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"error": "the request is not a JSON object"}
