from __future__ import annotations

from collections.abc import Sequence
from typing import Any

HELD_BY = "price data"
CAPPABLE = ("Buy", "Overweight")  # the gate stops a purchase on unreliable prices, never a sale


def gate_verdict(verdict: dict[str, Any], flags: Sequence[str]) -> dict[str, Any]:
    """The verdict after the price check. With no flags it is returned unchanged. Otherwise a key risk says that the price
    figures cannot be trusted and a Buy or Overweight is held back to Hold, keeping the specialists' own call in
    `pre_overlay_verdict`."""
    if not flags:
        return verdict
    listed = ", ".join(flags)
    risk = (
        f"The price history has large overnight break(s) that no corporate-action record explains ({listed}); volatility, "
        "drawdown and trend figures that span them may be wrong. Check the exchange's announcements for the cause."
    )
    out = dict(verdict)
    if verdict["verdict"] in CAPPABLE:
        out["pre_overlay_verdict"] = verdict["verdict"]
        out["held_back_by"] = HELD_BY
        out["verdict"] = "Hold"
        out["key_risks"] = [f"Held back by unreliable price data: the specialists said {verdict['verdict']}.", risk, *verdict["key_risks"]]
    else:
        out["key_risks"] = [risk, *verdict["key_risks"]]
    return out
