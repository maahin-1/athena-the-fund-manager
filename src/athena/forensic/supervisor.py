"""The supervisor: chooses the lanes for a case, runs them in rounds, routes the questions they put to each other, asks a second lane to look at any flag that has only one source, stops when nothing new
comes in or the calls are spent, and writes the report.

It is mostly code. The one model call it makes is the narrative at the end, which may only cite flag ids that exist and numbers in the report, and may not advise; if it fails those checks, a plain text
written by code is used instead. The concern level is computed by `athena.forensic.scoring` from the final status of the flags; nothing the supervisor writes can change it. The report says what was
examined and what was not (a lane that failed, a question nobody answered, a lane that never ran) so a gap is never read as a clean result."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from athena.contracts import AthenaError
from athena.evaluation.grounding import check_grounded
from athena.forensic.agent import AgentResult, run_agent
from athena.forensic.budget import BudgetExhausted, CallBudget, Client
from athena.forensic.model import ANSWER, ESTABLISHED, FLAG, NOTE, OBSERVATION, OPEN, QUESTION, REJECTED, SINGLE_SOURCE, UNROUTABLE, Case, Entry, LaneSpec
from athena.forensic.scoring import classify, concern
from athena.forensic.store import ForensicStore
from athena.forensic.tools import Registry
from athena.guard.scope import advice_in

KIND_LANES: Mapping[str, tuple[str, ...]] = {
    "drhp": ("text", "statements", "ownership", "peers"),
    "listed": ("statements", "ownership", "market", "text"),
    "news": ("market", "text", "ownership"),
}
DEFAULT_BRIEFS = {
    "drhp": "Examine this draft offer document for statements that conflict with each other or with the financial data, and for anything the numbers do not support.",
    "listed": "Examine this listed company's statements, ownership and market behaviour for differences that need an explanation.",
    "news": "Examine the claims made about this company in the news and on forums against what its own data and filings show.",
}
# words a summary of differences has no use for: an order to trade, or an accusation (the report says "differs", never "lied")
_FORBIDDEN = re.compile(r"\b(buy|sell|avoid|subscribe|invest|short|lied|lie|lying|fraud\w*|cheat\w*|scam\w*|crook\w*|manipulat\w*|fake\w*)\b", re.IGNORECASE)
MAX_SECOND_OPINIONS = 3  # requests per round to a second lane for a flag with one source
SUPERVISOR_SYSTEM = (
    "SUPERVISOR\nYou write a short summary for the owner of a forensic review of one company or offer document. A stylized analytical framework, not financial advice and not an accusation. "
    "Use ONLY the JSON you are given. Cite flags by their ids. Do not compute anything and do not give a concern level (it is already decided). Never say anyone lied, committed fraud, or that the owner should buy, sell, apply or avoid. "
    "Say plainly what was and was not examined. Two to six sentences, plain words."
)


class Supervisor:
    def __init__(
        self,
        store: ForensicStore,
        registry: Registry,
        lanes: Mapping[str, LaneSpec],
        llm: Client,
        total_calls: int = 40,
        max_rounds: int = 2,
        kind_lanes: Mapping[str, Sequence[str]] = KIND_LANES,
    ):
        self.store, self.registry, self.lanes, self.llm = store, registry, dict(lanes), llm
        self.total_calls, self.max_rounds, self.kind_lanes = total_calls, max_rounds, kind_lanes

    # --- planning ---
    def plan(self, case: Case) -> list[str]:
        """The lanes that run for this case: those the case kind uses that are available."""
        return [name for name in self.kind_lanes.get(case.kind, ()) if name in self.lanes]

    # --- the investigation ---
    def investigate(self, case: Case, brief: str | None = None) -> dict[str, Any]:
        self.store.add_case(case)
        lane_names = self.plan(case)
        budget = CallBudget(self.total_calls, self.store, case.case_id)
        lookup = lambda ev: self.store.evidence(case.case_id, ev)  # noqa: E731
        results: dict[str, list[AgentResult]] = {name: [] for name in lane_names}
        stopped = "rounds finished"
        reserve = 1  # the narrative
        for round_no in range(1, self.max_rounds + 1):
            if not lane_names:
                stopped = "no lane is available for this kind of case"
                break
            share = max(1, (max(0, budget.left - reserve)) // max(1, len(lane_names)))
            before = len([e for e in self.store.entries(case.case_id) if e.kind != NOTE and e.status != REJECTED])
            for name in lane_names:
                if budget.left - reserve <= 0:
                    results[name].append(AgentResult(name, "budget", note="the case's call cap was reached before this lane ran"))
                    continue
                inbox = [e for e in self.store.entries(case.case_id, kind=QUESTION, status=OPEN) if e.body.get("to") == name]
                client = budget.client(self.llm, name, round_no, cap=min(share, budget.left - reserve))
                results[name].append(run_agent(self.lanes[name], case, self.store, self.registry, client, round_no, brief or DEFAULT_BRIEFS.get(case.kind, ""), inbox))
            classify(self.store, case.case_id, lookup)
            self._referee(case, lane_names, round_no)
            after = len([e for e in self.store.entries(case.case_id) if e.kind != NOTE and e.status != REJECTED])
            if budget.left - reserve <= 0:
                stopped = "the call cap was reached"
                break
            if after == before:
                stopped = f"round {round_no} added nothing new"
                break
            if not self.store.entries(case.case_id, kind=QUESTION, status=OPEN):
                stopped = "no open questions are left"
                break
        classify(self.store, case.case_id, lookup)
        report = self._report(case, lane_names, results, budget, stopped)
        report["narrative"] = self._narrative(report, budget)
        report["cost"] = self._cost(case, budget)  # after the narrative, which is a call too
        self.store.save_report(case.case_id, report)
        return report

    def _cost(self, case: Case, budget: CallBudget) -> dict[str, Any]:
        calls = self.store.calls(case.case_id)
        return {"model_calls": len(calls), "failed_calls": sum(1 for c in calls if not c["ok"]), "approx_tokens_in": sum(c["chars_in"] for c in calls) // 4, "approx_tokens_out": sum(c["chars_out"] for c in calls) // 4, "cap": budget.total}

    # --- between rounds ---
    def _referee(self, case: Case, lane_names: Sequence[str], round_no: int) -> None:
        """Questions to a lane that is not in the case cannot be answered: mark them. A request about a flag is closed once that lane has backed the flag. A flag with one source gets a request to
        another lane to find independent evidence for or against it."""
        entries = self.store.entries(case.case_id)
        for q in self.store.entries(case.case_id, kind=QUESTION, status=OPEN):
            if q.body.get("to") not in lane_names:
                self.store.set_status(case.case_id, q.id, UNROUTABLE, f"no lane named {q.body.get('to')!r} in this case")
            elif q.body.get("about") and any(e.author == q.body.get("to") and e.body.get("supports") == q.body["about"] and e.status != REJECTED for e in entries):
                self.store.set_status(case.case_id, q.id, "answered", "backed by an observation")
        asked = 0
        for flag in self.store.entries(case.case_id, kind=FLAG, status=SINGLE_SOURCE):
            if asked >= MAX_SECOND_OPINIONS or round_no >= self.max_rounds:
                break
            if any(q.body.get("about") == flag.id for q in self.store.entries(case.case_id, kind=QUESTION)):
                continue
            other = next((n for n in lane_names if n != flag.author), None)
            if other is None:
                continue
            self.store.add_entry(
                case.case_id, QUESTION, "supervisor", round_no,
                {"to": other, "about": flag.id, "text": f"Look for independent evidence for or against {flag.id} from your own data: {flag.text[:240]}. If you find some, write an OBSERVATION with supports={flag.id}; if you find nothing, answer cannot_determine."},
            )
            asked += 1

    # --- the report ---
    def _report(self, case: Case, lane_names: Sequence[str], results: Mapping[str, list[AgentResult]], budget: CallBudget, stopped: str) -> dict[str, Any]:
        entries = self.store.entries(case.case_id)
        flags = [e for e in entries if e.kind == FLAG]
        kept = [e for e in flags if e.status in (ESTABLISHED, SINGLE_SOURCE)]

        def chain(flag: Entry) -> dict[str, Any]:
            refs = []
            for ev in flag.evidence:
                row = self.store.evidence(case.case_id, ev)
                refs.append({"evidence": ev, "tool": row["tool"] if row else None, "args": row["args"] if row else None})
            support = [{"id": e.id, "author": e.author, "text": e.text[:240]} for e in entries if e.kind in (OBSERVATION, ANSWER) and e.body.get("supports") == flag.id and e.status != REJECTED]
            return {"id": flag.id, "type": flag.body.get("flag_type"), "author": flag.author, "text": flag.text, "why": flag.reason, "references": refs, "innocent_explanations": flag.body.get("innocent"), "supported_by": support}

        unanswered = [{"id": q.id, "to": q.body.get("to"), "text": q.text[:240], "status": q.status} for q in entries if q.kind == QUESTION and q.status in (OPEN, UNROUTABLE)]
        not_examined = []
        for name in lane_names:
            runs = results.get(name, [])
            if not runs or all(r.status in ("budget", "failed") for r in runs):
                not_examined.append({"lane": name, "why": (runs[-1].note if runs else "never ran") or "no usable run"})
        for name in self.kind_lanes.get(case.kind, ()):
            if name not in self.lanes:
                not_examined.append({"lane": name, "why": "no such agent is available yet"})
        return {
            "case": {"id": case.case_id, "kind": case.kind, "target": case.target, "as_of": case.as_of.isoformat()},
            "concern": concern(kept),
            "established": [chain(f) for f in kept if f.status == ESTABLISHED],
            "single_source": [chain(f) for f in kept if f.status == SINGLE_SOURCE],
            "observations": sum(1 for e in entries if e.kind == OBSERVATION and e.status != REJECTED),
            "rejected_entries": sum(1 for e in entries if e.status == REJECTED),
            "lanes": {name: [{"status": r.status, "steps": r.steps, "written": r.written, "rejected": r.rejected, "summary": r.summary, "note": r.note} for r in runs] for name, runs in results.items()},
            "unanswered_questions": unanswered,
            "not_examined": not_examined,
            "stopped_because": stopped,
            "cost": self._cost(case, budget),
            "note": "A stylized analytical framework for the owner. Flags are differences to examine, not findings of wrongdoing, and not investment advice.",
        }

    def _narrative(self, report: dict[str, Any], budget: CallBudget) -> str:
        facts = {k: report[k] for k in ("concern", "established", "single_source", "not_examined", "unanswered_questions", "stopped_because")}
        fallback = self._plain(report)
        try:
            text = budget.client(self.llm, "supervisor", 0).complete(SUPERVISOR_SYSTEM, json.dumps(facts, ensure_ascii=False, default=str)).strip()
        except (AthenaError, BudgetExhausted):
            return fallback
        ids = {f["id"] for f in report["established"] + report["single_source"]}
        cited = set(re.findall(r"\bL\d+\b", text))
        if not text or advice_in(text) or _FORBIDDEN.search(text) or not cited <= ids or not check_grounded(re.sub(r"\bL\d+\b", "", text), facts).ok:
            return fallback
        return text

    @staticmethod
    def _plain(report: dict[str, Any]) -> str:
        c = report["concern"]
        span = c["low"] if c["low"] == c["high"] else f"{c['low']} to {c['high']}"
        parts = [f"Concern level: {span}. {c['established']} established difference(s) and {c['single_source']} with one source only."]
        parts += [f"{f['id']}: {f['text']}" for f in report["established"][:3]]
        if report["not_examined"]:
            parts.append("Not examined: " + "; ".join(f"{n['lane']} ({n['why']})" for n in report["not_examined"]) + ".")
        return " ".join(parts)
