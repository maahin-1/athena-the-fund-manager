from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SIGNAL_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}
COVERAGE_WEIGHT = {"full": 1.0, "partial": 0.7}  # how much a specialist's confidence counts
COVERAGE_CONVICTION_CAP = {"full": 100, "partial": 70}  # a verdict resting on partial data is not fully convinced
CONFLICT_MIN_CONFIDENCE = 60  # TRD 2.15 default
CONFLICT_CONVICTION_CAP = 40  # while the debate step does not exist, a contested blend stays tentative
BUY_AT = 0.6  # net score at or above: Buy
OVERWEIGHT_AT = 0.2  # at or above: Overweight; above -OVERWEIGHT_AT: Hold
UNDERWEIGHT_ABOVE = -0.6  # above: Underweight; at or below: Sell
NO_VIEW_RISK = "no specialist had enough data to form a view"


@dataclass(frozen=True)
class Conflict:
    """Specialists on opposite sides with both confidences at or above the threshold (TRD 2.15)."""

    bullish: tuple[str, ...]
    bearish: tuple[str, ...]


@dataclass(frozen=True)
class BlendResult:
    verdict: dict[str, Any]  # the judge verdict contract (TRD section 3)
    net: float  # -1 (all bearish) .. +1 (all bullish), confidence- and coverage-weighted
    participants: tuple[str, ...]
    conflict: Conflict | None


def participants(outputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Specialists that formed a view; one reporting `insufficient` coverage does not take part."""
    return {name: out for name, out in outputs.items() if out["data_coverage"] != "insufficient"}


def detect_conflict(
    outputs: Mapping[str, Mapping[str, Any]], min_confidence: int = CONFLICT_MIN_CONFIDENCE
) -> Conflict | None:
    taking_part = participants(outputs)
    bullish = tuple(n for n, o in taking_part.items() if o["signal"] == "bullish" and o["confidence"] >= min_confidence)
    bearish = tuple(n for n, o in taking_part.items() if o["signal"] == "bearish" and o["confidence"] >= min_confidence)
    return Conflict(bullish, bearish) if bullish and bearish else None


def _verdict_for(net: float) -> str:
    if net >= BUY_AT:
        return "Buy"
    if net >= OVERWEIGHT_AT:
        return "Overweight"
    if net > -OVERWEIGHT_AT:
        return "Hold"
    if net > UNDERWEIGHT_ABOVE:
        return "Underweight"
    return "Sell"


def _key_risks(outputs: Mapping[str, Mapping[str, Any]], conflict: Conflict | None) -> list[str]:
    risks: list[str] = []
    for name, out in outputs.items():
        if out["data_coverage"] == "partial":
            risks.append(f"{name}: partial data, missing {', '.join(out['missing'])}")
        elif out["data_coverage"] == "insufficient":
            risks.append(f"{name}: not enough data to form a view (missing {', '.join(out['missing'])})")
    if conflict:
        risks.append(
            f"specialists disagree (bullish: {', '.join(conflict.bullish)}; bearish: {', '.join(conflict.bearish)}); "
            "the debate step is not built, so this is a blend with capped conviction"
        )
    return risks


def blend_signals(
    outputs: Mapping[str, Mapping[str, Any]], min_conflict_confidence: int = CONFLICT_MIN_CONFIDENCE
) -> BlendResult:
    """Conviction-weighted blend of specialist outputs into a judge-contract verdict (resolution_path 'blend').

    net = sum(sign * confidence * coverage weight) / sum(confidence * coverage weight). The verdict comes from
    net; conviction is the mean confidence times |net| for a directional call, or times (1 - |net|) for a Hold
    (so a Hold measures how balanced the evidence is), capped by the weakest participant's coverage and, if
    specialists conflict, by CONFLICT_CONVICTION_CAP."""
    taking_part = participants(outputs)
    conflict = detect_conflict(outputs, min_conflict_confidence)
    if not taking_part:
        risks = [NO_VIEW_RISK] + _key_risks(outputs, None)
        verdict = {"verdict": "Hold", "conviction": 0, "key_risks": risks, "resolution_path": "blend"}
        return BlendResult(verdict, 0.0, (), None)

    weights = {n: o["confidence"] / 100 * COVERAGE_WEIGHT[o["data_coverage"]] for n, o in taking_part.items()}
    total = sum(weights.values())
    net = sum(SIGNAL_SIGN[taking_part[n]["signal"]] * w for n, w in weights.items()) / total if total else 0.0
    verdict = _verdict_for(net)
    mean_confidence = sum(o["confidence"] for o in taking_part.values()) / len(taking_part)
    strength = 1 - abs(net) if verdict == "Hold" else abs(net)
    cap = min(COVERAGE_CONVICTION_CAP[o["data_coverage"]] for o in taking_part.values())
    if conflict:
        cap = min(cap, CONFLICT_CONVICTION_CAP)
    conviction = max(0, min(round(mean_confidence * strength), cap))
    return BlendResult(
        {"verdict": verdict, "conviction": conviction, "key_risks": _key_risks(outputs, conflict), "resolution_path": "blend"},
        round(net, 4),
        tuple(taking_part),
        conflict,
    )
