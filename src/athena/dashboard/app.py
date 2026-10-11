from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import streamlit as st

from athena.contracts import AthenaError
from athena.backtest.rules import RULES, Rule, SeriesRule
from athena.dashboard.backtest_view import BacktestView
from athena.dashboard.market import render_market
from athena.dashboard.risk_form import risk_controls
from athena.dashboard.strategy_form import strategy_controls
from athena.dashboard.view import DashboardView, Panel
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW
from athena.orchestrator.report import DISCLAIMER
from athena.risk_overlay.model import LABELS, Overlay
from athena.risk_overlay.parse import overlay_token
from athena.dashboard.charts import build_chart, quality_chart, returns_chart, signal_share_chart, specialist_chart, yields_chart
from athena.technicals.indicators import (
    MAX_INDICATORS, REGISTRY, Selected, default_params, default_selection, next_id, short_history, validate,
)

PLACEHOLDER = "SBIN"
QUERY_KEY = "query"  # the search box
PICK_KEY = "picked_instrument"  # a table click waiting to be moved into the search box
PICK_COUNT_KEY = "picks_made"
CHOSEN_KEY = "chosen_instrument"  # the row last clicked; while the box still holds it, it is analysed without a list
INDICATORS_KEY = "indicators"  # the chosen indicators, as a list of plain dicts
VOLUME_KEY = "show_volume"
ADD_KEY = "add_indicator"
CHART_NOTE = (
    "Chart indicators are for looking only. The specialists read their own fixed windows, "
    "so changing them here never changes a verdict."
)
BACKTESTABLE = ("equity", "etf")
STATUS_WORDS = {"ok": "ok", "warn": "warning", "breach": "BREACH", "unchecked": "not checked"}
BACKTEST_NOTE = "Past results do not predict future ones."


class ViewService(Protocol):
    def view(self, query: str, overlay: Overlay | None = None, list_relatives: bool = False) -> DashboardView: ...

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


def _risk_overlay(verdict: dict) -> None:
    """The risk limits the person set, one row per check, and what they did to the verdict."""
    findings = verdict.get("risk_findings")
    if not findings:
        return
    st.markdown("**Risk overlay**")
    st.dataframe(
        [{"check": LABELS[item["check"]], "status": STATUS_WORDS[item["status"]], "detail": item["message"]} for item in findings],
        hide_index=True, width="stretch",
    )


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


def _at_a_glance(view: DashboardView) -> None:
    """The same figures the page already holds, drawn as bars and a donut: who says what, where the weight sits, and
    how the price moved over each horizon."""
    bars = specialist_chart(view.specialists)
    others = [figure for figure in (signal_share_chart(view.specialists), returns_chart(view.candles)) if figure is not None]
    business = [figure for figure in (yields_chart(view.panels), quality_chart(view.panels)) if figure is not None]
    if bars is None and not others and not business:
        return
    st.markdown("**At a glance**")
    if bars is not None:
        st.plotly_chart(bars, width="stretch")
    for figures in (others, business):
        for column, figure in zip(st.columns(len(figures)) if figures else [], figures):
            column.plotly_chart(figure, width="stretch")


def render(view: DashboardView) -> None:
    """Draw one `DashboardView`: verdict, specialist views, charts, then each metric panel with its as-of and coverage."""
    if view.status == NEEDS_CLARIFICATION:
        st.warning(f"{view.query!r} could be more than one instrument ({' '.join(view.notes)}). Click a row below, or type the exact symbol.")
        st.caption(f"{len(view.candidates)} matches. Click a row to analyse it.")
        picks = st.session_state.get(PICK_COUNT_KEY, 0)  # a new key per pick, so an old row selection never comes back
        event = st.dataframe(
            [{"symbol": c.identifier, "name": c.name, "class": c.asset_class, "match": round(c.score, 2)} for c in view.candidates],
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"candidate_table-{picks}",
        )
        if event.selection.rows:
            st.session_state[PICK_KEY] = view.candidates[event.selection.rows[0]].identifier
            st.session_state[PICK_COUNT_KEY] = picks + 1
            st.rerun()
        return

    st.subheader(f"{view.identifier}  {view.name}")
    st.caption(f"{view.asset_class}  |  matched by {view.match}")
    verdict = view.verdict or {}
    left, right = st.columns(2)
    left.metric("Verdict", verdict.get("verdict", "-"))
    right.metric("Conviction", verdict.get("conviction", 0))
    if "pre_overlay_verdict" in verdict:
        cause = "unreliable price data" if verdict.get("held_back_by") == "price data" else "your risk limits"
        st.warning(f"Held back from {verdict['pre_overlay_verdict']} by {cause}: the specialists said {verdict['pre_overlay_verdict']}.")
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
    _at_a_glance(view)
    for panel in view.panels:
        _panel(panel)
    _risk_overlay(verdict)

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
        title = f"{panel.rule} rule" if panel.rule in RULES else panel.rule
        st.markdown(f"**{title}**: {panel.description}")
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
        "Backtest rules and strategies (up to 8 years of history)",
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
    analyse, market = st.tabs(["Analyse a stock", "Market screen"])
    with analyse:
        _analyse(service)
    with market:
        path = getattr(service, "market_db", None)
        if path is None:
            st.info("The market screen needs the market database: run `python -m athena.warehouse.build`, then restart the dashboard.")
        else:
            render_market(path)


def _analyse(service: ViewService) -> None:
    overlay, problem = risk_controls()
    picked = st.session_state.pop(PICK_KEY, None)  # applied before the box exists: Streamlit forbids changing it after
    if picked:
        st.session_state[QUERY_KEY] = picked
        st.session_state[CHOSEN_KEY] = picked
    query = st.text_input("Ticker, ISIN or name", placeholder=PLACEHOLDER, key=QUERY_KEY).strip()
    if st.session_state.get(CHOSEN_KEY) != query:
        st.session_state.pop(CHOSEN_KEY, None)
    list_relatives = CHOSEN_KEY not in st.session_state  # a typed ticker lists its relatives; a chosen row is analysed
    if problem:  # shown even with an empty box, and nothing is analysed until it is fixed
        st.error(problem)
        return
    if not query:
        st.info("Type an NSE ticker, an ISIN or a name to analyze it.")
        return
    try:
        with st.spinner("Resolving, fetching data and asking the specialists..."):
            view = _remembered(
                "view", f"{query}|{overlay_token(overlay)}|{list_relatives}", lambda: service.view(query, overlay, list_relatives)
            )
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
