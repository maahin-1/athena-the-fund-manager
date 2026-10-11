import json
from datetime import date, datetime, timedelta, timezone

from bar_factory import make_bars

from athena.evaluation.pit_cases import month_ends
from athena.evaluation.pit_market import PACKET_FOR, Market, choose_stocks, rule_trend
from athena.evaluation.pit_run import read_rows, report, run_cases
from athena.pit.bhavcopy import Row
from athena.trading_calendar import ist_date
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc)
END = date(2026, 10, 2)


def rising(n=700, step=0.5):
    return make_bars([100 + step * i for i in range(n)], end=END)


def market(bars):
    index = {ist_date(bar.timestamp): 1000.0 for bar in bars}  # a flat index: the stock's rise is all excess return
    return Market({"UP": bars}, {}, index)


def test_the_trend_rule_reads_the_packets_trend_label_and_nothing_else():
    def packet(value):
        return {"metrics": {"trend_alignment": {"value": value}}}

    assert rule_trend(packet("aligned_up"))["signal"] == "bullish" and rule_trend(packet("aligned_down"))["signal"] == "bearish"
    assert rule_trend(packet("mixed"))["signal"] == "neutral" and rule_trend({"metrics": {}})["signal"] == "neutral"
    assert rule_trend(packet("aligned_up"))["confidence"] == 0


def test_a_case_is_built_from_the_stored_bars_up_to_the_date_with_the_outcome_after_it():
    case = market(rising()).builder().build("UP", date(2025, 6, 30))
    assert "quant_technical" in case.packets and "fundamentals" not in case.packets
    assert case.packets["quant_technical"]["instrument"] == "UP"
    assert case.outcomes[63].label == "outperform" and case.outcomes[252] is not None


def test_a_blind_case_hides_the_symbol_and_moves_the_dates_back():
    packet = market(rising()).builder().build("UP", date(2025, 6, 30), blind=True).packets["quant_technical"]
    assert packet["instrument"] != "UP" and packet["as_of"].startswith("1997")


def test_the_model_and_the_rule_are_logged_on_the_same_cases_and_scored_together(tmp_path):
    class Model:
        calls = 0

        def __call__(self, packet):
            Model.calls += 1
            return {"signal": "bullish", "confidence": 80, "data_coverage": "full"}

    out = tmp_path / "log.jsonl"
    days = month_ends(date(2025, 5, 31), date(2025, 7, 31))
    made = run_cases(market(rising()).builder(), {"quant_technical": Model(), "rule_trend": rule_trend}, ["UP"], days, out, 1, False, packet_for=PACKET_FOR)
    assert made == 6 and Model.calls == 3
    rows = read_rows(out)
    assert {r["specialist"] for r in rows} == {"quant_technical", "rule_trend"} and {r["horizon"] for r in rows} == {63, 126, 252}
    text = report(rows, PACKET_FOR)
    assert "quant_technical, 63-day" in text and "rule_trend, 63-day" in text


def test_stock_choice_is_by_rule_skips_short_histories_and_breaks_and_is_repeatable():
    w = Warehouse(":memory:")
    days = [d for d in (date(2025, 1, 1) + timedelta(days=i) for i in range(500)) if d.weekday() < 5]

    flat = [100.0] * len(days)
    series = {
        "BIG": (flat, 1e6, 0), "SHORT": (flat, 9e5, len(days) - 100), "SMALL": (flat, 1e3, 0),
        "BROKEN": (flat[:200] + [10.0] * (len(days) - 200), 8e5, 0),  # a 90% overnight fall no record explains
    }
    for i, day in enumerate(days):
        rows = {sym: Row(sym, "EQ", c[i], c[i], c[i], c[i], v) for sym, (c, v, skip) in series.items() if i >= skip}
        w.upsert_day(day, rows, NOW)
    w.set_universe([(sym, "stock", sym, "") for sym in series], NOW)
    first = days[300]
    chosen, raw, actions = choose_stocks(w, first, want_top=5, want_mid=0)
    assert chosen == ["BIG", "SMALL"] and set(raw) == {"BIG", "SMALL"} and set(actions) == {"BIG", "SMALL"}
    assert choose_stocks(w, first, want_top=1, want_mid=0)[0] == ["BIG"]
