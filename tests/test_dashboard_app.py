import dash_fakes
from dash_fakes import FakeService, RoutingService, ambiguous_result, athena_error, full_view, sample_backtest_view
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


def test_the_page_shows_the_three_fundamentals_panels_each_with_coverage():
    app = open_app(FakeService(full_view(fundamentals=dash_fakes.FUNDAMENTALS)), "sbin")
    assert not app.exception and len(app.dataframe) == 5
    bold = texts(app.markdown)
    assert all(any(title in m for m in bold) for title in ("Valuation", "Business quality", "Earnings"))
    captions = texts(app.caption)
    assert sum("annual to 2026-03-31, quarter to 2026-06-30" in c and "coverage: full" in c for c in captions) == 3
    for frame in app.dataframe:
        assert {type(v) for v in frame.value["value"]} == {str}


def test_the_page_offers_a_backtest_for_stocks_and_etfs_only():
    assert len(open_app(FakeService(full_view("equity")), "sbin").checkbox) == 1
    assert len(open_app(FakeService(full_view("etf")), "sbin").checkbox) == 1
    assert len(open_app(FakeService(full_view("mutual_fund")), "sbin").checkbox) == 0
    assert len(open_app(FakeService(build_view(ambiguous_result())), "sbi").checkbox) == 0
    assert len(open_app(FakeService(), "").checkbox) == 0


def test_nothing_is_replayed_until_the_box_is_ticked():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    assert service.backtests == [] and len(app.get("plotly_chart")) == 2


def test_ticking_the_box_replays_the_rules_without_asking_the_specialists_again():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    assert not app.exception and service.backtests == ["SBIN"] and service.queries == ["sbin"]
    assert len(app.get("plotly_chart")) == 4  # the price and RSI charts, plus one equity curve per rule
    assert any("trend rule" in m for m in texts(app.markdown)) and any("persona rule" in m for m in texts(app.markdown))
    captions = texts(app.caption)
    assert any("same trades" in c and "Blinding check" in c for c in captions)
    assert any("15 bps per side" in c for c in captions)
    assert any("Past results do not predict future ones" in c and DISCLAIMER in c for c in captions)


def test_the_backtest_tables_compare_strategy_and_buy_and_hold_as_text():
    app = open_app(FakeService(full_view()), "sbin")
    app.checkbox[0].check().run()
    tables = [frame for frame in app.dataframe if "strategy" in frame.value.columns]
    assert len(tables) == 2
    for frame in tables:
        assert list(frame.value["measure"]) == ["Total return", "Yearly return", "Worst drawdown", "Sharpe"]
        assert {type(v) for column in ("strategy", "buy and hold") for v in frame.value[column]} == {str}


def test_redrawing_the_page_does_not_replay_the_same_backtest_twice():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    app.checkbox[0].uncheck().run()
    assert len(app.get("plotly_chart")) == 2  # unticked: the curves go away
    app.checkbox[0].check().run()
    assert service.backtests == ["SBIN"] and service.queries == ["sbin"] and len(app.get("plotly_chart")) == 4


def test_a_rule_whose_blinded_run_differs_is_flagged_on_the_page():
    from dataclasses import replace

    from athena.dashboard.backtest_view import BacktestView

    view = sample_backtest_view()
    flagged = BacktestView(view.identifier, (replace(view.panels[0], blinded_identical=False), view.panels[1]), view.assumptions)
    app = open_app(FakeService(full_view(), backtest=flagged), "sbin")
    app.checkbox[0].check().run()
    blinding = [c for c in texts(app.caption) if c.startswith("Blinding check")]
    assert len(blinding) == 2 and "DIFFERENT trades" in blinding[0] and blinding[1].endswith("same trades")


def test_a_backtest_that_cannot_run_shows_the_reason_and_keeps_the_analysis():
    service = FakeService(full_view(), backtest_error=athena_error("not enough price history for SBIN"))
    app = open_app(service, "sbin")
    app.checkbox[0].check().run()
    assert not app.exception and app.error[0].value == "not enough price history for SBIN"
    assert len(app.metric) == 2 and len(app.get("plotly_chart")) == 2


def test_the_box_says_how_much_history_is_replayed():
    app = open_app(FakeService(full_view()), "sbin")
    assert app.checkbox[0].label == "Backtest the technical rules (up to 8 years of history)"


def test_each_backtest_note_is_shown_as_a_warning_and_none_without_notes():
    from dataclasses import replace

    notes = ("Prices adjusted for 1 split or bonus event(s) ...", "second note")
    app = open_app(FakeService(full_view(), backtest=replace(sample_backtest_view(), notes=notes)), "sbin")
    app.checkbox[0].check().run()
    assert not app.exception and texts(app.warning) == list(notes)
    plain = open_app(FakeService(full_view()), "sbin")
    plain.checkbox[0].check().run()
    assert texts(plain.warning) == []


def many():
    return build_view(ambiguous_result(12))


def click(app, row):
    app.session_state["candidate_table"] = {"selection": {"rows": [row], "columns": [], "cells": []}}
    app.run()


def test_every_candidate_is_listed_and_the_page_says_to_click_a_row():
    app = open_app(FakeService(many()), "sbi")
    assert not app.exception and len(app.dataframe[0].value) == 12
    assert any("12 matches. Click a row to analyse it." in c for c in texts(app.caption))


def test_clicking_a_candidate_fills_the_search_box_and_analyses_that_instrument():
    service = RoutingService({"sbi": many(), "SBI02": full_view()})
    app = open_app(service, "sbi")
    click(app, 2)
    assert not app.exception and app.text_input[0].value == "SBI02"
    assert service.queries == ["sbi", "SBI02"]
    assert len(app.metric) == 2 and not app.warning  # the verdict page replaced the ambiguity warning and list


def test_a_later_ambiguous_search_does_not_repeat_the_old_click():
    service = RoutingService({"sbi": many(), "SBIN": full_view(), "tat": many()})
    app = open_app(service, "sbi")
    click(app, 0)
    assert service.queries == ["sbi", "SBIN"]
    app.text_input[0].set_value("tat").run()
    assert not app.exception and service.queries == ["sbi", "SBIN", "tat"]
    assert len(app.dataframe) == 1 and not app.metric  # the list is shown again and nothing was picked for the user
