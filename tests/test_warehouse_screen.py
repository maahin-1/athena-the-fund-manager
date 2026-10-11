from datetime import date, datetime, timedelta, timezone

from bar_factory import make_bars
from fund_fixtures import ACME

from athena.pit.bhavcopy import Row
from athena.warehouse.packets import build_packets
from athena.warehouse.screen import SCREEN_COLUMNS, screen_row
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
END = date(2026, 10, 9)


def test_returns_are_measured_in_trading_days_back_from_the_last_close():
    closes = [100.0 + i for i in range(300)]  # one rupee up a day
    bars = make_bars(closes, end=END, symbol="ACME")
    row = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None)
    last = closes[-1]
    assert row["close"] == last
    assert abs(row["ret_1d"] - (last / closes[-2] - 1)) < 1e-12 and abs(row["ret_1y"] - (last / closes[-253] - 1)) < 1e-12
    assert row["from_52w_high"] <= 0 <= row["from_52w_low"]


def test_a_return_with_too_little_history_is_none_not_zero():
    bars = make_bars([100.0 + i for i in range(80)], end=END, symbol="ACME")
    row = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None)
    assert row["ret_3m"] is not None and row["ret_6m"] is None and row["ret_1y"] is None


def test_metrics_come_from_the_packets_and_a_missing_one_is_none():
    bars = make_bars([100.0 + i * 0.1 for i in range(300)], end=END, symbol="ACME")
    technical = {"metrics": {"rsi_14": {"value": 61.5}, "trend_alignment": {"value": "aligned_up"}, "sma_50": {"value": 110.0}, "sma_200": {"value": 100.0}}}
    fundamentals = {"sector": "Technology", "metrics": {"pe_trailing": {"value": 18.0}, "roe_latest": {"value": 0.2}, "market_cap": {"value": 5000.0}}}
    row = screen_row("ACME", "stock", "Acme", END, bars, technical, fundamentals, 2, 40)
    assert (row["rsi_14"], row["trend"], row["pe"], row["roe"], row["market_cap_cr"], row["sector"]) == (61.5, "aligned_up", 18.0, 0.2, 5000.0, "Technology")
    assert abs(row["sma50_vs_200"] - 0.1) < 1e-12 and row["pb"] is None and row["flags"] == 2 and row["statements_age_days"] == 40


def test_an_etf_row_has_no_fundamentals():
    bars = make_bars([50.0 + i * 0.01 for i in range(100)], end=END, symbol="FUND")
    row = screen_row("FUND", "etf", "Fund", END, bars, {"metrics": {}}, None, 0, None)
    assert row["pe"] is None and row["sector"] is None and row["kind"] == "etf" and list(row) == list(SCREEN_COLUMNS)


def test_the_build_stores_one_row_per_stock_and_etf_and_a_full_build_drops_the_inactive():
    w = Warehouse(":memory:")
    days = [d for d in (END - timedelta(days=i) for i in range(160)) if d.weekday() < 5][::-1][:100]
    for i, day in enumerate(days):
        w.upsert_day(day, {"ACME": Row("ACME", "EQ", 100 + i * 0.1, 101 + i * 0.1, 99, 100 + i * 0.1, 10000.0), "FUND": Row("FUND", "EQ", 50, 51, 49, 50 + i * 0.01, 5000.0)}, NOW)
    w.set_universe([("ACME", "stock", "Acme Ltd", ""), ("FUND", "etf", "Fund ETF", "")], NOW)
    w.put_statement("ACME", "ok", ACME, "", NOW)
    summary = build_packets(w, NOW)
    rows = {r["symbol"]: r for r in w.screen_rows()}
    assert summary["screen"] == 2 and set(rows) == {"ACME", "FUND"} and rows["ACME"]["name"] == "Acme Ltd" and rows["ACME"]["kind"] == "stock"
    assert [r["symbol"] for r in w.screen_rows(["etf"])] == ["FUND"]
    w.put_screen([], NOW, replace_all=True)  # a full build with nothing active leaves nothing behind
    assert w.screen_rows() == []
