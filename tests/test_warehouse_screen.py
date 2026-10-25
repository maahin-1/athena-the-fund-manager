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


def _recorded(price_date, price=100.0, low=80.0, base=100.0, high=130.0, implied=0.11, snap=date(2026, 10, 1)):
    return {"price": price, "price_date": price_date, "low": low, "base": base, "high": high, "implied_return": implied, "snap_date": snap}


def _bars_for_value(end_close=100.0):
    closes = [90.0 + i * 0.05 for i in range(299)] + [end_close]
    return make_bars(closes, end=END, symbol="ACME"), closes


def test_the_screen_row_has_the_industry_and_the_stock_against_it():
    bars, _ = _bars_for_value()
    fundamentals = {"industry": {"industry": "Hotel/Gaming"}, "metrics": {"pe_vs_industry": {"value": 1.4}, "pb_vs_industry": {"value": 0.8}, "roe_vs_industry": {"value": -0.02}}}
    row = screen_row("ACME", "stock", "Acme", END, bars, None, fundamentals, 0, None)
    assert (row["industry_group"], row["pe_vs_industry"], row["pb_vs_industry"], row["roe_vs_industry"]) == ("Hotel/Gaming", 1.4, 0.8, -0.02)
    bare = screen_row("ACME", "stock", "Acme", END, bars, None, {"metrics": {}}, 0, None)
    assert bare["industry_group"] is None and bare["pe_vs_industry"] is None


def test_the_recorded_range_is_set_against_todays_close():
    bars, closes = _bars_for_value(end_close=90.0)
    on = date(2026, 9, 28)  # a day inside the bars; its adjusted close is what the share basis is read from
    day_close = next(b.close for b in bars if b.timestamp.astimezone(timezone(timedelta(hours=5, minutes=30))).date() == on)
    row = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(on, price=day_close))
    assert row["value_base"] == 100.0 * day_close / day_close
    assert row["price_vs_value"] == 90.0 / 100.0 - 1 and row["value_position"] == 0  # 90 sits inside 80 to 130
    assert row["implied_return"] == 0.11 and row["valued_on"] == date(2026, 10, 1)
    below = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(on, price=day_close, low=95.0))
    assert below["value_position"] == -1
    above = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(on, price=day_close, high=85.0, base=84.0, low=70.0))
    assert above["value_position"] == 1


def test_a_split_since_the_range_was_recorded_is_undone_by_the_share_basis_of_that_day():
    bars, _ = _bars_for_value(end_close=50.0)
    on = date(2026, 9, 28)
    day_close = next(b.close for b in bars if b.timestamp.astimezone(timezone(timedelta(hours=5, minutes=30))).date() == on)
    old_price = day_close * 2  # recorded before a 2-for-1 split: the stored price was twice the adjusted close of the same day
    row = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(on, price=old_price, low=160.0, base=200.0, high=260.0))
    assert abs(row["value_base"] - 100.0) < 1e-9  # 200 a share before the split is 100 after it
    assert abs(row["price_vs_value"] - (50.0 / 100.0 - 1)) < 1e-9 and row["value_position"] == -1  # 50 is under the 80 low


def test_no_range_when_there_is_no_record_no_bar_on_its_day_or_an_unbelievable_factor():
    bars, _ = _bars_for_value()
    assert screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None)["value_base"] is None
    assert screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(date(2020, 1, 1)))["value_base"] is None
    wild = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(date(2026, 9, 28), price=0.001))
    assert wild["value_base"] is None and wild["valued_on"] is None


def test_a_screen_table_made_before_the_new_columns_gets_them_and_keeps_its_rows(tmp_path):
    import duckdb

    path = tmp_path / "old.duckdb"
    old_columns = {name: t for name, t in SCREEN_COLUMNS.items() if name not in ("industry_group", "pe_vs_industry", "pb_vs_industry", "roe_vs_industry", "value_base", "price_vs_value", "value_position", "implied_return", "valued_on")}
    con = duckdb.connect(str(path))
    columns = ", ".join(f"{n} {t}" for n, t in old_columns.items())
    con.execute(f"CREATE TABLE screen ({columns}, built_at TIMESTAMP NOT NULL, PRIMARY KEY (symbol))")
    con.execute("INSERT INTO screen (symbol, kind, name, built_at) VALUES ('OLD', 'stock', 'Old Ltd', TIMESTAMP '2026-10-01 00:00:00')")
    con.close()
    with Warehouse(path) as w:
        assert w.screen_rows()[0]["symbol"] == "OLD" and w.screen_rows()[0]["industry_group"] is None
        bars, _ = _bars_for_value()
        w.put_screen([screen_row("NEW", "stock", "New Ltd", END, bars, None, {"industry": {"industry": "Steel"}, "metrics": {}}, 0, None)], NOW)
        got = {r["symbol"]: r for r in w.screen_rows()}
        assert set(got) == {"OLD", "NEW"} and got["NEW"]["industry_group"] == "Steel"


def test_the_latest_recorded_ranges_are_read_from_the_ledger_and_a_missing_ledger_gives_none(tmp_path):
    from athena.valuation.ledger import Ledger, latest_snapshots

    assert latest_snapshots(tmp_path / "nothing.duckdb") == {}
    path = tmp_path / "l.duckdb"
    row = {"symbol": "ACME", "basis": "consolidated", "model": "fcff", "price": 100.0, "price_date": date(2026, 9, 28), "low": 80.0, "base": 100.0, "high": 130.0, "implied_return": 0.1, "required_return": 0.12, "industry": "Steel", "cautions": 0}
    with Ledger(path) as ledger:
        ledger.record(date(2026, 9, 1), [{**row, "base": 90.0}], 1, 0)
        ledger.record(date(2026, 10, 1), [row], 1, 0)
    got = latest_snapshots(path)
    assert got["ACME"]["base"] == 100.0 and got["ACME"]["snap_date"] == date(2026, 10, 1)


def test_a_range_whose_base_is_not_above_zero_shows_nothing():
    bars, _ = _bars_for_value()
    on = date(2026, 9, 28)
    day_close = next(b.close for b in bars if b.timestamp.astimezone(timezone(timedelta(hours=5, minutes=30))).date() == on)
    row = screen_row("ACME", "stock", "Acme", END, bars, None, None, 0, None, _recorded(on, price=day_close, low=-50.0, base=-10.0, high=20.0))
    assert row["value_base"] is None and row["price_vs_value"] is None and row["value_position"] is None and row["valued_on"] is None
