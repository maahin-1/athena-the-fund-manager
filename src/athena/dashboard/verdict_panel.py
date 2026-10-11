"""The verdict under a stock's chart on the market screen: the stored verdict with its age and why it may be out of date, and a button to
make a fresh one. The verdict is the model's reading of the stock's stored packets; the number shown is dampened until the analysts'
confidence has been calibrated, and the panel says so."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import streamlit as st

UNCALIBRATED_HELP = (
    "Conviction is capped and scaled down because the analysts' confidence has not yet been checked against what stocks went on to do. "
    "In the point-in-time test none of them beat always giving the most common answer."
)


def panel(stored: dict[str, Any] | None, reasons: list[str], now: datetime) -> dict[str, Any]:
    """What the panel shows, as plain data: headline, age line, analysts' calls, those not run, risks and the reasons it is out of date."""
    if stored is None:
        return {"headline": None, "age": "", "analysts": [], "skipped": [], "risks": [], "reasons": []}
    payload = stored["payload"]
    verdict = payload["verdict"]
    days = (now - stored["made_at"]).days
    age = "today" if days <= 0 else f"{days} day{'s' if days != 1 else ''} ago"
    return {
        "headline": f"{verdict['verdict']}, conviction {verdict['conviction']} of 100 ({verdict['calibration']})",
        "age": f"Made {age} from prices to {stored['data_as_of']:%d %b %Y}; {payload['why']}.",
        "analysts": [(name, out["signal"], out["confidence"], out["data_coverage"]) for name, out in payload["specialists"].items()],
        "skipped": [f"{name}: {why}" for name, why in payload["skipped"].items()],
        "risks": list(verdict["key_risks"]),
        "reasons": list(reasons),
    }


def render_verdict(verdicts: Any, symbol: str) -> None:
    """`verdicts` is a `VerdictRunner`; the panel reads the stored verdict and makes a new one only when the button is pressed."""
    from athena.contracts import AthenaError

    try:
        state = verdicts.status(symbol)
    except AthenaError as exc:
        st.caption(f"No verdict possible for {symbol}: {exc}")
        return
    except Exception as exc:  # the database may be busy with a long job's write
        st.caption(f"Could not read the verdict ({type(exc).__name__}); try again in a minute.")
        return
    shown = panel(state["verdict"], state["reasons"], datetime.now(timezone.utc))
    st.markdown("##### Verdict")
    if shown["headline"] is None:
        st.info("No verdict yet for this stock. Making one takes about half a minute and uses the model.")
    else:
        st.write(f"**{shown['headline']}**")
        st.caption(shown["age"])
        if shown["reasons"]:
            st.warning("Out of date: " + "; ".join(shown["reasons"]) + ".")
        if shown["headline"].endswith("(uncalibrated)"):
            st.caption(UNCALIBRATED_HELP)
        if shown["analysts"]:
            st.table({"Analyst": [a[0] for a in shown["analysts"]], "Signal": [a[1] for a in shown["analysts"]],
                      "Confidence": [a[2] for a in shown["analysts"]], "Data": [a[3] for a in shown["analysts"]]})
        for line in shown["skipped"]:
            st.caption(f"Not run: {line}")
        for line in shown["risks"]:
            st.caption(f"Risk: {line}")
    label = "Get a fresh verdict" if shown["headline"] else "Get a verdict"
    if st.button(label, key=f"verdict_{symbol}"):
        with st.spinner(f"Asking the analysts about {symbol} (about half a minute)..."):
            try:
                verdicts.make(symbol, why="; ".join(shown["reasons"]) or "asked for")
            except Exception as exc:  # the model down or a missing packet: say so, keep the stored verdict
                st.error(f"No new verdict: {type(exc).__name__}: {str(exc)[:300]}")
                return
        st.rerun()
