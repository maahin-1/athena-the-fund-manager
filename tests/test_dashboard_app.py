import dash_fakes
from dash_fakes import FakeService, ambiguous_result, athena_error, full_view
from streamlit.testing.v1 import AppTest

from athena.dashboard.view import build_view
from athena.orchestrator.orchestrator import NO_VIEW
from athena.orchestrator.report import DISCLAIMER


def script():
    import dash_fakes

    from athena.dashboard.app import run

    run(dash_fakes.CURRENT["service"])


def open_app(service, query=None):
    dash_fakes.CURRENT["service"] = service
    app = AppTest.from_function(script, default_timeout=30).run()
    if query is not None:
        app.text_input[0].set_value(query).run()
    return app


def texts(elements):
    return [element.value for element in elements]


def test_an_empty_box_shows_a_prompt_and_calls_nothing():
    service = FakeService(full_view())
    app = open_app(service)
    assert not app.exception and app.title[0].value == "Athena"
    assert "Type an NSE ticker" in app.info[0].value and service.queries == []


def test_a_query_is_passed_to_the_service_trimmed():
    service = FakeService(full_view())
    open_app(service, "  sbin ")
    assert service.queries == ["sbin"]


def test_the_page_shows_instrument_verdict_conviction_and_specialists():
    app = open_app(FakeService(full_view()), "sbin")
    assert not app.exception
    assert "SBIN" in app.subheader[0].value and "State Bank of India" in app.subheader[0].value
    metrics = {m.label: m.value for m in app.metric}
    assert metrics == {"Verdict": "Buy", "Conviction": "72"}
    assert any("quant_technical: bullish 72" in e.label for e in app.expander)
    assert any("Not run: valuation (not built yet)" in c for c in texts(app.caption))


def test_the_page_draws_both_charts_and_each_panel_with_as_of_and_coverage():
    app = open_app(FakeService(full_view()), "sbin")
    assert len(app.get("plotly_chart")) == 2
    captions = texts(app.caption)
    assert any("coverage: full" in c and "as of 2026-10-02" in c for c in captions)  # technical panel
    assert any("coverage: partial" in c for c in captions)  # risk panel
    assert len(app.dataframe) == 2
    assert any("not available" in e.label for e in app.expander)  # the risk panel's missing figure


def test_the_page_lists_key_risks_notes_and_the_disclaimer():
    app = open_app(FakeService(full_view()), "sbin")
    markdown = texts(app.markdown)
    assert any("Key risks" in m for m in markdown) and "- a data gap" in markdown
    captions = texts(app.caption)
    assert "Note: risk overlay missing" in captions and DISCLAIMER in captions


def test_an_ambiguous_name_shows_candidates_and_no_charts():
    app = open_app(FakeService(build_view(ambiguous_result())), "sbi")
    assert "could be more than one instrument" in app.warning[0].value
    assert len(app.dataframe) == 1 and app.get("plotly_chart") == []
    assert not app.metric


def test_a_result_with_no_view_warns_the_user():
    app = open_app(FakeService(full_view(status=NO_VIEW)), "sbin")
    assert any("No specialist had enough data" in w.value for w in app.warning)


def test_athena_errors_and_bad_input_are_shown_not_raised():
    app = open_app(FakeService(error=athena_error("no LLM provider key is set")), "sbin")
    assert not app.exception and app.error[0].value == "no LLM provider key is set"
    assert open_app(FakeService(error=ValueError("empty query")), "x").error[0].value == "empty query"
    assert not app.metric


def test_each_metric_table_has_one_text_value_column_so_the_browser_never_gets_mixed_types():
    app = open_app(FakeService(full_view()), "sbin")
    assert len(app.dataframe) == 2
    for frame in app.dataframe:
        assert {type(v) for v in frame.value["value"]} == {str}
    values = dict(zip(app.dataframe[0].value["metric"], app.dataframe[0].value["value"]))
    assert values["trend_alignment"] == "aligned_up" and values["last_close"] == "259.5"
