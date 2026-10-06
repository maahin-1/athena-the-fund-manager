from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import streamlit as st

from athena.contracts import AthenaError
from athena.backtest.rules import Rule, SeriesRule
from athena.dashboard.backtest_view import BacktestView
from athena.dashboard.strategy_form import strategy_controls
from athena.dashboard.view import DashboardView, Panel
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW
from athena.orchestrator.report import DISCLAIMER
from athena.dashboard.charts import build_chart
from athena.technicals.indicators import (
    MAX_INDICATORS, REGISTRY, Selected, default_params, default_selection, next_id, short_history, validate,
)

PLACEHOLDER = "SBIN"
QUERY_KEY = "query"  # the search box
PICK_KEY = "picked_instrument"  # a table click waiting to be moved into the search box
INDICATORS_KEY = "indicators"  # the chosen indicators, as a list of plain dicts
VOLUME_KEY = "show_volume"
ADD_KEY = "add_indicator"
CHART_NOTE = (
    "Chart indicators are for looking only. The specialists read their own fixed windows, "
    "so changing them here never changes a verdict."
)
BACKTESTABLE = ("equity", "etf")
BACKTEST_NOTE = "Past results do not predict future ones."


class ViewService(Protocol):
    def view(self, query: str) -> DashboardView: ...

    def backtest(self, identifier: str, rules: Sequence[str | Rule | SeriesRule] | None = None) -> BacktestView: ...


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


def _chosen() -> list[dict]:
    if INDICATORS_KEY not in st.session_state:
        st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]
    return st.session_state[INDICATORS_KEY]


def _forget_widgets(prefix: str) -> None:
    for key in [key for key in st.session_state if key.startswith(prefix)]:
        del st.session_state[key]


def _add_indicator() -> None:
    chosen = _chosen()
    if len(chosen) < MAX_INDICATORS:
        key = st.session_state[ADD_KEY]
        chosen.append(Selected(next_id([Selected.from_dict(d) for d in chosen], key), key, default_params(key)).to_dict())


def _remove_indicator(item_id: str) -> None:
    st.session_state[INDICATORS_KEY] = [d for d in _chosen() if d["id"] != item_id]
    _forget_widgets(f"ind-{item_id}-")


def _reset_indicators() -> None:
    st.session_state[INDICATORS_KEY] = [item.to_dict() for item in default_selection()]
    _forget_widgets("ind-")


def _indicator_controls() -> list[Selected]:
    """The Indicators section: each chosen indicator with its settings and a remove button, an add picker, a reset."""
    chosen = _chosen()
    with st.expander("Indicators"):  # a fixed label: a changing one would collapse the section after every click
        for item in chosen:
            spec = REGISTRY[item["key"]]
            columns = st.columns([2, *([1] * len(spec.params)), 1])
            columns[0].markdown(f"**{spec.label}**")
            for column, param in zip(columns[1:], spec.params):
                number = float if not param.integer else int
                item["params"][param.name] = column.number_input(
                    param.label, min_value=number(param.minimum), max_value=number(param.maximum),
                    value=number(item["params"].get(param.name, param.default)), step=number(param.step),
                    key=f"ind-{item['id']}-{param.name}",
                )
            columns[-1].button("Remove", key=f"remove-{item['id']}", on_click=_remove_indicator, args=(item["id"],))
        picker, add, reset, volume = st.columns([3, 1, 1, 1])
        picker.selectbox("Add indicator", list(REGISTRY), format_func=lambda key: REGISTRY[key].label, key=ADD_KEY)
        add.button("Add", key="add_button", on_click=_add_indicator, disabled=len(chosen) >= MAX_INDICATORS)
        reset.button("Reset", key="reset_button", on_click=_reset_indicators)
        volume.checkbox("Volume", value=True, key=VOLUME_KEY)
    return [Selected.from_dict(d) for d in chosen]


def _chart_section(view: DashboardView) -> None:
    valid, problems = validate(_indicator_controls())
    for item_id, reason in problems.items():
        st.warning(f"{item_id}: {reason}")
    empty = short_history(view.candles, valid)
    if empty:
        st.caption("Not enough history to draw: " + ", ".join(empty) + ".")
    st.plotly_chart(build_chart(view.candles, valid, view.chart_title, st.session_state.get(VOLUME_KEY, True)), width="stretch")
    st.caption(CHART_NOTE)


def render(view: DashboardView) -> None:
    """Draw one `DashboardView`: verdict, specialist views, charts, then each metric panel with its as-of and coverage."""
    if view.status == NEEDS_CLARIFICATION:
        st.warning(f"{view.query!r} could be more than one instrument ({' '.join(view.notes)}). Click a row below, or type the exact symbol.")
        st.caption(f"{len(view.candidates)} matches. Click a row to analyse it.")
        event = st.dataframe(
            [{"symbol": c.identifier, "name": c.name, "class": c.asset_class, "match": round(c.score, 2)} for c in view.candidates],
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="candidate_table",
        )
        if event.selection.rows:
            st.session_state[PICK_KEY] = view.candidates[event.selection.rows[0]].identifier
            st.rerun()
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

    if view.candles:
        _chart_section(view)
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
    ready, rules, token = strategy_controls()
    if not ready:
        st.caption("Build or paste a strategy above, then press Run this strategy.")
        return
    try:
        with st.spinner("Downloading history and replaying every trading day..."):
            result = _remembered("backtest", f"{identifier}|{token}", lambda: service.backtest(identifier, rules))
    except (AthenaError, ValueError) as exc:
        st.error(str(exc))
        return
    render_backtest(result)


def run(service: ViewService) -> None:
    st.set_page_config(page_title="Athena", layout="wide")
    st.title("Athena")
    picked = st.session_state.pop(PICK_KEY, None)  # applied before the box exists: Streamlit forbids changing it after
    if picked:
        st.session_state[QUERY_KEY] = picked
    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER, key=QUERY_KEY).strip()
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
