from dash_fakes import BARS, RISK, TECHNICAL, ambiguous_result, full_view, ok_result

from athena.dashboard.view import UNMAPPED_ETF_NOTE, build_view
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
