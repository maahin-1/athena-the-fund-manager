import copy
import json
import pytest

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

from fund_fixtures import ACME, NOW, PRICE, QUARTERS, annual, by, index_history


def packet(payload=ACME, price=PRICE, history=None, **overrides):
    return build_fundamentals_packet("ACME", NOW, payload, price, NOW, history if history is not None else index_history(), **overrides)


def value(p, name):
    return p["metrics"][name]["value"]


def test_valuation_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "last_price") == PRICE and value(p, "market_cap") == pytest.approx(2662.0)
    assert value(p, "pe_trailing") == pytest.approx(20.0, abs=1e-3)  # 266.2 over 3.0 + 3.2 + 3.4 + 3.71
    assert value(p, "pb") == pytest.approx(3.3275, abs=1e-3)  # book value 80 per share
    assert value(p, "earnings_yield") == pytest.approx(0.05, abs=1e-4)
    assert value(p, "fcf_yield") == pytest.approx(100 / 2662, abs=1e-4)  # (160 - 60) over market cap
    assert value(p, "owner_earnings_yield") == pytest.approx(113.1 / 2662, abs=1e-4)  # 133.1 + 40 - 60
    assert value(p, "peg") == pytest.approx(2.0, abs=5e-3)  # P/E 20 over 10 percent growth
    assert value(p, "graham_number_premium") == pytest.approx(266.2 / (22.5 * 13.31 * 80) ** 0.5 - 1, abs=1e-4)
    assert value(p, "dividend_yield") == pytest.approx(5 / 266.2, abs=1e-4)


def test_index_context_compares_the_stock_with_the_nifty_50():
    p = packet()
    assert value(p, "index_pe") == 19.3 and value(p, "pe_vs_index") == pytest.approx(20 / 19.3, abs=1e-3)
    assert value(p, "index_pe_percentile") == pytest.approx(100 * 150 / 300, abs=0.01)  # 149 days at 18.0 plus itself, below the 150 days at 21.0


def test_quality_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "roe_latest") == pytest.approx(133.1 / 750, abs=1e-4)  # average of 800 and 700
    assert value(p, "roe_minimum") == pytest.approx(133.1 / 750, abs=1e-4)
    assert 0.17 < value(p, "roe_average") < 0.21
    assert value(p, "net_margin_latest") == pytest.approx(0.1, abs=1e-6)
    assert value(p, "operating_margin_latest") == pytest.approx(0.15, abs=1e-6)
    assert value(p, "operating_margin_change") == pytest.approx(2.0, abs=1e-6)  # 13 percent to 15 percent
    assert value(p, "gross_margin_latest") == pytest.approx(0.4, abs=1e-6)
    assert value(p, "revenue_cagr") == pytest.approx(0.1, abs=2e-3) and value(p, "earnings_cagr") == pytest.approx(0.1, abs=2e-3)
    assert value(p, "debt_to_equity") == pytest.approx(0.25) and value(p, "current_ratio") == pytest.approx(2.0)
    assert value(p, "interest_coverage") == pytest.approx(199.65 / 20, abs=1e-3)
    assert value(p, "asset_turnover") == pytest.approx(1331 / 1250, abs=1e-3)


def test_earnings_figures_match_a_hand_calculation():
    p = packet()
    assert value(p, "accruals_ratio") == pytest.approx((133.1 - 160) / 1250, abs=1e-4)
    assert value(p, "cash_conversion") == pytest.approx(160 / 133.1, abs=1e-4)
    assert value(p, "revenue_growth_yoy_quarter") == pytest.approx(0.15) and value(p, "net_income_growth_yoy_quarter") == pytest.approx(0.2)
    assert value(p, "eps_surprise_last") == pytest.approx(0.06) and value(p, "beats_last_4") == 3
    assert value(p, "eps_surprise_average_4") == pytest.approx((6.0 + 3.03 - 3.03 + 3.45) / 400, abs=1e-4)
    assert value(p, "days_to_next_earnings") == 15


def test_a_complete_company_has_nothing_missing_and_every_metric_names_its_group_unit_inputs_and_window():
    p = packet()
    assert p["missing"] == [] and p["missing_reasons"] == {}
    for name, metric in p["metrics"].items():
        assert metric["group"] in ("valuation", "quality", "earnings") and metric["unit"] and metric["inputs"] and metric["window"], name
    assert p["latest_annual_period"] == "2026-03-31" and p["latest_quarter"] == "2026-06-30" and p["is_lender"] is False


def test_the_packet_is_json_ready():
    json.dumps(packet())


