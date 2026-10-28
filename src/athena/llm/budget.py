"""Counting model calls and stopping at the owner's monthly ceiling.

Every call through a metered client is appended to `.athena/llm_usage.jsonl` (time, tier, the model that answered, characters sent and received). The ceilings are the owner's, in
`.athena/llm_budget.json`, a plain file he edits: calls a month per tier, for example `{"strong": {"calls_per_month": 300}}`. A tier with no ceiling is only counted. The ceiling is in calls, not
rupees, because what a call costs depends on the model that answered; the usage file holds enough (the model name and the characters) to price a month afterwards. A call that would pass the ceiling
is refused before it is made, with the count in the message, and the caller (the council) records that and goes on with the blend.

    python -m athena.llm.budget            # this month's calls and characters by tier and model, against the ceilings"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError

USAGE_PATH = Path(".athena") / "llm_usage.jsonl"
BUDGET_PATH = Path(".athena") / "llm_budget.json"


class BudgetExceeded(AthenaError):
    """The month's ceiling for a tier is reached."""


def _month(moment: datetime) -> str:
    return f"{moment:%Y-%m}"


class Meter:
    def __init__(self, usage_path: Path = USAGE_PATH, budget_path: Path = BUDGET_PATH, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._usage, self._budget, self._clock = Path(usage_path), Path(budget_path), clock

    def ceilings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._budget.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def rows(self) -> list[dict[str, Any]]:
        try:
            return [json.loads(line) for line in self._usage.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            return []

    def calls_this_month(self, tier: str) -> int:
        month = _month(self._clock())
        return sum(1 for r in self.rows() if r.get("tier") == tier and not r.get("failed") and str(r.get("at", "")).startswith(month))

    def check(self, tier: str) -> None:
        """Raises `BudgetExceeded` when one more call would pass the tier's ceiling for the month."""
        limit = (self.ceilings().get(tier) or {}).get("calls_per_month")
        if isinstance(limit, int) and self.calls_this_month(tier) >= limit:
            raise BudgetExceeded(f"the {tier} tier has made {self.calls_this_month(tier)} calls this month; your ceiling in {self._budget} is {limit}")

    def record(
        self, tier: str, source: str, chars_in: int, chars_out: int, usage: dict[str, int] | None = None, failures: list[str] | None = None,
    ) -> None:
        self._usage.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": self._clock().isoformat(), "tier": tier, "model": source, "chars_in": chars_in, "chars_out": chars_out, **(usage or {})}
        if failures:
            row["failures"] = [str(f)[:200] for f in failures]  # the models tried before this one answered, and why each did not
        with self._usage.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def record_failure(self, tier: str, error: str) -> None:
        """A call that no model answered. It is kept for the owner to read and is not counted against the ceiling (it used no model)."""
        self._usage.parent.mkdir(parents=True, exist_ok=True)
        with self._usage.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": self._clock().isoformat(), "tier": tier, "failed": True, "error": str(error)[:400]}) + "\n")

    def summary(self) -> dict[str, dict[str, Any]]:
        month = _month(self._clock())
        out: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "chars_in": 0, "chars_out": 0, "prompt_tokens": 0, "completion_tokens": 0, "models": defaultdict(int)})
        for r in self.rows():
            if str(r.get("at", "")).startswith(month) and not r.get("failed"):
                t = out[r["tier"]]
                t["calls"] += 1
                t["chars_in"] += int(r.get("chars_in", 0))
                t["chars_out"] += int(r.get("chars_out", 0))
                t["prompt_tokens"] += int(r.get("prompt_tokens", 0))
                t["completion_tokens"] += int(r.get("completion_tokens", 0))
                t["models"][r.get("model", "")] += 1
        return {tier: {**v, "models": dict(v["models"]), "ceiling": (self.ceilings().get(tier) or {}).get("calls_per_month")} for tier, v in out.items()}


class MeteredLLM:
    """A model client that counts its calls and refuses past the ceiling. It passes `names` and `last_source` through, so it stands wherever the client did."""

    def __init__(self, inner: Any, tier: str, meter: Meter):
        self._inner, self._tier, self._meter = inner, tier, meter

    @property
    def names(self) -> list[str]:
        return list(getattr(self._inner, "names", []))

    @property
    def last_source(self) -> str:
        return str(getattr(self._inner, "last_source", ""))

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(getattr(self._inner, "last_usage", {}) or {})

    def complete(self, system: str, user: str) -> str:
        self._meter.check(self._tier)
        try:
            reply = self._inner.complete(system, user)
        except Exception as exc:
            self._meter.record_failure(self._tier, f"{type(exc).__name__}: {exc}")
            raise
        failures = [f"{name}: {why}" for name, why in getattr(self._inner, "last_failures", ())]
        self._meter.record(self._tier, self.last_source, len(system) + len(user), len(reply), self.last_usage, failures)
        return reply


def main() -> int:
    meter = Meter()
    summary = meter.summary()
    if not summary:
        print("no calls recorded this month")
    for tier, v in sorted(summary.items()):
        limit = "no ceiling" if v["ceiling"] is None else f"ceiling {v['ceiling']}"
        tokens = f", {v['prompt_tokens']:,} prompt and {v['completion_tokens']:,} completion tokens" if v["prompt_tokens"] or v["completion_tokens"] else ""
        print(f"{tier}: {v['calls']} calls ({limit}), {v['chars_in']:,} characters sent, {v['chars_out']:,} received{tokens}; " + ", ".join(f"{m or 'unknown'} x{n}" for m, n in v["models"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
