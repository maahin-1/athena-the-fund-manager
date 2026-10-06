from __future__ import annotations

import json
from collections.abc import Mapping

import streamlit as st

from athena.backtest.rules import SeriesRule
from athena.strategies.compile import strategy_rule
from athena.strategies.describe import describe
from athena.strategies.model import LINE_NAMES, MAX_SHIFT, MAX_WINDOW, PRICE_FIELDS
from athena.strategies.parse import StrategyError, parse_strategy, to_dict
from athena.strategies.presets import PRESET_DATA, PRESETS
from athena.technicals.indicators import REGISTRY

MODES = ("Built-in rules", "A preset strategy", "Build my own", "Paste JSON")
RUN_KEY = "strategy_to_run"  # the strategy last run, as canonical JSON
MODE_KEY = "strategy_mode_seen"  # the mode of the previous run of the page
OPERATORS = {
    "is above": "gt", "is at or above": "ge", "is below": "lt", "is at or below": "le",
    "crosses above": "crosses_above", "crosses below": "crosses_below",
}
KINDS = ("Price", "Indicator", "Highest of", "Lowest of", "Average of", "Number")
ROLLING_KINDS = {"Highest of": "max", "Lowest of": "min", "Average of": "mean"}
MAX_BUILDER_CONDITIONS = 4
DEFAULT_STOP = 2.0  # the same protective stop the built-in rules use, so results are comparable
FALLBACK_CONDITION = {"op": "gt", "left": {"price": "close"}, "right": {"const": 0}}
ENTRY_DEFAULT = PRESET_DATA["breakout_52w"]["entry"]
EXIT_DEFAULT = PRESET_DATA["breakout_52w"]["exit"]


def _kind_of(expr: Mapping) -> str:
    if "ind" in expr:
        return "Indicator"
    if "rolling" in expr:
        return next(kind for kind, fn in ROLLING_KINDS.items() if fn == expr["rolling"])
    return "Number" if "const" in expr else "Price"


def _number(label: str, key: str, value: float, low: float, high: float, step: float, whole: bool):
    cast = int if whole else float
    return st.number_input(
        label, min_value=cast(low), max_value=cast(high), value=cast(value), step=cast(step), key=key, label_visibility="collapsed"
    )


def _indicator_operand(prefix: str, default: Mapping) -> dict:
    keys = list(REGISTRY)
    key = st.selectbox(
        "Indicator", keys, index=keys.index(default.get("ind", "sma")), format_func=lambda k: REGISTRY[k].label,
        key=f"{prefix}-ind", label_visibility="collapsed",
    )
    saved = default.get("params", {}) if default.get("ind") == key else {}
    params = {}
    for param in REGISTRY[key].params:
        params[param.name] = _number(
            param.label, f"{prefix}-{key}-{param.name}", saved.get(param.name, param.default), param.minimum, param.maximum,
            param.step, param.integer,
        )
    out: dict = {"ind": key, "params": params}
    names = LINE_NAMES[key]
    if len(names) > 1:
        default_line = default.get("line", names[0]) if default.get("ind") == key else names[0]
        out["line"] = st.selectbox("Line", names, index=names.index(default_line), key=f"{prefix}-{key}-line", label_visibility="collapsed")
    return out


def _operand(prefix: str, default: Mapping) -> dict:
    """One side of a comparison: a price, an indicator, the highest, lowest or average of a price over some bars, or a number."""
    kind = st.selectbox("Value", KINDS, index=KINDS.index(_kind_of(default)), key=f"{prefix}-kind", label_visibility="collapsed")
    if kind == "Number":
        return {"const": st.number_input("Number", value=float(default.get("const", 0.0)), key=f"{prefix}-number", label_visibility="collapsed")}
    if kind == "Price":
        field = default.get("price", "close")
        base = {"price": st.selectbox("Price", PRICE_FIELDS, index=PRICE_FIELDS.index(field), key=f"{prefix}-field", label_visibility="collapsed")}
    elif kind == "Indicator":
        base = _indicator_operand(prefix, default)
    else:
        inner = default.get("of", {}).get("price", "close") if "rolling" in default else "close"
        window = default.get("window", 20) if "rolling" in default else 20
        base = {
            "rolling": ROLLING_KINDS[kind],
            "of": {"price": st.selectbox("Of", PRICE_FIELDS, index=PRICE_FIELDS.index(inner), key=f"{prefix}-of", label_visibility="collapsed")},
            "window": _number("Bars", f"{prefix}-window", window, 2, MAX_WINDOW, 1, True),
        }
    back = _number("Bars back", f"{prefix}-back", default.get("shift", 0), 0, MAX_SHIFT, 1, True)
    return {**base, "shift": back} if back else base


