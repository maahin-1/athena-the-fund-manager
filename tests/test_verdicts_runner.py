from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.verdicts.calibration import Calibration
from athena.verdicts.runner import NoVerdictPossible, VerdictRunner
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 9)


class Analyst:
    def __init__(self, signal="bullish", confidence=80, error=None):
        self.signal, self.confidence, self.error, self.packets = signal, confidence, error, []

    def analyze(self, packet):
        self.packets.append(packet)
        if self.error:
            raise self.error
        return {"signal": self.signal, "confidence": self.confidence, "reasoning": "r", "data_coverage": "full", "missing": []}


def technical(close=100.0):
    return {"metrics": {"last_close": {"value": close}}}


def fundamentals(annual="2026-03-31", quarter="2026-06-30"):
    return {"latest_annual_period": annual, "latest_quarter": quarter, "metrics": {}}


def database(tmp_path, symbols=("TCS",), close=100.0, with_statements=True, flags=()):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.set_universe([(s, "stock", f"{s} Ltd", "INE000") for s in symbols], NOW)
        rows = []
        for s in symbols:
            rows.append((s, "technical", DAY, list(flags), technical(close)))
            if with_statements:
                rows.append((s, "fundamentals", DAY, [], fundamentals()))
        w.put_packets(rows, NOW)
    return path


def analysts(**kw):
    return {name: Analyst(**kw) for name in ("quant_technical", "valuation", "moat_quality", "earnings_intelligence")}


def runner(path, specialists=None, calibration=None, minutes=0):
    return VerdictRunner(path, specialists or analysts(), calibration or Calibration(), lambda: NOW + timedelta(minutes=minutes), model="fake")


def test_a_verdict_is_made_from_the_stored_packets_and_stored_with_its_facts(tmp_path):
    path = database(tmp_path)
    people = analysts()
    stored = runner(path, people).make("TCS")
    assert people["quant_technical"].packets == [technical()] and people["valuation"].packets == [fundamentals()]
    payload = stored["payload"]
    assert payload["status"] == "ok" and payload["verdict"]["verdict"] == "Buy" and payload["model"] == "fake"
    assert payload["verdict"]["calibration"] == "uncalibrated" and payload["verdict"]["conviction"] < payload["verdict"]["raw_conviction"]
    assert sorted(payload["taking_part"]) == sorted(people) and payload["skipped"].get("news_sentiment")
    assert (stored["data_as_of"], stored["close"], stored["annual_period"], stored["quarter"]) == (DAY, 100.0, date(2026, 3, 31), date(2026, 6, 30))
    assert runner(path).latest("TCS")["payload"]["verdict"]["verdict"] == "Buy"


def test_a_stock_without_statements_is_judged_on_prices_alone_and_says_so(tmp_path):
    stored = runner(database(tmp_path, with_statements=False)).make("TCS")
    payload = stored["payload"]
    assert payload["taking_part"] == ["quant_technical"]
    assert all("no statements" in why for name, why in payload["skipped"].items() if name != "news_sentiment")
    assert stored["annual_period"] is None


def test_an_unexplained_price_break_holds_back_a_purchase(tmp_path):
    path = database(tmp_path, flags=["price break with no recorded action: 2026-05-04 -45%"])
    verdict = runner(path).make("TCS")["payload"]["verdict"]
    assert verdict["verdict"] == "Hold" and verdict["held_back_by"] == "price data"


def test_when_no_analyst_forms_a_view_nothing_is_stored(tmp_path):
    path = database(tmp_path)
    runner(path).make("TCS")
    with pytest.raises(NoVerdictPossible, match="provider down"):
        runner(path, analysts(error=AthenaError("provider down")), minutes=5).make("TCS")
    with Warehouse(path) as w:
        assert len(w.verdict_history("TCS")) == 1


def test_no_price_packet_or_an_unknown_symbol_is_not_a_verdict(tmp_path):
    path = database(tmp_path)
    with pytest.raises(NoVerdictPossible, match="not a stock or ETF"):
        runner(path).make("NOPE")
    with Warehouse(path) as w:
        w.set_universe([("TCS", "stock", "x", "i"), ("NEW", "stock", "y", "j")], NOW)
    with pytest.raises(NoVerdictPossible, match="no price packet"):
        runner(path).make("NEW")


def test_status_says_why_a_stored_verdict_is_out_of_date(tmp_path):
    path = database(tmp_path)
    runner(path).make("TCS")
    assert runner(path).status("TCS")["reasons"] == []
    with Warehouse(path) as w:  # new results and a large fall arrive
        w.put_packets([("TCS", "technical", DAY, [], technical(80.0)), ("TCS", "fundamentals", DAY, [], fundamentals(quarter="2026-09-30"))], NOW)
    reasons = runner(path).status("TCS")["reasons"]
    assert any("new quarterly results" in r for r in reasons) and any("-20%" in r for r in reasons)
    assert runner(path).status("TCS")["verdict"] is not None


def test_no_verdict_yet_is_none_with_no_reasons(tmp_path):
    assert runner(database(tmp_path)).status("TCS") == {"verdict": None, "reasons": []}


def test_refresh_makes_only_changed_stocks_that_already_have_a_verdict_within_the_budget(tmp_path):
    path = database(tmp_path, symbols=("AAA", "BBB", "CCC", "DDD"))
    for i, s in enumerate(("AAA", "BBB", "CCC")):
        runner(path, minutes=i).make(s)  # DDD never judged
    with Warehouse(path) as w:  # AAA, BBB and DDD change; CCC does not
        w.put_packets([(s, "technical", DAY, [], technical(70.0)) for s in ("AAA", "BBB", "DDD")], NOW)
    summary = runner(path, minutes=60).refresh_changed(budget=1)
    assert summary["checked"] == 3 and summary["changed"] == ["AAA", "BBB"] and summary["made"] == ["AAA"] and summary["left"] == 1
    with Warehouse(path) as w:
        assert len(w.verdict_history("AAA")) == 2 and len(w.verdict_history("BBB")) == 1


def test_a_failing_stock_is_recorded_and_repeated_failures_stop_the_run(tmp_path):
    path = database(tmp_path, symbols=("AAA", "BBB", "CCC", "DDD"))
    for i, s in enumerate(("AAA", "BBB", "CCC", "DDD")):
        runner(path, minutes=i).make(s)
    with Warehouse(path) as w:
        w.put_packets([(s, "technical", DAY, [], technical(60.0)) for s in ("AAA", "BBB", "CCC", "DDD")], NOW)
    broken = analysts(error=AthenaError("provider down"))
    summary = runner(path, broken, minutes=60).refresh_changed(budget=10)
    assert summary["made"] == [] and len(summary["failed"]) == 3 and summary["stopped"] == "3 failures in a row" and summary["left"] == 1
