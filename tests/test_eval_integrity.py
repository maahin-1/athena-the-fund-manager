import json
from datetime import date

from athena.evaluation import pit_run
from athena.fundamentals import annual_reports as ar
from athena.fundamentals import pdf_extract as pe


# ---- a figure the model copied must be printed on the pages
def test_numbers_printed_on_the_pages_are_read_with_commas_and_brackets_taken_out():
    printed = pe.printed_numbers({"income": "Revenue 1,23,104.50 (2,610) 39,310 Profit 8.4"})
    assert {123104.5, 2610.0, 39310.0, 8.4} <= printed


def test_a_figure_not_on_the_pages_is_left_out_and_named_while_printed_ones_and_brackets_stay():
    blocks = {"income": "Sales 1,000 900 Tax (60) 60", "balance": "Total assets 1,600"}
    raw = {"unit": "crore", "years": ["2019-03-31", "2018-03-31"], "income": {"sales": [1000, 901], "tax": [-60, 60]}, "balance": {"total_assets": [1600, 1599]}}
    kept, dropped = pe.drop_unprinted(raw, blocks)
    assert kept["income"]["sales"] == [1000, None] and kept["income"]["tax"] == [-60, 60] and kept["balance"]["total_assets"] == [1600, None]
    assert sorted(dropped) == ["balance.total_assets=1599", "income.sales=901"]
    assert raw["income"]["sales"] == [1000, 901]  # the reply itself is not changed
    years = pe.normalise(kept)
    assert "sales" not in years[date(2018, 3, 31)]["income"] and years[date(2019, 3, 31)]["income"]["sales"] == 1000 * 1e7


def test_a_part_of_the_assets_larger_than_all_of_them_is_an_internal_problem():
    year = {"income": {}, "balance": {"total_assets": 100.0, "cash": 150.0, "investments": 50.0}}
    assert pe.internal_checks(year) == ["cash is larger than total assets"]
    assert pe.internal_checks({"income": {}, "balance": {"total_assets": 100.0, "cash": 100.0}}) == []


def test_the_chain_records_which_figures_were_dropped(tmp_path):
    from datetime import datetime

    page = "Consolidated Statement of Profit and Loss Total expenses Profit before tax " + " ".join(str(1000 + i) for i in range(40)) + " 900 20 800 30 200 60 138 100 300 1600"
    reply = json.dumps({"unit": "crore", "years": ["2019-03-31", "2018-03-31"], "income": {"sales": [1000, 999999], "other_income": [20, 20], "total_expenses": [800, 800], "interest": [30, 30], "pbt": [200, 200], "tax": [60, 60], "net_profit": [138, 138]}, "balance": {"equity_capital": [100, 100], "reserves": [900, 900], "borrowings": [300, 300], "total_assets": [1600, 1600]}})

    class Model:
        def complete(self, system, user):
            return reply

    ref = {date(2019, 3, 31): {"income": {"sales": 1000e7, "other_income": 20e7, "total_expenses": 800e7, "interest": 30e7, "pbt": 200e7, "tax": 60e7, "net_profit": 138e7}, "balance": {"equity_capital": 100e7, "reserves": 900e7, "borrowings": 300e7, "total_assets": 1600e7}}}
    out = ar.chain([{"title": "FY2018-19", "url": "u", "published": datetime(2019, 3, 30)}], ref, lambda url: [page, "Balance Sheet EQUITY AND LIABILITIES Total assets " + page], Model(), back_to=2019)
    assert out["steps"][0]["dropped"] == ["income.sales=999999"]


# ---- the evaluation harness
class FakeCase:
    def __init__(self):
        self.symbol, self.as_of, self.blinded, self.statements_source, self.notes = "TCS", date(2026, 3, 31), False, "test", ()
        self.packets = {"technical": {"metrics": {"a": 1}}}

        class Outcome:
            label, excess = "up", 0.05

        self.outcomes = {20: Outcome()}


class FakeBuilder:
    def build(self, symbol, day, blind):
        return FakeCase()


class Analyst:
    """Takes `run`, like the live analyst, and says which model answered."""

    def __init__(self):
        self.runs, self.last_source = [], ""

    def __call__(self, packet, run=0):
        self.runs.append(run)
        self.last_source = f"nvidia:model-{run}"
        return {"signal": "bullish", "confidence": 60, "data_coverage": "full", "reasoning": "x", "missing": []}


def test_every_row_carries_the_packet_hash_and_the_model_that_answered_and_each_repeat_is_asked_with_its_run_number(tmp_path):
    analyst = Analyst()
    out = tmp_path / "run.jsonl"
    made = pit_run.run_cases(FakeBuilder(), {"quant_technical": analyst}, ["TCS"], [date(2026, 3, 31)], out, repeats=3, packet_for={"quant_technical": "technical"})
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert made == 3 and analyst.runs == [0, 1, 2]
    assert [r["model"] for r in rows] == ["nvidia:model-0", "nvidia:model-1", "nvidia:model-2"]
    assert len({r["packet_hash"] for r in rows}) == 1 and len(rows[0]["packet_hash"]) == 16  # the same evidence each time, and the row says so
    assert pit_run.packet_hash({"b": 1, "a": 2}) == pit_run.packet_hash({"a": 2, "b": 1}) != pit_run.packet_hash({"a": 2})


def test_an_analyst_that_does_not_take_a_run_is_still_called_as_before(tmp_path):
    def old(packet):
        return {"signal": "neutral", "confidence": 10, "data_coverage": "full", "reasoning": "x", "missing": []}

    out = tmp_path / "run.jsonl"
    assert pit_run.run_cases(FakeBuilder(), {"quant_technical": old}, ["TCS"], [date(2026, 3, 31)], out, repeats=2, packet_for={"quant_technical": "technical"}) == 2
    assert all(json.loads(line)["model"] == "" for line in out.read_text().splitlines())


def test_a_live_analyst_answers_the_first_run_from_its_own_client_and_every_repeat_from_a_fresh_one_with_a_new_specialist():
    from athena.agents.base import SpecialistSpec

    class Llm:
        def __init__(self, name):
            self.name, self.asked, self.last_source = name, 0, name

        def complete(self, system, user):
            self.asked += 1
            return json.dumps({"signal": "bullish", "confidence": 50, "reasoning": "x"})

    cached, fresh = Llm("cached"), Llm("fresh")
    made = []
    analyst = pit_run.LiveAnalyst(SpecialistSpec(name="T", persona="p", critical=("a",)), cached, lambda: made.append(1) or fresh)
    packet = {"metrics": {"a": {"value": 1, "unit": "x"}}}
    analyst(packet, run=0)
    analyst(packet, run=1)
    analyst(packet, run=2)  # the same packet again: a new specialist has no in-memory answer to hand back
    assert cached.asked == 1 and fresh.asked == 2 and made == [1] and analyst.last_source == "fresh"
