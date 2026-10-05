import copy

from dash_fakes import BARS, FUNDAMENTALS, RISK, TECHNICAL, ambiguous_result, full_view, ok_result
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.dashboard.view import ETF_FUNDAMENTALS_NOTE, LENDER_NOTE, UNMAPPED_ETF_NOTE, build_view
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW


def test_the_view_carries_the_instrument_verdict_and_specialist_rows():
    view = full_view()
    assert (view.identifier, view.name, view.asset_class, view.match) == ("SBIN", "State Bank of India", "equity", "exact")
    assert view.verdict["verdict"] == "Buy"
    row = view.specialists[0]
    assert (row.name, row.signal, row.confidence, row.coverage) == ("quant_technical", "bullish", 72, "full")
    assert view.skipped == {"valuation": "not built yet"} and "risk overlay missing" in view.notes


def test_each_panel_has_its_own_as_of_source_and_coverage_label():
    technical, risk = full_view().panels
    assert technical.title.startswith("Technical") and risk.title.startswith("Risk")
    assert technical.as_of.endswith("(last close)") and technical.as_of.startswith("2026-10-02")
    assert technical.source == "t" and risk.source.startswith("t, NIFTY 50")
    assert technical.coverage == "full" and risk.coverage == "partial"  # the risk packet is missing tracking_error
    assert risk.missing == {"tracking_error": "required series not provided"}


def test_panel_rows_copy_the_packet_figures_exactly():
    technical = full_view().panels[0]
    by_name = {row.name: row for row in technical.rows}
    assert by_name["last_close"].value == TECHNICAL["metrics"]["last_close"]["value"]
    assert by_name["trend_alignment"].value == "aligned_up"
    assert by_name["reward_risk"].note  # the notes travel with the numbers


def test_charts_are_built_from_the_same_bars():
    view = full_view()
    assert view.price_figure is not None and view.rsi_figure is not None
    assert len(view.price_figure.data[0].x) == len(BARS)
    assert "SBIN" in view.price_figure.layout.title.text and "2026-10-02" in view.price_figure.layout.title.text


def test_no_bars_means_no_charts_and_panels_that_say_no_data():
    view = build_view(ok_result(), (), {"metrics": {}, "missing": ["last_close"], "missing_reasons": {"last_close": "x"}}, None)
    assert view.price_figure is None and view.rsi_figure is None
    panel = view.panels[0]
    assert panel.as_of == "no data" and panel.coverage == "insufficient"


def test_an_etf_without_a_tracking_index_gets_an_explanatory_note():
    assert UNMAPPED_ETF_NOTE in full_view("etf").notes
    assert UNMAPPED_ETF_NOTE not in full_view("equity").notes
    mapped = {**RISK, "missing": [], "missing_reasons": {}}
    assert UNMAPPED_ETF_NOTE not in full_view("etf", risk=mapped).notes


def test_an_ambiguous_result_becomes_a_candidate_list_with_no_charts_or_panels():
    view = build_view(ambiguous_result())
    assert view.status == NEEDS_CLARIFICATION and view.identifier == ""
    assert [(c.identifier, c.asset_class) for c in view.candidates] == [("SBIN", "equity")]
    assert view.panels == () and view.price_figure is None and view.notes == ("several matches",)


def test_a_no_view_result_keeps_its_status():
    view = full_view(status=NO_VIEW)
    assert view.status == NO_VIEW


# ---- fundamentals panels
def test_a_stock_with_fundamentals_gets_three_more_panels_after_technical_and_risk():
    panels = full_view(fundamentals=FUNDAMENTALS).panels
    assert [p.title for p in panels][2:] == ["Valuation", "Business quality", "Earnings"]
    assert all(p.coverage == "full" and not p.missing for p in panels[2:])
    assert panels[2].as_of == "annual to 2026-03-31, quarter to 2026-06-30" and "NSE index valuation" in panels[2].source


def test_each_fundamentals_panel_holds_only_its_own_group_and_copies_the_packet_figures():
    valuation, quality, earnings = full_view(fundamentals=FUNDAMENTALS).panels[2:]
    names = {p.title: {row.name for row in p.rows} for p in (valuation, quality, earnings)}
    assert {"pe_trailing", "pb", "fcf_yield", "peg", "index_pe"} <= names["Valuation"]
    assert {"roe_latest", "net_margin_latest", "revenue_cagr", "debt_to_equity"} <= names["Business quality"]
    assert {"accruals_ratio", "eps_surprise_last", "days_to_next_earnings"} <= names["Earnings"]
    assert not (names["Valuation"] & names["Business quality"]) and not (names["Business quality"] & names["Earnings"])
    pe = next(row for row in valuation.rows if row.name == "pe_trailing")
    assert pe.value == FUNDAMENTALS["metrics"]["pe_trailing"]["value"] and pe.unit == "ratio" and pe.window


def test_a_group_with_missing_figures_is_partial_and_lists_why():
    thin = copy.deepcopy(ACME)
    thin["earnings_dates"] = []
    packet = build_fundamentals_packet("SBIN", NOW, thin, PRICE, NOW, index_history())
    earnings = full_view(fundamentals=packet).panels[4]
    assert earnings.coverage == "partial" and earnings.missing["eps_surprise_last"] == "no reported earnings with a surprise figure"
    assert full_view(fundamentals=packet).panels[2].coverage == "full"


def test_a_group_with_no_figures_at_all_is_insufficient():
    empty = build_fundamentals_packet("SBIN", NOW, {}, None, NOW, {})
    assert [p.coverage for p in full_view(fundamentals=empty).panels[2:]] == ["insufficient"] * 3


def test_data_quality_flags_and_the_lender_note_reach_the_notes():
    flagged = copy.deepcopy(ACME)
    flagged["quality_flags"] = ["quarterly periods a, b carry identical figures (possible duplicate); treated as missing"]
    flagged["info"]["sector"] = "Financial Services"
    notes = full_view(fundamentals=build_fundamentals_packet("SBIN", NOW, flagged, PRICE, NOW, index_history())).notes
    assert flagged["quality_flags"][0] in notes and LENDER_NOTE in notes
    assert LENDER_NOTE not in full_view(fundamentals=FUNDAMENTALS).notes


def test_an_etf_gets_no_fundamentals_panels_and_a_note_and_a_failure_note_is_passed_through():
    etf = full_view("etf")
    assert [p.title.split()[0] for p in etf.panels] == ["Technical", "Risk"] and ETF_FUNDAMENTALS_NOTE in etf.notes
    failed = full_view(fundamentals_note="fundamentals unavailable: no statements for 'X'")
    assert "fundamentals unavailable: no statements for 'X'" in failed.notes and len(failed.panels) == 2
