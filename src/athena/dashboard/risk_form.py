from __future__ import annotations

import streamlit as st

from athena.risk_overlay.model import LABELS, MEASURES, PRESETS, Overlay
from athena.risk_overlay.parse import ProfileError, parse_holdings, parse_profile

RISK_ON = "risk_on"
PRESET_KEY = "risk_preset"
HOLDINGS_KEY = "risk_holdings"
UPLOAD_KEY = "risk_upload"
AMOUNT_KEY = "risk_amount"
SHOWN_AS_PERCENT = tuple(measure for measure in MEASURES if measure != "concentration")  # the index is shown as a number
HOLDINGS_HELP = "One row per holding, with the header: symbol,value (what you hold now, in rupees)."


def _scale(measure: str) -> float:
    return 100.0 if measure in SHOWN_AS_PERCENT else 1.0


def _limit_boxes(preset: str) -> dict:
    """The limits as the person has edited them, as the `limits` part of a profile."""
    base = PRESETS[preset].limits
    limits: dict = {}
    for measure in MEASURES:
        scale, unit = _scale(measure), "%" if measure in SHOWN_AS_PERCENT else ""
        key = f"risk-{preset}-{measure}"
        if not st.checkbox(LABELS[measure], value=True, key=f"{key}-on"):
            limits[measure] = None
            continue
        warn_box, hard_box = st.columns(2)
        step = 1.0 if scale == 100.0 else 0.05
        warn = warn_box.number_input(f"Warn at {unit}".strip(), min_value=0.0, value=round(base[measure].warn * scale, 6), step=step, key=f"{key}-warn")
        hard = hard_box.number_input(f"Hard limit {unit}".strip(), min_value=0.0, value=round(base[measure].hard * scale, 6), step=step, key=f"{key}-hard")
        limits[measure] = {"warn": warn / scale, "hard": hard / scale}
    return limits


def _holdings_text() -> tuple[str, str | None]:
    """The holdings CSV: the uploaded file if there is one, otherwise what was pasted."""
    pasted = st.text_area("Holdings (CSV)", key=HOLDINGS_KEY, height=120, placeholder="symbol,value\nSBIN,50000", help=HOLDINGS_HELP)
    uploaded = st.file_uploader("...or upload a CSV file", type=["csv"], key=UPLOAD_KEY)
    if uploaded is None:
        return pasted, None
    try:
        return uploaded.getvalue().decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return "", "holdings: the uploaded file is not a text file"


def risk_controls() -> tuple[Overlay | None, str | None]:
    """The sidebar: the person's risk limits, holdings and the amount they might invest. Returns the overlay to apply
    (None while the limits are switched off) and a message when something they entered cannot be used."""
    with st.sidebar:
        st.header("Risk profile")
        if not st.checkbox("Apply my risk limits", key=RISK_ON, help="Checks the stock against your limits after the specialists have spoken."):
            st.caption("Off: the verdict is the specialists' blend, with no risk limits.")
            return None, None
        preset = st.selectbox("Starting point", list(PRESETS), index=1, key=PRESET_KEY)
        with st.expander("Limits"):  # a fixed label: a changing one would collapse the section on every click
            limits = _limit_boxes(preset)
        text, upload_problem = _holdings_text()
        amount = st.number_input("Amount you might invest (rupees, 0 for none)", min_value=0.0, step=10000.0, value=0.0, key=AMOUNT_KEY)
        st.caption("Limits are starting points, not advice. Nothing you enter here is stored.")
    if upload_problem:
        return None, upload_problem
    try:
        return Overlay(parse_profile({"preset": preset, "limits": limits}), parse_holdings(text), amount or None), None
    except ProfileError as exc:
        return None, str(exc)
