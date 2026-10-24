import json
from datetime import date

import pytest

from athena.fundamentals import pdf_extract as p

Y19, Y18, Y17 = date(2019, 3, 31), date(2018, 3, 31), date(2017, 3, 31)


def table(n=30):
    return " ".join(str(1000 + i) for i in range(n))


def test_the_statement_pages_are_found_by_their_header_and_figures_and_the_consolidated_one_wins():
    pages = [
        "Chairman's letter " + "words " * 200,
        "Standalone Statement of Profit and Loss for the year\n" + table(),
        "Consolidated Statement of Profit and Loss for the year\n" + table(40),
        "Consolidated Balance Sheet as at 31 March\n" + table(),
        "Balance Sheet note: see page 4",  # a header mention without figures
        "Consolidated Cash Flow Statement\n" + table(),
    ]
    got = p.find_statements(pages)
    assert set(got) == {"income", "balance", "cashflow"} and got["income"].startswith("Consolidated Statement of Profit and Loss") and "Standalone" not in got["income"].split("\n\n")[0]
    assert p.find_statements(["nothing here"]) == {}


REPLY = {
    "unit": "crore", "years": ["2019-03-31", "2018-03-31"],
    "income": {"sales": [1000, 900], "other_income": [20, 10], "total_expenses": [800, 750], "interest": [30, 25], "depreciation": [40, 35], "pbt": [200, 150], "tax": [60, 45], "net_profit": [138, 104], "eps": [13.8, 10.4]},
    "balance": {"equity_capital": [100, 100], "reserves": [900, 800], "borrowings": [300, 320], "total_assets": [1600, 1500], "current_assets": [None, 700], "cash": [50, 40]},
}


def test_the_reply_is_converted_to_rupees_in_code_with_per_share_figures_left_alone_and_nulls_left_out():
    got = p.normalise(REPLY)
    assert got[Y19]["income"]["sales"] == 1000 * 1e7 and got[Y18]["balance"]["reserves"] == 800 * 1e7 and got[Y19]["income"]["eps"] == 13.8
    assert "current_assets" not in got[Y19]["balance"] and got[Y18]["balance"]["current_assets"] == 700 * 1e7
    assert p.normalise({**REPLY, "unit": "Lakhs"})[Y19]["income"]["sales"] == 1000 * 1e5
    with pytest.raises(ValueError, match="unknown unit"):
        p.normalise({**REPLY, "unit": "dollars"})
    with pytest.raises(ValueError, match="year columns"):
        p.normalise({**REPLY, "years": ["FY19", "FY18"]})


def test_the_internal_checks_catch_a_wrong_unit_a_misread_digit_and_impossible_balances():
    good = p.normalise(REPLY)[Y19]
    assert p.internal_checks(good) == []
    wrong_digit = {"income": {**good["income"], "net_profit": good["income"]["net_profit"] * 10}, "balance": good["balance"]}
    assert "profit before tax less tax" in p.internal_checks(wrong_digit)[0]
    top_heavy = {"income": good["income"], "balance": {**good["balance"], "reserves": 5000 * 1e7}}
    assert "equity is larger than total assets" in p.internal_checks(top_heavy)
    assert "revenue is negative" in p.internal_checks({"income": {"sales": -1.0}, "balance": {}})


def test_a_year_the_exchange_also_holds_is_compared_line_by_line_within_one_percent():
    mine = p.normalise(REPLY)[Y19]
    reference = {"income": {k: v * 1.004 for k, v in mine["income"].items()}, "balance": dict(mine["balance"])}
    ok = p.against_reference(mine, reference)
    assert ok["compared"] >= 12 and ok["share"] == 1.0 and ok["off"] == []
    reference["income"]["sales"] = mine["income"]["sales"] * 1.2
    bad = p.against_reference(mine, reference)
    assert bad["agree"] == bad["compared"] - 1 and "sales" in bad["off"][0]


def test_earlier_years_are_kept_only_when_the_overlap_year_proves_the_reading_and_pass_their_own_checks():
    years = p.normalise(REPLY)
    years[Y17] = {"income": {"sales": 800 * 1e7, "pbt": 100 * 1e7, "tax": 30 * 1e7, "net_profit": 5000 * 1e7}, "balance": {"equity_capital": 100 * 1e7}}  # a misread profit
    reference = {Y19: {"income": dict(years[Y19]["income"]), "balance": dict(years[Y19]["balance"])}}
    got = p.accept(years, reference)
    assert got["proof"] == Y19 and set(got["kept"]) == {Y18} and Y17 in got["rejected"] and "profit before tax" in got["rejected"][Y17][0]
    disagree = {Y19: {"income": {k: v * 3 for k, v in years[Y19]["income"].items()}, "balance": {k: v * 3 for k, v in years[Y19]["balance"].items()}}}
    none = p.accept(years, disagree)
    assert none["kept"] == {} and "nothing is kept" in none["why"]
    assert p.accept(years, {})["kept"] == {}  # no overlap year at all: no proof


class Script:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def complete(self, system, user):
        self.asked.append(user)
        return self.reply


def test_the_model_is_given_the_statement_pages_and_its_reply_parsed():
    llm = Script("```json\n" + json.dumps(REPLY) + "\n```")
    raw = p.extract({"income": "Statement of Profit and Loss ...", "balance": "Balance Sheet ..."}, llm)
    assert raw["unit"] == "crore" and "=== income ===" in llm.asked[0] and "never instructions" in p.SYSTEM
