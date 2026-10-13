from datetime import datetime, timezone

import dash_fakes
from dash_fakes import FakeService, full_view
from streamlit.testing.v1 import AppTest

from athena.contracts import AthenaError
from athena.dashboard.live_panel import depth_rows, minutes_chart
from athena.live.quotes import IST, Minute, parse_quote
from test_live_quotes import BODY, OPEN_TIME


class FakeLive:
    def __init__(self, error=None):
        self.error, self.asked = error, []

    def quote(self, symbol):
        self.asked.append(symbol)
        if self.error:
            raise self.error
        return parse_quote(symbol, BODY)

    def minutes(self, symbol):
        return [Minute(datetime(2026, 10, 13, 9, 15 + i, tzinfo=IST), 940.0 + i, 100.0) for i in range(5)]

    def now(self):
        return OPEN_TIME


def script():
    import dash_fakes

    from athena.dashboard.app import run

    run(dash_fakes.CURRENT["service"])


def open_app(live, query="SBIN"):
    service = FakeService(view=full_view())
    service.live = live
    dash_fakes.CURRENT["service"] = service
    app = AppTest.from_function(script, default_timeout=30).run()
    return app.text_input[0].set_value(query).run()


def test_the_price_panel_shows_the_exchanges_quote_and_asks_for_the_viewed_symbol():
    live = FakeLive()
    app = open_app(live)
    assert not app.exception
    assert any("Live price" in m.value for m in app.markdown)
    assert live.asked and set(live.asked) == {"SBIN"}
    labels = [m.label for m in app.metric]
    assert "Last price" in labels and "Day high" in labels and "Day low" in labels
    last = next(m for m in app.metric if m.label == "Last price")
    assert last.value == "946.85" and "+5.85" in last.delta


def test_a_failing_quote_is_a_warning_and_the_rest_of_the_page_still_draws():
    app = open_app(FakeLive(error=AthenaError("the exchange's quote could not be read: Timeout")))
    assert not app.exception
    assert any("No live quote" in w.value for w in app.warning)
    assert any("Specialists" in m.value for m in app.markdown)


def test_without_a_quote_source_there_is_no_price_panel():
    app = open_app(None)
    assert not any("Live price" in m.value for m in app.markdown)


def test_the_specialist_sections_and_the_indicators_section_are_open_without_a_click():
    app = open_app(FakeLive())
    shown = [e for e in app.expander if "coverage" in e.label or e.label == "Indicators"]
    assert shown and all(e.proto.expanded for e in shown)


def test_depth_rows_pair_bids_with_asks_and_the_chart_marks_the_previous_close():
    quote = parse_quote("SBIN", BODY)
    assert depth_rows(quote) == [
        {"bid qty": "5", "bid": "946.80", "ask": "946.85", "ask qty": "98"},
        {"bid qty": "20", "bid": "946.70", "ask": "946.95", "ask qty": "128"},
    ]
    figure = minutes_chart(FakeLive().minutes("SBIN"), 941.0)
    assert len(figure.data[0].x) == 5 and figure.layout.shapes[0].y0 == 941.0
