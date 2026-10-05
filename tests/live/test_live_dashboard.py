import pytest
from streamlit.testing.v1 import AppTest

from athena.contracts import AthenaError
from athena.dashboard.service import live_service
from athena.orchestrator.orchestrator import OK

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def service():
    try:
        return live_service()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def rows(panel):
    return {row.name: row.value for row in panel.rows}


def test_live_stock_view_has_verdict_charts_and_complete_panels(service):
    view = service.view("SBIN")
    technical, risk = view.panels
    print("\nSBIN", view.verdict, {k: rows(technical)[k] for k in ("last_close", "rsi_14", "trend_alignment")}, rows(risk))
    assert view.status == OK and view.price_figure is not None and view.rsi_figure is not None
    assert technical.coverage == "full" and not technical.missing
    assert {"volatility_annualized", "max_drawdown", "beta", "alpha_annualized", "sharpe"} <= set(rows(risk))
    assert "tracking_error" in risk.missing  # a stock has no tracking index


def test_live_etf_view_adds_tracking_error_against_the_index(service):
    view = service.view("NIFTYBEES")
    risk = view.panels[1]
    print("\nNIFTYBEES", view.verdict, rows(risk))
    assert view.asset_class == "etf" and {"tracking_error", "tracking_difference"} <= set(rows(risk))
    assert all(row.note for row in risk.rows if row.name.startswith("tracking"))  # the market-price caveat travels along


def test_live_ambiguous_name_asks_instead_of_guessing(service):
    view = service.view("SBI")
    assert view.candidates and view.price_figure is None


def test_live_page_renders_end_to_end():
    def script():
        from athena.dashboard.app import _live_service, run

        run(_live_service())

    app = AppTest.from_function(script, default_timeout=180).run()
    app.text_input[0].set_value("SBIN").run()
    assert not app.exception, app.exception
    assert len(app.get("plotly_chart")) == 2 and {m.label for m in app.metric} == {"Verdict", "Conviction"}
