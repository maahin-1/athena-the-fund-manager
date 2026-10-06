import json

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


def backtest_box(app):
    return app.checkbox(key="backtest-SBIN")


def backtest_boxes(app):
    return [box for box in app.checkbox if str(box.key).startswith("backtest-")]


def chart_names(app):
    """The names of the traces on the first chart on the page (the price chart)."""
    return [trace.get("name") for trace in json.loads(app.get("plotly_chart")[0].proto.spec)["data"]]


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


def test_the_page_draws_the_chart_and_each_panel_with_as_of_and_coverage():
    app = open_app(FakeService(full_view()), "sbin")
    assert len(app.get("plotly_chart")) == 1
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
    assert len(backtest_boxes(open_app(FakeService(full_view("equity")), "sbin"))) == 1
    assert len(backtest_boxes(open_app(FakeService(full_view("etf")), "sbin"))) == 1
    assert len(backtest_boxes(open_app(FakeService(full_view("mutual_fund")), "sbin"))) == 0
    assert len(backtest_boxes(open_app(FakeService(build_view(ambiguous_result())), "sbi"))) == 0
    assert len(backtest_boxes(open_app(FakeService(), ""))) == 0


def test_nothing_is_replayed_until_the_box_is_ticked():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    assert service.backtests == [] and len(app.get("plotly_chart")) == 1


def test_ticking_the_box_replays_the_rules_without_asking_the_specialists_again():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    backtest_box(app).check().run()
    assert not app.exception and service.backtests == ["SBIN"] and service.queries == ["sbin"]
    assert len(app.get("plotly_chart")) == 3  # the price chart, plus one equity curve per rule
    assert any("trend rule" in m for m in texts(app.markdown)) and any("persona rule" in m for m in texts(app.markdown))
    captions = texts(app.caption)
    assert any("same trades" in c and "Blinding check" in c for c in captions)
    assert any("15 bps per side" in c for c in captions)
    assert any("Past results do not predict future ones" in c and DISCLAIMER in c for c in captions)


def test_the_backtest_tables_compare_strategy_and_buy_and_hold_as_text():
    app = open_app(FakeService(full_view()), "sbin")
    backtest_box(app).check().run()
    tables = [frame for frame in app.dataframe if "strategy" in frame.value.columns]
    assert len(tables) == 2
    for frame in tables:
        assert list(frame.value["measure"]) == ["Total return", "Yearly return", "Worst drawdown", "Sharpe"]
        assert {type(v) for column in ("strategy", "buy and hold") for v in frame.value[column]} == {str}


def test_redrawing_the_page_does_not_replay_the_same_backtest_twice():
    service = FakeService(full_view())
    app = open_app(service, "sbin")
    backtest_box(app).check().run()
    backtest_box(app).uncheck().run()
    assert len(app.get("plotly_chart")) == 1  # unticked: the curves go away
    backtest_box(app).check().run()
    assert service.backtests == ["SBIN"] and service.queries == ["sbin"] and len(app.get("plotly_chart")) == 3


def test_a_rule_whose_blinded_run_differs_is_flagged_on_the_page():
    from dataclasses import replace

    from athena.dashboard.backtest_view import BacktestView

    view = sample_backtest_view()
    flagged = BacktestView(view.identifier, (replace(view.panels[0], blinded_identical=False), view.panels[1]), view.assumptions)
    app = open_app(FakeService(full_view(), backtest=flagged), "sbin")
    backtest_box(app).check().run()
    blinding = [c for c in texts(app.caption) if c.startswith("Blinding check")]
    assert len(blinding) == 2 and "DIFFERENT trades" in blinding[0] and blinding[1].endswith("same trades")


def test_a_backtest_that_cannot_run_shows_the_reason_and_keeps_the_analysis():
    service = FakeService(full_view(), backtest_error=athena_error("not enough price history for SBIN"))
    app = open_app(service, "sbin")
    backtest_box(app).check().run()
    assert not app.exception and app.error[0].value == "not enough price history for SBIN"
    assert len(app.metric) == 2 and len(app.get("plotly_chart")) == 1


def test_the_box_says_how_much_history_is_replayed():
    app = open_app(FakeService(full_view()), "sbin")
    assert backtest_box(app).label == "Backtest the technical rules (up to 8 years of history)"