def _condition(prefix: str, default: Mapping) -> dict:
    left_column, operator_column, right_column = st.columns([4, 2, 4])
    with left_column:
        left = _operand(f"{prefix}-left", default["left"])
    with operator_column:
        words = list(OPERATORS)
        word = st.selectbox("Comparison", words, index=list(OPERATORS.values()).index(default["op"]), key=f"{prefix}-op", label_visibility="collapsed")
    with right_column:
        right = _operand(f"{prefix}-right", default["right"])
    return {"op": OPERATORS[word], "left": left, "right": right}


def _side(prefix: str, title: str, default: Mapping) -> dict:
    st.markdown(f"**{title}**")
    count = int(st.number_input("How many conditions", min_value=1, max_value=MAX_BUILDER_CONDITIONS, value=1, key=f"{prefix}-count"))
    combine = "all of them"
    if count > 1:
        combine = st.radio("Needs", ("all of them", "any of them"), horizontal=True, key=f"{prefix}-combine")
    conditions = [_condition(f"{prefix}-{n}", default if n == 0 else FALLBACK_CONDITION) for n in range(count)]
    if count == 1:
        return conditions[0]
    return {"all" if combine == "all of them" else "any": conditions}


def _builder() -> dict:
    name = st.text_input("Name", value="My strategy", key="builder_name")
    entry = _side("entry", "Buy when", ENTRY_DEFAULT)
    exit_ = _side("exit", "Sell when", EXIT_DEFAULT)
    stop = st.number_input("Protective stop in ATR below the entry fill (0 for none)", 0.0, 10.0, DEFAULT_STOP, 0.5, key="builder_stop")
    data: dict = {"version": 1, "name": name, "entry": entry, "exit": exit_}
    if stop:
        data["stop_atr"] = float(stop)
    return data


def _json_box() -> dict | None:
    text = st.text_area("Strategy as JSON", value=json.dumps(to_dict(PRESETS["breakout_52w"]), indent=2), height=300, key="strategy_json")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        st.error(f"This is not valid JSON: {exc}")
    except RecursionError:
        st.error("This is not valid JSON: it is nested too deeply.")
    except ValueError as exc:  # for example an integer with more than 4300 digits
        st.error(f"This is not valid JSON: {exc}")
    return None


def strategy_controls() -> tuple[bool, list[SeriesRule] | None, str]:
    """Ask what to test. Returns (ready, rules, token): `rules` is None for the built-in rules, and `token` names the
    choice so the page can remember its result. A strategy of the person's own waits for the Run button."""
    mode = st.radio("Rules to test", MODES, horizontal=True, key="strategy_mode")
    if st.session_state.get(MODE_KEY) != mode:  # a strategy run earlier must not run again when the person comes back
        st.session_state.pop(RUN_KEY, None)
        st.session_state[MODE_KEY] = mode
    if mode == MODES[0]:
        return True, None, "builtin"
    if mode == MODES[1]:
        key = st.selectbox("Preset", list(PRESETS), format_func=lambda k: PRESETS[k].name, key="preset_choice")
        st.caption(describe(PRESETS[key]))
        return True, [strategy_rule(PRESETS[key])], f"preset:{key}"
    data = _builder() if mode == MODES[2] else _json_box()
    strategy = None
    if data is not None:
        try:
            strategy = parse_strategy(data)
        except StrategyError as exc:
            st.error(str(exc))
        else:
            st.caption(describe(strategy))
    if st.button("Run this strategy", key="run_strategy", disabled=strategy is None) and strategy is not None:
        st.session_state[RUN_KEY] = json.dumps(to_dict(strategy), sort_keys=True)
    ran = st.session_state.get(RUN_KEY)
    if not ran:
        return False, None, ""
    return True, [strategy_rule(parse_strategy(json.loads(ran)))], f"custom:{ran}"
