import pytest

from athena.backtest.cli import analyze, live_world

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def world():
    return live_world(years=8)


def test_live_stock_history_has_one_equity_price_per_day_with_no_bond_rows(world):
    bars = world.fetch_bars("SBIN")
    days = [bar.timestamp for bar in bars]
    assert len(days) == len(set(days)) and len(bars) > 1500  # eight years is about 1,950 sessions
    assert max(bar.high for bar in bars) < 2000  # the bond series trade near 10,000


def test_live_backtest_of_a_stock_reports_both_rules_against_buy_and_hold(world):
    text, code = analyze(world, "SBIN", ["trend", "persona"])
    print("\n" + text)
    assert code == 0
    for expected in ("rule: trend", "rule: persona", "buy and hold", "market (NIFTY 50)", "same trades", "15 bps per side"):
        assert expected in text
    assert "DIFFERENT" not in text  # blinding must not change a rule that reads only the price pattern


def test_live_backtest_of_an_etf_runs(world):
    text, code = analyze(world, "NIFTYBEES", ["trend"])
    print("\n" + text)
    assert code == 0 and "Backtest NIFTYBEES" in text


def test_live_market_series_cover_the_backtest_window(world):
    assert len(world.index) > 1500 and len(world.riskfree) > 1500


def test_live_backtest_adjusts_the_reliance_bonus_issue(world):
    text, code = analyze(world, "RELIANCE", ["trend"])
    print("\n" + text)
    assert code == 0 and "Prices adjusted for 1 split or bonus event" in text and "2024-10-28" in text