def test_each_backtest_note_is_shown_as_a_warning_and_none_without_notes():
    from dataclasses import replace

    notes = ("Prices adjusted for 1 split or bonus event(s) ...", "second note")
    app = open_app(FakeService(full_view(), backtest=replace(sample_backtest_view(), notes=notes)), "sbin")
    backtest_box(app).check().run()
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


DEFAULT_TRACES = ["Price", "SMA 50", "SMA 200", "Bollinger upper (20, 2)", "Bollinger lower (20, 2)", "Volume", "RSI 14"]


def add_indicator(app, key):
    app.selectbox(key="add_indicator").select(key)
    app.button(key="add_button").click().run()


def test_the_chart_starts_with_the_familiar_indicators_on_one_figure_and_says_they_are_for_looking():
    app = open_app(FakeService(full_view()), "sbin")
    assert not app.exception and chart_names(app) == DEFAULT_TRACES
    assert "Indicators" in [e.label for e in app.expander]
    assert any("Chart indicators are for looking only" in c for c in texts(app.caption))


def test_adding_an_indicator_draws_it_in_its_own_row():
    app = open_app(FakeService(full_view()), "sbin")
    add_indicator(app, "macd")
    assert not app.exception and chart_names(app) == DEFAULT_TRACES + ["MACD", "Signal", "Histogram"]


def test_changing_a_setting_redraws_the_line_with_the_new_value():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    names = chart_names(app)
    assert not app.exception and "SMA 30" in names and "SMA 50" not in names


def test_removing_an_indicator_takes_its_lines_off_the_chart():
    app = open_app(FakeService(full_view()), "sbin")
    app.button(key="remove-rsi-1").click().run()
    assert chart_names(app) == DEFAULT_TRACES[:-1]


def test_reset_brings_back_the_default_indicators_and_their_settings():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    add_indicator(app, "macd")
    app.button(key="remove-rsi-1").click().run()
    app.button(key="reset_button").click().run()
    assert not app.exception and chart_names(app) == DEFAULT_TRACES
    assert app.number_input(key="ind-sma-1-length").value == 50


def test_the_volume_row_can_be_switched_off():
    app = open_app(FakeService(full_view()), "sbin")
    app.checkbox(key="show_volume").uncheck().run()
    assert "Volume" not in chart_names(app)


def test_the_add_button_is_switched_off_at_the_limit_of_eight_indicators():
    app = open_app(FakeService(full_view()), "sbin")
    for key in ("ema", "wma", "sar", "macd"):
        add_indicator(app, key)
    assert {"EMA 20", "WMA 20", "Parabolic SAR", "MACD"} <= set(chart_names(app)) and app.button(key="add_button").disabled


def test_a_choice_that_cannot_be_drawn_is_reported_and_the_others_are_still_drawn():
    app = open_app(FakeService(full_view()), "sbin")
    add_indicator(app, "macd")
    app.number_input(key="ind-macd-1-fast").set_value(40).run()
    assert not app.exception and any("macd-1: the fast length must be shorter than the slow length" in w for w in texts(app.warning))
    names = chart_names(app)
    assert "MACD" not in names and "SMA 50" in names


def test_a_history_too_short_for_a_setting_is_said_on_the_page():
    app = open_app(FakeService(full_view()), "sbin")
    app.number_input(key="ind-sma-2-length").set_value(400).run()  # only 320 bars in this history
    assert any("Not enough history to draw: SMA 400." in c for c in texts(app.caption))


def test_the_chosen_indicators_stay_when_another_instrument_is_looked_up():
    service = RoutingService({"sbin": full_view(), "tcs": full_view()})
    app = open_app(service, "sbin")
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    app.text_input[0].set_value("tcs").run()
    assert service.queries == ["sbin", "tcs"] and "SMA 30" in chart_names(app)


def start_backtest(service=None):
    service = service or FakeService(full_view())
    app = open_app(service, "sbin")
    backtest_box(app).check().run()
    return service, app


def choose(app, mode):
    app.radio(key="strategy_mode").set_value(mode).run()


def test_the_backtest_starts_on_the_built_in_rules():
    service, app = start_backtest()
    assert app.radio(key="strategy_mode").value == "Built-in rules"
    assert service.rule_sets == [None] and not app.exception


