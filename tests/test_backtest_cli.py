from dataclasses import replace

import pytest
from bar_factory import NOW, make_bars

from athena.backtest.adjust import Adjustment
from athena.backtest.cli import BacktestWorld, analyze, assumptions, format_runs, main, run_rules
from athena.backtest.engine import Config
from athena.backtest.rules import RULES, Rule
from athena.contracts import AthenaError, Record
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import ist_date

CLOSES = [100.0 + i * 0.3 + (3 if i % 13 == 0 else 0) - (4 if i % 31 == 0 else 0) for i in range(520)]
BARS = make_bars(CLOSES, symbol="SBIN")
BREAK = 450  # inside the evaluation window, while the trend rule holds
# The same history as unadjusted exchange prices with a 1:1 bonus on bar BREAK: every earlier price is twice as high.
BONUS_BARS = [
    replace(bar, open=bar.open * 2, high=bar.high * 2, low=bar.low * 2, close=bar.close * 2, volume=bar.volume / 2)
    if i < BREAK else bar
    for i, bar in enumerate(BARS)
]


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("SBILIFE", "SBI Life Insurance Company Limited"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def world(fetched=None, bars=BARS):
    days = [ist_date(bar.timestamp) for bar in BARS]
    rate = {day: 100.0 * 1.0002 ** i for i, day in enumerate(days)}
    index = {day: 1000.0 + i for i, day in enumerate(days)}

    def fetch_bars(symbol):
        if fetched is not None:
            fetched.append(symbol)
        return bars

    return BacktestWorld(make_resolver(), fetch_bars, rate, index)


def test_a_known_symbol_gets_both_rules_a_blinding_check_the_assumptions_and_the_disclaimer():
    fetched = []
    text, code = analyze(world(fetched), "sbin", list(RULES))
    assert code == 0 and fetched == ["SBIN"]
    for expected in ("Backtest SBIN  rule: trend", "Backtest SBIN  rule: persona", "buy and hold", "market (NIFTY 50)",
                     "trend: same trades, persona: same trades", "15 bps per side", "cash earns nothing", "not financial advice"):
        assert expected in text


def test_an_etf_can_be_backtested_too():
    text, code = analyze(world(), "NIFTYBEES", ["trend"])
    assert code == 0 and "Backtest NIFTYBEES" in text and "rule: persona" not in text


def test_an_ambiguous_name_lists_candidates_downloads_nothing_and_returns_two():
    fetched = []
    text, code = analyze(world(fetched), "sbi", ["trend"])
    assert code == 2 and fetched == [] and "could be more than one instrument" in text and "SBILIFE" in text


def test_an_unknown_rule_is_rejected_before_any_run():
    with pytest.raises(ValueError, match="unknown rule 'magic'"):
        analyze(world(), "sbin", ["magic"])


def test_the_blinding_check_catches_a_rule_that_depends_on_the_absolute_price(monkeypatch):
    def cheating_entry(packet):
        return packet["metrics"]["last_close"]["value"] > 250.0  # true on these real-looking prices, false once rescaled to 100

    monkeypatch.setitem(RULES, "cheat", Rule("cheat", "enters on a price level", cheating_entry, lambda p: False))
    expensive = make_bars([close + 200.0 for close in CLOSES], symbol="SBIN")  # starts at 300, so blinding really rescales it
    market = world()
    runs = run_rules(expensive, ["trend", "cheat"], market.riskfree, market.index, Config())
    assert [run.blinded_identical for run in runs] == [True, False]


def test_main_joins_the_words_applies_the_rule_choice_and_prints_the_report(capsys):
    assert main(["state", "bank", "--rule", "trend", "--years", "5"], factory=lambda years: world()) == 2  # "state bank" is ambiguous here
    capsys.readouterr()
    assert main(["SBIN", "--rule", "persona"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: persona" in out and "rule: trend" not in out


def test_main_reports_errors_without_a_traceback(capsys):
    def broken(years):
        raise AthenaError("no price data")

    assert main(["SBIN"], factory=broken) == 1 and "error: no price data" in capsys.readouterr().out


def test_a_bonus_issue_is_adjusted_before_either_replay_so_buy_and_hold_sees_the_true_move():
    market = world()
    true = run_rules(BARS, ["trend", "persona"], market.riskfree, market.index)
    runs = run_rules(BONUS_BARS, ["trend", "persona"], market.riskfree, market.index)
    expected = (Adjustment(ist_date(BARS[BREAK].timestamp), 0.5),)
    assert [run.adjustments for run in runs] == [expected, expected] and [run.adjustments for run in true] == [(), ()]
    for adjusted, real in zip(runs, true):
        assert adjusted.summary.benchmark_total_return == pytest.approx(real.summary.benchmark_total_return)
    assert runs[0].summary.benchmark_total_return > 0.2  # unadjusted, the halving would show as a loss
    assert runs[0].result.trades == () and runs[0].result.in_market[-1]  # no stop fired on a gap that never happened
    assert all(run.blinded_identical for run in runs)  # the blinded replay sees the adjusted bars too


def test_the_report_names_the_adjustments_on_the_line_before_the_blinding_check_only_when_there_were_any():
    text, _ = analyze(world(bars=BONUS_BARS), "SBIN", ["trend"])
    lines = text.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("Blinding check"))
    assert lines[at - 1] == (
        "Prices adjusted for 1 split or bonus event(s) found from an overnight price break, not from corporate-action "
        f"data: {ist_date(BARS[BREAK].timestamp)} (x0.5)."
    )
    plain, _ = analyze(world(), "SBIN", ["trend"])
    assert "Prices adjusted" not in plain and "Prices adjusted" not in format_runs("SBIN", [])


def test_the_assumptions_state_costs_stop_timing_cash_adjustment_index_and_open_positions():
    text = assumptions(Config())
    for expected in ("15 bps per side", "2 x ATR below the entry fill", "decisions at the close", "fills at the next open",
                     "cash earns nothing", "overnight rate", "penalises time in cash", "splits and bonuses",
                     "large overnight breaks", "never for dividends", "NIFTY 50 comparison is a price index",
                     "open at the end counts in the total return but not in the trade statistics"):
        assert expected in text
    assert text.count(". ") <= 1  # one or two sentences
    unstopped = assumptions(Config(stop_atr_multiple=None, cost_bps_per_side=10.0))
    assert "None" not in unstopped and "ATR" not in unstopped and "no protective stop" in unstopped
    assert "10 bps per side" in unstopped


def test_an_ambiguous_backtest_query_prints_ten_candidates_and_says_how_many_more():
    from dataclasses import replace

    store = DataStore()
    for n in range(25):
        store.put(Record(EQUITY_DATASET, f"ALPHA{n:02d}", NOW, "x", {"name": f"Alpha Industries {n:02d} Limited", "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    many = replace(world(), resolver=InstrumentResolver(InstrumentIndex.from_store(store, now=NOW)))
    text, code = analyze(many, "alpha", ["trend"])
    assert code == 2 and "ALPHA09" in text and "ALPHA10" not in text
    assert "...and 15 more; type the exact symbol." in text


def write_strategy(tmp_path, data):
    import json

    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


MINE = {"name": "Mine", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 1}}, "exit": {"op": "lt", "left": {"price": "close"}, "right": {"const": 1}}}


def test_with_no_flags_both_built_in_rules_run_and_a_preset_replaces_them(capsys):
    assert main(["SBIN"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: trend" in out and "rule: persona" in out
    assert main(["SBIN", "--preset", "rsi_reversion"], factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    assert "rule: RSI mean reversion" in out and "rule: trend" not in out and "Buy when the RSI (length 14) is below 30." in out


def test_a_built_in_rule_can_be_asked_for_alongside_a_preset_and_a_strategy_file(capsys, tmp_path):
    argv = ["SBIN", "--rule", "trend", "--preset", "golden_cross", "--strategy", write_strategy(tmp_path, MINE)]
    assert main(argv, factory=lambda years: world()) == 0
    out = capsys.readouterr().out
    for expected in ("rule: trend", "rule: Golden cross", "rule: Mine", "Buy when the close is above 1."):
        assert expected in out
    assert "rule: persona" not in out


def test_a_bad_strategy_file_is_reported_without_a_traceback(capsys, tmp_path):
    assert main(["SBIN", "--strategy", str(tmp_path / "missing.json")], factory=lambda years: world()) == 1
    assert "error: cannot read" in capsys.readouterr().out
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main(["SBIN", "--strategy", str(broken)], factory=lambda years: world()) == 1
    assert "is not valid JSON" in capsys.readouterr().out
    bad = write_strategy(tmp_path, {**MINE, "entry": {"op": "gt", "left": {"ind": "nope"}, "right": {"const": 1}}})
    assert main(["SBIN", "--strategy", bad], factory=lambda years: world()) == 1
    assert "error: entry.left.ind: unknown indicator 'nope'" in capsys.readouterr().out


def test_an_unknown_preset_is_refused_by_the_argument_parser():
    with pytest.raises(SystemExit):
        main(["SBIN", "--preset", "magic"], factory=lambda years: world())
