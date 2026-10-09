from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from athena.risk_overlay.model import BREACH, WARN, Finding

CAPPABLE = ("Buy", "Overweight")  # a limit can stop a purchase, never a sale
HELD_BACK = "Held back by your risk limits"
UNCALIBRATED_NOTE = "the risk limits are starting points that are not calibrated to any data; VaR and CVaR describe about a year of the past"


def apply_overlay(verdict: dict[str, Any], findings: Sequence[Finding]) -> dict[str, Any]:
    """The verdict after the risk limits. Breaches and warnings always go to the front of the key risks; a breach also
    holds a Buy or Overweight back to Hold and keeps the specialists' own call in `pre_overlay_verdict`."""
    breaches = [f for f in findings if f.status == BREACH]
    warnings = [f for f in findings if f.status == WARN]
    out = dict(verdict)
    risks = [f.message for f in breaches] + [f.message for f in warnings]
    if breaches and verdict["verdict"] in CAPPABLE:
        out["pre_overlay_verdict"] = verdict["verdict"]
        out["verdict"] = "Hold"
        risks.insert(0, f"{HELD_BACK}: the specialists said {verdict['verdict']}.")
    out["key_risks"] = risks + list(verdict["key_risks"])
    out["risk_findings"] = [f.to_dict() for f in findings]
    return out
