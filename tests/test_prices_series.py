from datetime import datetime, timezone

from athena.adapters.prices import EQUITY_SERIES_RANK, JugaadPriceAdapter, bars_from_jugaad, equity_rows

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
DAY1 = datetime(2026, 9, 30, 18, 30)  # jugaad stamps IST midnight as 18:30 the evening before: this is 1 Oct
DAY2 = datetime(2026, 10, 1, 18, 30)  # 2 Oct


def row(naive, series, close, volume=1000):
    return {"DATE": naive, "SERIES": series, "OPEN": close, "HIGH": close + 1, "LOW": close - 1, "CLOSE": close, "VOLUME": volume}


def test_equity_series_rows_are_kept_and_bond_and_block_deal_series_are_dropped():
    rows = [row(DAY1, "N5", 10750.0), row(DAY1, "EQ", 958.0), row(DAY1, "BL", 957.5), row(DAY1, "T0", 10990.0)]
    assert equity_rows(rows) == [row(DAY1, "EQ", 958.0)]


def test_plain_eq_wins_over_the_other_equity_series_on_the_same_day():
    rows = [row(DAY1, "BE", 100.0), row(DAY1, "EQ", 101.0), row(DAY1, "SM", 99.0)]
    assert [r["SERIES"] for r in equity_rows(rows)] == ["EQ"]


def test_a_day_that_only_has_a_trade_for_trade_series_still_gets_a_row():
    rows = [row(DAY1, "EQ", 100.0), row(DAY2, "BE", 101.0), row(DAY2, "N2", 10000.0)]
    kept = equity_rows(rows)
    assert sorted((r["SERIES"], r["CLOSE"]) for r in kept) == [("BE", 101.0), ("EQ", 100.0)]


def test_a_day_with_only_non_equity_series_has_no_row():
    assert equity_rows([row(DAY1, "N5", 10750.0), row(DAY1, "BL", 958.0)]) == []


def test_rows_without_a_series_column_pass_through_unchanged():
    plain = [{"DATE": DAY1, "OPEN": 1.0, "HIGH": 2.0, "LOW": 0.5, "CLOSE": 1.5, "VOLUME": 10}]
    assert equity_rows(plain) is plain and equity_rows([]) == []


def test_the_rank_table_lists_eq_first():
    assert min(EQUITY_SERIES_RANK, key=EQUITY_SERIES_RANK.get) == "EQ"


def test_bars_from_jugaad_gives_one_equity_bar_per_day_with_the_equity_price():
    rows = [row(DAY1, "N5", 10750.0), row(DAY1, "EQ", 958.0), row(DAY2, "EQ", 960.0), row(DAY2, "N2", 10990.0), row(DAY2, "BL", 959.0)]
    bars = bars_from_jugaad(rows, "SBIN", NOW)
    assert [bar.close for bar in bars] == [958.0, 960.0]


def test_the_adapter_never_returns_a_bond_price_for_an_equity():
    rows = [row(DAY1, "EQ", 958.0), row(DAY1, "N5", 10750.0), row(DAY2, "N6", 10800.0), row(DAY2, "EQ", 960.0)]
    adapter = JugaadPriceAdapter(fetch_rows=lambda symbol, start, end: rows, clock=lambda: NOW)
    bars = adapter.fetch_ohlcv("SBIN", "1d")
    assert [bar.close for bar in bars] == [958.0, 960.0] and max(bar.high for bar in bars) < 1000
