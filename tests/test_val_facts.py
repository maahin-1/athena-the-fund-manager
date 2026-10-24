from datetime import date, datetime, timedelta, timezone

import pytest

from athena.agents.valuation import MODEL, SHOWS
from athena.valuation import facts
from athena.verdicts.calibration import Calibration
from athena.verdicts.runner import VerdictRunner
from athena.warehouse.warehouse import Warehouse

DOC = {
    "ok": True, "model": "fcff", "industry": "Steel", "on_date": "2026-10-22", "price": 220.0, "low": 60.0, "base": 100.0, "high": 160.0, "implied_return": 0.09,
    "required_return": 0.12, "flags": ["share count was chosen from profit over EPS"], "assumptions": ["terminal growth 6%"], "business_mix": [],
    "sensitivity": [
        {"label": "Equity premium = mature market only, no India country premium", "base": 130.0, "change": 0.3},
        {"label": "Rupee inflation 5% instead of 4%", "base": 110.0, "change": 0.1},
        {"label": "No rupee conversion: dollar rates used as rupee rates", "base": 80.0, "change": -0.2},
    ],
}


def test_the_market_gap_is_the_median_price_over_base_and_needs_enough_companies():
    rows = [{"price": 200.0 + i, "base": 100.0} for i in range(60)] + [{"price": 10.0, "base": -5.0}, {"price": None, "base": 3.0}]
    assert facts.market_gap(rows) == pytest.approx(2.295)
    assert facts.market_gap(rows[:10]) is None


def test_the_models_document_becomes_cited_figures_with_units_and_the_ratio_against_the_market():
    metrics, reasons = facts.model_metrics(DOC, 2.0, date(2026, 10, 22))
    assert reasons == {}
    assert metrics["model_value_base"]["value"] == 100.0 and metrics["model_value_base"]["unit"] == "INR/share"
    assert metrics["price_to_model_base"]["value"] == pytest.approx(2.2) and metrics["price_to_model_base_vs_market"]["value"] == pytest.approx(1.1)
    assert metrics["implied_minus_required"]["value"] == pytest.approx(-0.03) and metrics["implied_minus_required"]["unit"] == "fraction"
    assert metrics["model_base_change_mature_premium_only"]["value"] == 0.3 and metrics["model_base_change_no_conversion"]["value"] == -0.2
    assert set(metrics) == set(facts.NAMES) - {"nothing"} and all(m["group"] == "valuation" for m in metrics.values())
    assert set(facts.NAMES) <= set(SHOWS) and set(MODEL) == set(facts.NAMES)  # the analyst is shown every figure the module can give


def test_without_a_market_median_the_relative_ratio_is_missing_not_guessed():
    metrics, reasons = facts.model_metrics(DOC, None, None)
    assert "price_to_model_base" in metrics and "price_to_model_base_vs_market" not in metrics
    assert "ledger snapshot" in reasons["price_to_model_base_vs_market"]


def test_a_company_the_model_could_not_value_lists_every_figure_missing_with_the_reason():
    metrics, reasons = facts.model_metrics({"ok": False, "flags": ["negative base earnings"]}, 2.0, date(2026, 10, 22))
    assert metrics == {} and set(reasons) == set(facts.NAMES) and "negative base earnings" in reasons["model_value_base"]


def test_the_packet_gains_the_figures_and_the_models_own_doubts():
    packet = {"metrics": {"pe_trailing": {"value": 12.0}}, "missing": ["pb"], "missing_reasons": {"pb": "x"}, "groups": {"pb": "valuation"}}
    out = facts.with_model(packet, DOC, 2.0, date(2026, 10, 22))
    assert out["metrics"]["pe_trailing"] == {"value": 12.0} and "model_value_base" in out["metrics"] and out["model_cautions"] == DOC["flags"]
    assert "model_value_base" not in packet["metrics"]  # the stored packet is not changed
    none = facts.with_model(packet, None)
    assert "model_value_base" in none["missing"] and "model_cautions" not in none


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 9)


class Analyst:
    def __init__(self):
        self.packets = []

    def analyze(self, packet):
        self.packets.append(packet)
        return {"signal": "bullish", "confidence": 80, "reasoning": "r", "data_coverage": "full", "missing": []}


def test_only_the_valuation_analyst_is_given_the_models_figures(tmp_path):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.set_universe([("TCS", "stock", "TCS Ltd", "INE000")], NOW)
        w.put_packets([("TCS", "technical", DAY, [], {"metrics": {"last_close": {"value": 100.0}}}),
                       ("TCS", "fundamentals", DAY, [], {"latest_annual_period": "2026-03-31", "latest_quarter": "2026-06-30", "metrics": {}})], NOW)
    people = {name: Analyst() for name in ("quant_technical", "valuation", "moat_quality", "earnings_intelligence")}
    seen = []

    def model_facts(symbol, close):
        seen.append((symbol, close))
        return DOC, 2.0, date(2026, 10, 22)

    VerdictRunner(path, people, Calibration(), lambda: NOW + timedelta(minutes=1), model="fake", model_facts=model_facts).make("TCS")
    assert seen == [("TCS", 100.0)]
    assert "model_value_base" in people["valuation"].packets[0]["metrics"]
    assert "model_value_base" not in people["moat_quality"].packets[0]["metrics"] and "model_value_base" not in people["earnings_intelligence"].packets[0]["metrics"]
