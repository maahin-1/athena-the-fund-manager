"""A cap on model calls for one investigation, shared by every lane and the supervisor, with a share per lane so one agent cannot spend the case. Every call is recorded (characters in and out; about four
characters make a token) so a report can say what it cost."""
from __future__ import annotations

from typing import Any, Protocol

from athena.contracts import AthenaError
from athena.forensic.store import ForensicStore

CHARS_PER_TOKEN = 4


class BudgetExhausted(AthenaError):
    """The call cap of the case, or of the lane, is reached."""


class Client(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class CallBudget:
    def __init__(self, total: int, store: ForensicStore | None = None, case_id: str = ""):
        self.total, self.used = total, 0
        self._store, self._case_id = store, case_id

    @property
    def left(self) -> int:
        return max(0, self.total - self.used)

    def client(self, llm: Client, lane: str, round_no: int, cap: int | None = None) -> _Metered:
        return _Metered(self, llm, lane, round_no, cap)

    def _record(self, lane: str, round_no: int, chars_in: int, chars_out: int, ok: bool, model: str = "", usage: dict[str, int] | None = None) -> None:
        if self._store is not None:
            usage = usage or {}
            self._store.add_call(self._case_id, lane, round_no, chars_in, chars_out, ok, model, usage.get("prompt_tokens"), usage.get("completion_tokens"))


class _Metered:
    """The model as a lane sees it: counts calls against the case and the lane's own cap, and refuses past either."""

    def __init__(self, budget: CallBudget, llm: Client, lane: str, round_no: int, cap: int | None):
        self._budget, self._llm, self.lane, self._round, self._cap, self.calls = budget, llm, lane, round_no, cap, 0

    def complete(self, system: str, user: str) -> str:
        if self._budget.left <= 0:
            raise BudgetExhausted("the call cap of this case is reached")
        if self._cap is not None and self.calls >= self._cap:
            raise BudgetExhausted(f"{self.lane} has used its {self._cap} calls this round")
        self._budget.used += 1
        self.calls += 1
        try:
            reply: Any = self._llm.complete(system, user)
        except Exception:
            self._budget._record(self.lane, self._round, len(system) + len(user), 0, False)  # noqa: SLF001
            raise
        # what the provider said the call used and which model answered, when the client says (the router's clients do: `athena.llm.budget.Meter` records the same figures); a cache hit says nothing and costs nothing
        usage = {k: int(v) for k, v in (getattr(self._llm, "last_usage", None) or {}).items() if isinstance(v, (int, float))}
        self._budget._record(self.lane, self._round, len(system) + len(user), len(reply), True, str(getattr(self._llm, "last_source", "") or ""), usage)  # noqa: SLF001
        return str(reply)