def test_choosing_a_preset_runs_it_straight_away_and_says_what_it_does():
    service, app = start_backtest()
    choose(app, "A preset strategy")
    assert [rule.name for rule in service.rule_sets[-1]] == ["52-week high breakout"]
    app.selectbox(key="preset_choice").select("golden_cross").run()
    assert [rule.name for rule in service.rule_sets[-1]] == ["Golden cross"] and not app.exception
    assert any("crosses above" in c and "simple moving average (length 50)" in c for c in texts(app.caption))


def test_my_own_strategy_waits_for_the_run_button_and_shows_its_description_first():
    service, app = start_backtest()
    choose(app, "Build my own")
    assert service.rule_sets == [None]  # nothing new has run
    assert any("Build or paste a strategy above" in c for c in texts(app.caption))
    assert any(c.startswith("Buy when the close is at or above the highest close over the last 252 bars") and "Stop out if the price falls 2 x ATR" in c for c in texts(app.caption))
    app.button(key="run_strategy").click().run()
    (rule,) = service.rule_sets[-1]
    assert rule.name == "My strategy" and rule.stop_atr == 2.0 and not app.exception
    assert len(service.rule_sets) == 2


def test_the_builder_turns_the_boxes_into_the_strategy_that_runs():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.text_input(key="builder_name").set_value("Cheap and oversold").run()
    app.selectbox(key="entry-0-left-kind").select("Indicator").run()
    app.selectbox(key="entry-0-left-ind").select("rsi").run()
    app.number_input(key="entry-0-left-rsi-length").set_value(10).run()
    app.selectbox(key="entry-0-op").select("is below").run()
    app.selectbox(key="entry-0-right-kind").select("Number").run()
    app.number_input(key="entry-0-right-number").set_value(30).run()
    app.number_input(key="builder_stop").set_value(0).run()
    app.button(key="run_strategy").click().run()
    (rule,) = service.rule_sets[-1]
    assert rule.name == "Cheap and oversold" and rule.stop_atr is None
    assert rule.description.startswith("Buy when the RSI (length 10) is below 30. Sell when the close is at or below the lowest close")
    assert rule.description.endswith("No protective stop.")


def test_several_conditions_can_be_required_all_together_or_any_one_of_them():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.number_input(key="entry-count").set_value(2).run()
    assert app.radio(key="entry-combine").value == "all of them"
    app.button(key="run_strategy").click().run()
    assert " and the close is above 0." in service.rule_sets[-1][0].description
    app.radio(key="entry-combine").set_value("any of them").run()
    app.button(key="run_strategy").click().run()
    assert " or the close is above 0." in service.rule_sets[-1][0].description


def test_pasted_json_runs_and_a_bad_one_is_explained_and_cannot_be_run():
    import json

    service, app = start_backtest()
    choose(app, "Paste JSON")
    assert not app.exception and app.button(key="run_strategy").disabled is False
    app.text_area(key="strategy_json").set_value("{not json").run()
    assert "This is not valid JSON" in app.error[0].value and app.button(key="run_strategy").disabled
    unknown = {"name": "x", "entry": {"op": "gt", "left": {"ind": "nope"}, "right": {"const": 1}}, "exit": {"op": "lt", "left": {"price": "close"}, "right": {"const": 1}}}
    app.text_area(key="strategy_json").set_value(json.dumps(unknown)).run()
    assert "entry.left.ind: unknown indicator 'nope'" in app.error[0].value and app.button(key="run_strategy").disabled
    assert len(service.rule_sets) == 1  # nothing was run
    good = {**unknown, "name": "From JSON", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 1}}}
    app.text_area(key="strategy_json").set_value(json.dumps(good)).run()
    app.button(key="run_strategy").click().run()
    assert service.rule_sets[-1][0].name == "From JSON" and not app.error


def test_a_strategy_that_was_run_is_remembered_and_not_run_again_on_other_clicks():
    service, app = start_backtest()
    choose(app, "Build my own")
    app.button(key="run_strategy").click().run()
    runs = len(service.rule_sets)
    app.checkbox(key="show_volume").uncheck().run()
    app.number_input(key="ind-sma-1-length").set_value(30).run()
    assert len(service.rule_sets) == runs and service.queries == ["sbin"]


def test_switching_back_to_the_built_in_rules_shows_them_again():
    service, app = start_backtest()
    choose(app, "A preset strategy")
    choose(app, "Built-in rules")
    assert service.rule_sets[-1] is None and not app.exception
