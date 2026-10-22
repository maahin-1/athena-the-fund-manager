import pytest

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

from fund_fixtures import ACME, NOW, PRICE, index_history

STEEL = {
    "industry": "Steel",
    "mapping_method": "nse_ticker",
    "mapping_confidence": 1.0,
    "n_firms": 185,
    "data_as_of": "2026-01-05",
    "values": {"pe": 25.0, "pb": 2.5, "roe": 0.09, "operating_margin": 0.0918},
    "flags": [],
}


def packet(industry=STEEL, payload=ACME):
    return build_fundamentals_packet("ACME", NOW, payload, PRICE, NOW, index_history(), industry=industry)


def test_the_stock_is_set_against_its_industry_in_code_with_the_source_named():
    p = packet()
    m = p["metrics"]
    assert m["industry_pe"]["value"] == 25.0
    assert m["pe_vs_industry"]["value"] == pytest.approx(20.0 / 25.0, abs=1e-3)  # the stock trades at a P/E of 20.0
    assert m["pb_vs_industry"]["value"] == pytest.approx(3.3275 / 2.5, abs=1e-3)
    assert m["roe_vs_industry"]["value"] == pytest.approx(133.1 / 750 - 0.09, abs=1e-4)  # a gap in fractions, not a ratio
    assert m["operating_margin_vs_industry"]["unit"] == "fraction"
    assert "Damodaran India industry table for Steel (185 firms, dated 2026-01-05)" in m["pe_vs_industry"]["source"]
    assert p["industry"]["industry"] == "Steel" and p["industry"]["n_firms"] == 185


def test_a_packet_without_industry_figures_is_unchanged():
    p = packet(industry=None)
    assert p["industry"] is None and "pe_vs_industry" not in p["metrics"] and "pe_vs_industry" not in p["missing"]


def test_a_missing_industry_figure_is_listed_with_the_reason_not_dropped():
    thin = dict(STEEL, values={"pe": 25.0})
    p = packet(industry=thin)
    assert "pe_vs_industry" in p["metrics"] and "pb_vs_industry" not in p["metrics"]
    assert "the industry table gives no pb for Steel" in p["missing_reasons"]["pb_vs_industry"]


def test_a_non_positive_industry_multiple_gives_no_ratio():
    p = packet(industry=dict(STEEL, values={"pe": -3.0, "pb": 2.5}))
    assert "pe_vs_industry" not in p["metrics"] and p["missing_reasons"]["pe_vs_industry"] == "the industry figure is not positive"
