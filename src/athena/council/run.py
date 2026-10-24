"""Convening the council on one stock: the evidence sheet, the debate when there is a conflict, then the judge.

The council runs beside the blend, never in place of it: its answer is stored with the verdict (`payload["council"]`) and the blend's rating stays the verdict, so the two can be
scored against each other on the same stocks and the council promoted only if it wins. It costs model calls, so it runs only when asked for (`mode="always"`) or when the analysts conflict or
the decision is REVIEW (`mode="conflicts"`)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from athena.council import debate, judge

ALWAYS, CONFLICTS = "always", "conflicts"


@dataclass(frozen=True)
class Council:
    debate_llm: Any  # something with .complete(system, user): the specialist tier
    judge_llm: Any  # the strongest tier the owner has named
    judge_name: str = ""
    mode: str = CONFLICTS


def wanted(council: Council, sheet: Mapping[str, Any]) -> bool:
    if council.mode == ALWAYS:
        return True
    return bool(sheet.get("conflict")) or (sheet.get("decision") or {}).get("type") == "REVIEW"


def convene(council: Council, sheet: Mapping[str, Any]) -> dict[str, Any] | None:
    """The council's record for the sheet, or None when it was not called for."""
    if not wanted(council, sheet):
        return None
    held = debate.hold(sheet, council.debate_llm)
    return {"mode": council.mode, "judge_model": council.judge_name, "debate": held, "judge": judge.decide(sheet, held, council.judge_llm)}