def test_a_lender_gets_no_cash_flow_or_working_capital_ratios_with_the_reason_stated():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    for line in ("Operating Income", "EBIT", "Gross Profit", "Interest Expense"):
        del bank["annual"]["income"][line]
    for line in ("Current Assets", "Current Liabilities"):
        del bank["annual"]["balance"][line]
    p = packet(bank)
    assert p["is_lender"] is True
    for name in ("fcf_yield", "owner_earnings_yield", "operating_margin_latest", "operating_margin_change", "gross_margin_latest",
                 "debt_to_equity", "current_ratio", "interest_coverage", "asset_turnover", "accruals_ratio", "cash_conversion"):
        assert p["missing_reasons"][name] == LENDER_REASON, name
    for name in ("pe_trailing", "pb", "graham_number_premium", "roe_latest", "net_margin_latest", "revenue_cagr", "eps_surprise_last", "peg"):
        assert name in p["metrics"], name


def test_without_a_price_every_price_based_figure_is_missing_but_fundamentals_still_compute():
    p = packet(price=None)
    for name in ("last_price", "market_cap", "pe_trailing", "pb", "fcf_yield", "peg", "graham_number_premium", "dividend_yield"):
        assert p["missing_reasons"][name] == "no current price", name
    assert "roe_latest" in p["metrics"] and "revenue_cagr" in p["metrics"] and "eps_surprise_last" in p["metrics"]


def test_losses_and_stalled_growth_remove_the_ratios_that_would_mislead():
    losing = copy.deepcopy(ACME)
    losing["quarterly"]["income"]["Diluted EPS"] = by(QUARTERS, [-1, -1, -1, -1, -1], 1)
    p = packet(losing)
    assert p["missing_reasons"]["pe_trailing"] == "earnings per share is not positive"
    assert "graham_number_premium" in p["missing"] and "earnings_yield" in p["missing"]
    flat = copy.deepcopy(ACME)
    flat["annual"]["income"]["Net Income"] = annual([100, 100, 100, 100])
    assert packet(flat)["missing_reasons"]["peg"] == "earnings are not growing"


def test_too_few_fiscal_years_remove_the_growth_and_persistence_figures():
    short = copy.deepcopy(ACME)
    for group in short["annual"].values():
        for line, series in group.items():
            group[line] = {period: v for period, v in series.items() if period >= "2025-03-31"}
    p = packet(short)
    for name in ("revenue_cagr", "earnings_cagr", "peg", "roe_average", "roe_minimum"):
        assert name in p["missing"], name
    assert "at least 3 annual periods" in p["missing_reasons"]["revenue_cagr"]
    assert "roe_latest" in p["metrics"]


def test_a_gap_in_the_quarters_falls_back_to_annual_eps_and_says_so():
    gappy = copy.deepcopy(ACME)
    gappy["quarterly"]["income"]["Diluted EPS"]["2025-12-31"] = None
    p = packet(gappy)
    assert value(p, "pe_trailing") == pytest.approx(PRICE / 13.31, abs=1e-3)
    assert "annual EPS used" in p["metrics"]["pe_trailing"]["note"]
    assert "fiscal year to 2026-03-31" in p["metrics"]["pe_trailing"]["window"]


def test_quarter_growth_needs_the_same_quarter_a_year_earlier():
    no_base = copy.deepcopy(ACME)
    del no_base["quarterly"]["income"]["Total Revenue"]["2025-06-30"]
    p = packet(no_base)
    assert p["missing_reasons"]["revenue_growth_yoy_quarter"] == "the same quarter a year earlier is not available"
    assert "net_income_growth_yoy_quarter" in p["metrics"]


def test_a_short_index_history_keeps_the_index_pe_but_drops_the_percentile():
    short = {d: row for d, row in list(index_history().items())[:50]}
    p = packet(history=short)
    assert "index_pe" in p["metrics"] and "index_pe_percentile" in p["missing"]
    assert "pe_vs_index" in p["metrics"]


def test_no_upcoming_date_and_no_dividend_are_reported_not_invented():
    quiet = copy.deepcopy(ACME)
    quiet["earnings_dates"] = quiet["earnings_dates"][1:]
    quiet["info"]["dividendRate"] = None
    p = packet(quiet)
    assert p["missing_reasons"]["days_to_next_earnings"] == "no upcoming earnings date"
    assert p["missing_reasons"]["dividend_yield"] == "no dividend reported"


def test_an_empty_payload_is_all_missing_not_an_error_and_flags_travel_with_the_packet():
    empty = packet({}, history={})
    assert empty["metrics"].keys() == {"last_price"} and len(empty["missing"]) > 20
    flagged = copy.deepcopy(ACME)
    flagged["quality_flags"] = ["quarterly periods x, y carry identical figures (possible duplicate); treated as missing"]
    assert packet(flagged)["data_quality_flags"] == flagged["quality_flags"]
