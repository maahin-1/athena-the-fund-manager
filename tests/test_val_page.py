import json
from datetime import date, datetime, timezone
from pathlib import Path

from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore
from athena.reference.store import ReferenceStore, StatRow
from athena.valuation import page

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
DAY = date(2026, 10, 22)


def stores():
    fund = FundamentalsStore(":memory:")
    statements = parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))
    fund.save_statements(statements, "u0", datetime(2024, 4, 12, tzinfo=timezone.utc))
    fund.save_company("TCS", "Tata Consultancy Services", "INE467B01029", "IT", statements.layout, statements.shares)
    ref = ReferenceStore(":memory:")
    for key, value in (("us_10y_yield", 0.042), ("tax_marginal", 0.25), ("inflation_inr", 0.04), ("inflation_usd", 0.02), ("erp_mature", 0.042), ("large_firm_cap_crore", 40000.0)):
        ref.put_input(key, date(2026, 4, 1), value, "test")
    ref.put_country_premiums([("India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272, date(2026, 7, 1)), ("United States", "North America", "Aa1", 0.0022, 0.0442, 0.0022, date(2026, 7, 1))])
    rows = [
        ("beta", "unlevered_beta_corrected_for_cash", 0.74), ("beta", "beta", 0.9), ("margin", "pre_tax_unadjusted_operating_margin", 0.17), ("histgr", "expected_growth_in_revenues_next_5_years", 0.08),
        ("capex", "sales_invested_capital_ltm", 2.5), ("roe", "roe_unadjusted", 0.27),
    ]
    ref.put_stats([StatRow("damodaran", "India", d, "Computer Services", m, v, 196, date(2026, 1, 5), f"{d}India.xls") for d, m, v in rows])
    ref.put_company_industries([("TCS", "Computer Services", "nse_ticker", 1.0, date(2026, 1, 5))])
    return fund, ref


def test_the_document_has_the_range_the_cost_of_capital_and_the_assumptions_and_is_plain_json():
    fund, ref = stores()
    with fund, ref:
        doc = page.build_valuation(fund, ref, "TCS", "consolidated", 3200.0, "warehouse close", DAY)
    assert doc["ok"] and doc["model"] == "fcff" and doc["industry"] == "Computer Services"
    assert doc["low"] <= doc["base"] <= doc["high"] and doc["price"] == 3200.0
    assert [s["name"] for s in doc["scenarios"]] == ["bear", "base", "bull"]
    assert doc["cost_of_capital"]["low"] < doc["cost_of_capital"]["inr"]["cost_of_capital"] < doc["cost_of_capital"]["high"]
    assert len(doc["grid"]["values"]) == 5 and doc["assumptions"] and doc["flags"]
    json.dumps(doc, allow_nan=False)  # no NaN or infinity reaches the page


def test_a_company_that_cannot_be_valued_gets_the_reasons_and_an_unknown_symbol_gets_an_error():
    fund, ref = stores()
    with fund, ref:
        no_price = page.build_valuation(fund, ref, "TCS", "consolidated", None, "", DAY)
        unknown = page.build_valuation(fund, ref, "NOPE", "consolidated", 10.0, "", DAY)
    assert not no_price["ok"] and no_price["missing"] and "low" not in no_price
    assert unknown["error"] == "no statements stored"


def test_nan_and_infinity_become_null():
    assert page._clean({"a": float("nan"), "b": [1.0, float("inf")], "c": (2.0,)}) == {"a": None, "b": [1.0, None], "c": [2.0]}


def test_the_document_carries_the_return_the_price_implies_beside_the_return_the_model_asks_for_and_the_value_under_the_biggest_alternative_inputs():
    fund, ref = stores()
    with fund, ref:
        doc = page.build_valuation(fund, ref, "TCS", "consolidated", 3200.0, "warehouse close", DAY)
    assert doc["implied_return"] is not None and doc["required_return"] == doc["scenarios"][1]["cost"]
    labels = [s["label"] for s in doc["sensitivity"]]
    assert labels[0].startswith("Equity premium = mature market only") and "Rupee inflation 5% instead of 4%" in labels[1] and labels[2].startswith("No rupee conversion")
    by = {s["label"].split(":")[0].split(" =")[0]: s for s in doc["sensitivity"]}
    assert all(s["base"] > 0 for s in doc["sensitivity"])
    mature, higher_inflation, none = doc["sensitivity"]
    assert mature["change"] > 0 and none["change"] > 0 and higher_inflation["change"] < 0  # a smaller premium and no conversion raise the value; more inflation lowers it
    assert none["change"] > mature["change"] and by
