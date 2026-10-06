from __future__ import annotations

from typing import Protocol

import streamlit as st

from athena.contracts import AthenaError
from athena.dashboard.backtest_view import BacktestView
from athena.dashboard.view import DashboardView, Panel
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW
from athena.orchestrator.report import DISCLAIMER

PLACEHOLDER = "SBIN"
BACKTESTABLE = ("equity", "etf")
BACKTEST_NOTE = "Past results do not predict future ones."


class ViewService(Protocol):
    def view(self, query: str) -> DashboardView: ...

    def backtest(self, identifier: str) -> BacktestView: ...


def _show(value: str | float) -> str:
    """Numbers and labels share one column, so show every value as text (a mixed column cannot be sent to the browser)."""
    return value if isinstance(value, str) else f"{value:.4f}".rstrip("0").rstrip(".")


def _panel(panel: Panel) -> None:
    st.markdown(f"**{panel.title}**")
    st.caption(f"as of {panel.as_of}  |  source: {panel.source}  |  coverage: {panel.coverage}")
    if panel.rows:
        st.dataframe(
            [{"metric": r.name, "value": _show(r.value), "unit": r.unit, "window": r.window, "note": r.note} for r in panel.rows],
            hide_index=True, width="stretch",
        )
    if panel.missing:
        with st.expander(f"{len(panel.missing)} figure(s) not available"):
            for name, why in panel.missing.items():
                st.write(f"{name}: {why}")


def render(view: DashboardView) -> None:
    """Draw one `DashboardView`: verdict, specialist views, charts, then each metric panel with its as-of and coverage."""
    if view.status == NEEDS_CLARIFICATION:
        st.warning(f"{view.query!r} could be more than one instrument ({' '.join(view.notes)}). Type the exact symbol.")
        st.dataframe(
            [{"symbol": c.identifier, "name": c.name, "class": c.asset_class, "match": round(c.score, 2)} for c in view.candidates],
            hide_index=True, width="stretch",
        )
        return

    st.subheader(f"{view.identifier}  {view.name}")
    st.caption(f"{view.asset_class}  |  matched by {view.match}")
    verdict = view.verdict or {}
    left, right = st.columns(2)
    left.metric("Verdict", verdict.get("verdict", "-"))
    right.metric("Conviction", verdict.get("conviction", 0))
    if view.status == NO_VIEW:
        st.warning("No specialist had enough data to form a view.")

    st.markdown("**Specialists**")
    for row in view.specialists:
        with st.expander(f"{row.name}: {row.signal} {row.confidence}  (coverage {row.coverage})"):
            st.write(row.reasoning)
            if row.missing:
                st.caption("Missing: " + ", ".join(row.missing))
    if view.skipped:
        st.caption("Not run: " + "; ".join(f"{name} ({why})" for name, why in view.skipped.items()))

    if view.price_figure is not None:
        st.plotly_chart(view.price_figure, width="stretch")
        st.plotly_chart(view.rsi_figure, width="stretch")
    for panel in view.panels:
        _panel(panel)

    risks = (view.verdict or {}).get("key_risks", [])
    if risks:
        st.markdown("**Key risks**")
        for risk in risks:
            st.write(f"- {risk}")
    for note in view.notes:
        st.caption(f"Note: {note}")
    st.caption(DISCLAIMER)


def render_backtest(view: BacktestView) -> None:
    """Draw a `BacktestView`: per rule a comparison table, the facts that do not fit it, the blinding check and the curve."""
    st.markdown(f"**Backtest {view.identifier}**")
    for note in view.notes:
        st.warning(note)
    for panel in view.panels:
        st.markdown(f"**{panel.rule} rule**: {panel.description}")
        st.dataframe(
            [{"measure": measure, "strategy": strategy, "buy and hold": hold} for measure, strategy, hold in panel.rows],
            hide_index=True, width="stretch",
        )
        for fact in panel.facts:
            st.caption(fact)
        st.caption(
            "Blinding check (ticker removed, dates shifted 28 years, prices rescaled to 100): "
            + ("same trades" if panel.blinded_identical else "DIFFERENT trades, so the rule may depend on the name or the price level")
        )
        st.plotly_chart(panel.figure, width="stretch")
    st.caption(view.assumptions)
    st.caption(f"{BACKTEST_NOTE} {DISCLAIMER}")


def _remembered(key: str, token: str, compute):
    """Streamlit reruns the whole page on every click. Keep the last result per input, so a click redraws it instead of
    asking the specialists or replaying eight years again."""
    saved = st.session_state.get(key)
    if saved is not None and saved[0] == token:
        return saved[1]
    value = compute()
    st.session_state[key] = (token, value)
    return value


def _backtest_section(service: ViewService, identifier: str) -> None:
    if not st.checkbox(
        "Backtest the technical rules (up to 8 years of history)",
        key=f"backtest-{identifier}",
        help="Replays each rule day by day on this instrument's price history and compares it with buy and hold.",
    ):
        return
    try:
        with st.spinner("Downloading history and replaying every trading day..."):
            result = _remembered("backtest", identifier, lambda: service.backtest(identifier))
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render_backtest(result)


def run(service: ViewService) -> None:
    st.set_page_config(page_title="Athena", layout="wide")
    st.title("Athena")
    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER).strip()
    if not query:
        st.info("Type an NSE ticker, an ISIN or a name to analyze it.")
        return
    try:
        with st.spinner("Resolving, fetching data and asking the specialists..."):
            view = _remembered("view", query, lambda: service.view(query))
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render(view)
    if view.status != NEEDS_CLARIFICATION and view.asset_class in BACKTESTABLE:
        _backtest_section(service, view.identifier)


@st.cache_resource(show_spinner="Loading NSE lists, market series and models...")
def _live_service():
    from athena.dashboard.service import live_service

    return live_service()


if __name__ == "__main__":
    run(_live_service())
