from datetime import date, datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from athena.dashboard.verdict_panel import panel

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def stored(days_ago=3, calibration="uncalibrated"):
    verdict = {"verdict": "Underweight", "conviction": 12, "raw_conviction": 15, "calibration": calibration, "key_risks": ["dampened", "split view"]}
    payload = {
        "verdict": verdict, "why": "asked for",
        "specialists": {"valuation": {"signal": "bearish", "confidence": 70, "data_coverage": "full"}},
        "skipped": {"news_sentiment": "not built yet"},
    }
    return {"symbol": "TCS", "made_at": NOW - timedelta(days=days_ago), "data_as_of": date(2026, 10, 9), "payload": payload}


def test_the_panel_data_has_the_headline_age_analysts_and_what_did_not_run():
    shown = panel(stored(), ["the price has moved -20% since"], NOW)
    assert shown["headline"] == "Underweight, conviction 12 of 100 (uncalibrated)"
    assert shown["age"] == "Made 3 days ago from prices to 09 Oct 2026; asked for."
    assert shown["analysts"] == [("valuation", "bearish", 70, "full")] and shown["skipped"] == ["news_sentiment: not built yet"]
    assert shown["risks"] == ["dampened", "split view"] and shown["reasons"] == ["the price has moved -20% since"]


def test_no_verdict_gives_an_empty_panel_and_a_verdict_made_today_says_today():
    assert panel(None, [], NOW)["headline"] is None
    assert panel(stored(0), [], NOW)["age"].startswith("Made today")


def page():
    import streamlit as st
    from datetime import date, datetime, timedelta, timezone

    from athena.dashboard.verdict_panel import render_verdict

    now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)

    class Runner:
        made = []

        def status(self, symbol):
            if symbol == "NOPE":
                from athena.contracts import AthenaError

                raise AthenaError("no price packet")
            if symbol == "NEW":
                return {"verdict": None, "reasons": []}
            verdict = {"verdict": "Hold", "conviction": 10, "raw_conviction": 20, "calibration": "uncalibrated", "key_risks": ["r"]}
            payload = {"verdict": verdict, "why": "asked for", "specialists": {}, "skipped": {}}
            return {"verdict": {"made_at": now - timedelta(days=1), "data_as_of": date(2026, 10, 9), "payload": payload}, "reasons": ["new quarterly results (quarter to 2026-09-30)"]}

        def make(self, symbol, why="asked for"):
            st.session_state.setdefault("made", []).append((symbol, why))

    st.session_state.setdefault("runner", Runner())
    render_verdict(st.session_state["runner"], st.session_state.get("symbol", "TCS"))


def open_page(symbol):
    app = AppTest.from_function(page, default_timeout=30)
    app.session_state["symbol"] = symbol
    return app.run()


def test_a_stock_with_no_verdict_offers_to_make_one():
    app = open_page("NEW")
    assert not app.exception and any("No verdict yet" in i.value for i in app.info)
    assert app.button[0].label == "Get a verdict"


def test_a_stored_verdict_shows_why_it_is_out_of_date_and_that_it_is_uncalibrated():
    app = open_page("TCS")
    assert not app.exception
    assert any("Hold, conviction 10 of 100 (uncalibrated)" in m.value for m in app.markdown)
    assert any("Out of date: new quarterly results" in w.value for w in app.warning)
    assert any("not yet been checked" in c.value for c in app.caption)
    assert app.button[0].label == "Get a fresh verdict"


def test_pressing_the_button_makes_a_verdict_for_the_reasons_it_is_out_of_date():
    app = open_page("TCS")
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["made"] == [("TCS", "new quarterly results (quarter to 2026-09-30)")]


def test_a_stock_that_cannot_be_judged_says_why():
    app = open_page("NOPE")
    assert not app.exception and any("No verdict possible for NOPE: no price packet" in c.value for c in app.caption)
