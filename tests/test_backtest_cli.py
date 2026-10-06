import pytest
from bar_factory import NOW, make_bars

from athena.backtest.cli import BacktestWorld, analyze, main, run_rules
from athena.backtest.engine import Config
from athena.backtest.rules import RULES, Rule
from athena.contracts import AthenaError, Record
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.trading_calendar import ist_date

CLOSES = [100.0 + i * 0.3 + (3 if i % 13 == 0 else 0) - (4 if i % 31 == 0 else 0) for i in range(520)]
BARS = make_bars(CLOSES, symbol="SBIN")


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("SBILIFE", "SBI Life Insurance Company Limited"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def world(fetched=None):
    days = [ist_date(bar.timestamp) for bar in BARS]
    rate = {day: 100.0 * 1.0002 ** i for i, day in enumerate(days)}
    index = {day: 1000.0 + i for i, day in enumerate(days)}

    def fetch_bars(symbol):
        if fetched is not None:
            fetched.append(symbol)
        return BARS

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
