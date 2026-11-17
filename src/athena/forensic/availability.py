"""The availability gate for the forensic tools: one rule, applied where the evidence is read, not only said in a prompt.

A case dated D may use only what was demonstrably available by the end of D (`athena.timing`). `strict_for(case)` decides whether the case is a strict historical one (its date is before today in
India, or the case says so in `meta["availability"]`). Every decision is kept in the forensic store, so the report states the policy and how much evidence was rejected and why."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from athena import timing
from athena.clock import utc_now
from athena.forensic.model import Case
from athena.forensic.tools import ToolContext, ToolError


def strict_for(case: Case) -> bool:
    """Strict historical mode: forced by `case.meta["availability"]` ("strict" or "live"), else on exactly when the case date is before today in India."""
    forced = (case.meta or {}).get("availability")
    if forced == "strict":
        return True
    if forced == "live":
        return False
    return timing.is_historical(case.as_of, utc_now())


def policy_for(case: Case) -> str:
    return timing.STRICT_POLICY if strict_for(case) else timing.LIVE_POLICY


def kind_of(admission: timing.Admission) -> str:
    if admission.ok:
        return "admitted_unknown_publication" if admission.precision == "unknown" else "admitted"
    return "unknown_publication" if admission.precision == "unknown" else "after_case_date"


def check(ctx: ToolContext, source: str, ref: str, published: date | datetime | None) -> dict[str, Any]:
    """Decide whether `ref` (published at `published`) may be used by this case, keep the decision, and return what the tool should show about its publication. Raises `ToolError` (the agent is
    shown the reason) when it may not."""
    strict, policy = strict_for(ctx.case), policy_for(ctx.case)
    admission = timing.admit(published, ctx.case.as_of, strict=strict)
    ctx.store.record_availability(ctx.case.case_id, source, ref, "admitted" if admission.ok else "rejected", kind_of(admission), admission.reason, policy, strict)
    if not admission.ok:
        raise ToolError(f"{source} {ref} is not available to a case dated {ctx.case.as_of.isoformat()} ({policy}): {admission.reason}")
    return {"published": published.isoformat() if published else None, "status": "unknown" if admission.precision == "unknown" else "admitted", "policy": policy, "precision": admission.precision}


def record(ctx: ToolContext, source: str, ref: str, admitted: bool, kind: str, reason: str) -> None:
    """Keep a decision made by the caller's own rule (for example a shareholding pattern's filing date), under this case's policy."""
    ctx.store.record_availability(ctx.case.case_id, source, ref, "admitted" if admitted else "rejected", kind, reason, policy_for(ctx.case), strict_for(ctx.case))
