import json
from dataclasses import replace

import pytest
from bar_factory import NOW, make_bars

from athena.backtest.cli import BacktestWorld, analyze, assumptions, format_runs, main, run_rules
from athena.backtest.engine import Config
from athena.backtest.rules import RULES, Rule
from athena.contracts import AthenaError, Record
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import CheckedHistory
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


def test_bars_are_replayed_as_given_so_a_halving_nobody_recorded_stays_a_halving():
    market = world()
    true = run_rules(BARS, ["trend"], market.riskfree, market.index)
    unrecorded = run_rules(BONUS_BARS, ["trend"], market.riskfree, market.index)
    assert unrecorded[0].summary.benchmark_total_return < true[0].summary.benchmark_total_return - 0.3  # nothing adjusted it away
    assert not hasattr(unrecorded[0], "adjustments") and unrecorded[0].blinded_identical


def test_bars_the_checked_layer_adjusted_for_a_recorded_bonus_replay_like_the_true_history():
    market = world()
    event = PriceEvent(ist_date(BARS[BREAK].timestamp), 2.0)  # a 1:1 bonus is recorded as a 2-for-1 split
    checked = CheckedHistory(lambda symbol: list(BONUS_BARS), lambda symbol: [event])
    true = run_rules(BARS, ["trend", "persona"], market.riskfree, market.index)
    runs = run_rules(checked("SBIN"), ["trend", "persona"], market.riskfree, market.index)
    for adjusted, real in zip(runs, true):
        assert adjusted.summary.benchmark_total_return == pytest.approx(real.summary.benchmark_total_return)
    assert runs[0].result.trades == () and all(run.blinded_identical for run in runs)


def test_the_report_prints_the_worlds_price_notes_on_the_lines_before_the_blinding_check():
    noted = replace(world(), notes=lambda symbol: [f"note one for {symbol}", "note two"])
    text, _ = analyze(noted, "SBIN", ["trend"])
    lines = text.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("Blinding check"))
    assert lines[at - 2 : at] == ["note one for SBIN", "note two"]
    plain, _ = analyze(world(), "SBIN", ["trend"])
    assert "note one" not in plain and "note one" not in format_runs("SBIN", [])


def test_the_assumptions_state_costs_stop_timing_cash_adjustment_index_and_open_positions():
    text = assumptions(Config())
    for expected in ("15 bps per side", "2 x ATR below the entry fill", "decisions at the close", "fills at the next open",
                     "cash earns nothing", "overnight rate", "penalises time in cash", "splits, bonuses and demergers",
                     "no record explains", "never for dividends", "NIFTY 50 comparison is a price index",
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


def deep_strategy_file(tmp_path, text):
    path = tmp_path / "text.json"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("text", ["[" * 100000, "1" * 5000], ids=["nested", "digits"])
def test_a_strategy_file_json_cannot_parse_is_reported_without_a_traceback(capsys, tmp_path, text):
    assert main(["SBIN", "--strategy", deep_strategy_file(tmp_path, text)], factory=lambda years: world()) == 1
    assert capsys.readouterr().out.startswith("error: ")


def test_a_strategy_file_with_an_oversized_number_is_reported_without_a_traceback(capsys, tmp_path):
    bad = write_strategy(tmp_path, {**MINE, "stop_atr": 10**400})
    assert main(["SBIN", "--strategy", bad], factory=lambda years: world()) == 1
    assert "error: strategy.stop_atr" in capsys.readouterr().out


def test_two_rules_with_the_same_name_are_refused_before_anything_runs(capsys, tmp_path):
    first = write_strategy(tmp_path, MINE)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**MINE, "stop_atr": 3}), encoding="utf-8")
    assert main(["SBIN", "--strategy", first, "--strategy", str(other)], factory=lambda years: world()) == 1
    assert "error: two rules are named 'Mine'; give each a different name" in capsys.readouterr().out
