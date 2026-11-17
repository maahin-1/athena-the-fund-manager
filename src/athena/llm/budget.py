"""Counting model calls and stopping at the owner's monthly ceiling.

Every metered call is a *logical request*: one reservation in `.athena/llm_ledger.sqlite` (`athena.llm.ledger`), taken in a transaction before the model is asked, so two processes cannot both take the
last slot. The ceilings are the owner's, in `.athena/llm_budget.json`, a plain file he edits: logical requests a month per tier, for example `{"strong": {"calls_per_month": 300}}`. A tier with no
ceiling is only counted. The ceiling is in logical requests: not provider HTTP attempts (a fallback chain and its retries make several for one request), not tokens, not money. What a call costs depends on
the model that answered; the ledger keeps the model, the attempts, the characters and the tokens the provider reported, enough to price a month afterwards, and says `calls_without_usage` for those that
reported none. A request that would pass the ceiling is refused before it is made, with the count in the message, and the caller (the council) records that and goes on with the blend.

`.athena/llm_usage.jsonl` remains as a readable log of finished requests. It is no longer the count: one unreadable line cannot reset anything, the lines that can be read are kept and the number that
cannot is reported, and the lines already in it are imported into the ledger once, so the month does not start again.

Integrity is never assumed. A ceiling file that is *absent* means the owner set none; one that exists but cannot be read, or that holds something other than non-negative whole-number ceilings, is an
unknown, not "no ceiling": metered calls are refused with `BudgetUnavailable` until it is fixed. The same when the ledger cannot be opened. A request that fails is `refused` (not counted) only when every
provider attempt is known to have been declined; a timeout, a network error or an unreadable reply may have been processed, so it counts as `unknown`. A request still reserved when its process died keeps
counting until a person releases it (`python -m athena.llm.budget --release ID --by NAME --why TEXT`).

    python -m athena.llm.budget            # this month's requests, attempts and characters by tier and model, against the ceilings, and what is unresolved"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.llm import envelope
from athena.llm.ledger import Ledger, LedgerFull, LedgerUnavailable

USAGE_PATH = Path(".athena") / "llm_usage.jsonl"
BUDGET_PATH = Path(".athena") / "llm_budget.json"


class BudgetExceeded(AthenaError):
    """The month's ceiling for a tier is reached."""


class BudgetUnavailable(AthenaError):
    """The ceilings or the ledger cannot be read, so whether a call is allowed is unknown. Metered work is refused until they can."""


def _month(moment: datetime) -> str:
    return f"{moment:%Y-%m}"


@dataclass(frozen=True)
class Reservation:
    id: int
    logical_id: str
    tier: str


class Meter:
    def __init__(self, usage_path: Path = USAGE_PATH, budget_path: Path = BUDGET_PATH, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), ledger_path: Path | str | None = None):
        self._usage, self._budget, self._clock = Path(usage_path), Path(budget_path), clock
        self._ledger = Ledger(ledger_path if ledger_path is not None else self._usage.with_name("llm_ledger.sqlite"))
        self.log_errors = 0  # appends to the usage log that failed: the ledger has the request either way

    # --- the ceilings ---
    def ceiling_state(self) -> tuple[dict[str, Any], str, str]:
        """(ceilings, state, why). `absent`: no file, the owner set none. `ok`: a valid file. `invalid`: it exists and cannot be trusted (unreadable, not JSON, not an object of tiers, or a `calls_per_month`
        that is not a non-negative whole number): the ceilings are then unknown, never "none"."""
        try:
            text = self._budget.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return {}, "absent", ""
        except OSError as exc:
            return {}, "invalid", f"{type(exc).__name__}: {exc}"
        try:
            data = json.loads(text)
        except ValueError as exc:
            return {}, "invalid", f"not JSON ({exc})"
        if not isinstance(data, dict):
            return {}, "invalid", "it must be an object of tiers"
        for tier, spec in data.items():
            if not isinstance(spec, dict):
                return {}, "invalid", f"{tier!r} must be an object such as {{\"calls_per_month\": 300}}"
            if "calls_per_month" in spec:
                limit = spec["calls_per_month"]
                if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                    return {}, "invalid", f"{tier!r}: calls_per_month must be a whole number of 0 or more, not {limit!r}"
        return data, "ok", ""

    def ceilings(self) -> dict[str, Any]:
        return self.ceiling_state()[0]

    # --- the usage log (a readable record; the ledger is the count) ---
    def read_usage(self) -> tuple[list[dict[str, Any]], int]:
        """(the rows that can be read, how many non-blank lines could not be). One bad line never hides the others."""
        try:
            lines = self._usage.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            return [], 0
        except OSError:
            return [], 0
        rows: list[dict[str, Any]] = []
        unreadable = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                unreadable += 1
        return rows, unreadable

    def rows(self) -> list[dict[str, Any]]:
        return self.read_usage()[0]

    def _append(self, row: dict[str, Any]) -> None:
        try:
            self._usage.parent.mkdir(parents=True, exist_ok=True)
            with self._usage.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        except OSError:
            self.log_errors += 1

    # --- reserving and settling ---
    def _ready(self) -> None:
        try:
            self._ledger.ensure(self.read_usage)
        except LedgerUnavailable as exc:
            raise BudgetUnavailable(f"{exc}; metered calls are refused until the ledger can be read (restore or move it aside, and the earlier usage log is imported again)") from exc

    def _limit(self, tier: str) -> int | None:
        ceilings, state, why = self.ceiling_state()
        if state == "invalid":
            raise BudgetUnavailable(f"the ceiling file {self._budget} cannot be trusted ({why}); metered calls are refused until it is fixed or removed")
        limit = (ceilings.get(tier) or {}).get("calls_per_month")
        return limit if isinstance(limit, int) and not isinstance(limit, bool) else None

    def reserve(self, tier: str) -> Reservation:
        """Take one logical-request unit for `tier` before the model is asked. Raises `BudgetExceeded` at the ceiling and `BudgetUnavailable` when the ceilings or the ledger cannot be read."""
        limit = self._limit(tier)
        self._ready()
        now = self._clock()
        logical = uuid.uuid4().hex[:16]
        try:
            rid = self._ledger.reserve(tier, _month(now), limit, logical, now.isoformat())
        except LedgerFull as full:
            raise BudgetExceeded(f"the {tier} tier has made {full.used} calls this month; your ceiling in {self._budget} is {full.limit}") from None
        except LedgerUnavailable as exc:
            raise BudgetUnavailable(f"{exc}; metered calls are refused until the ledger can be read") from exc
        return Reservation(rid, logical, tier)

    def check(self, tier: str) -> None:
        """Raises when one more call would pass the tier's ceiling, without reserving anything (use `reserve` to take the unit)."""
        limit = self._limit(tier)
        self._ready()
        try:
            used = self._ledger.counted(tier, _month(self._clock()))
        except LedgerUnavailable as exc:
            raise BudgetUnavailable(str(exc)) from exc
        if limit is not None and used >= limit:
            raise BudgetExceeded(f"the {tier} tier has made {used} calls this month; your ceiling in {self._budget} is {limit}")

    def settle(self, reservation: Reservation, source: str, chars_in: int, chars_out: int, usage: dict[str, int] | None = None, failures: list[str] | None = None,
               attempts: list[dict[str, Any]] | None = None) -> None:
        """A model answered the reservation. The reply is already in hand, so a ledger that cannot be written now does not lose it: the reservation stays `reserved` (still counted) and the usage log says so."""
        row = {"at": self._clock().isoformat(), "tier": reservation.tier, "model": source, "chars_in": chars_in, "chars_out": chars_out, **(usage or {}),
               "reservation": reservation.id, "logical_id": reservation.logical_id, "attempts": len(attempts or []), "retries": sum(1 for a in attempts or [] if a.get("retry"))}
        if failures:
            row["failures"] = [envelope.scrub(f)[:200] for f in failures]  # the models tried before this one answered, and why each did not
        try:
            self._ledger.settle(reservation.id, row["at"], source, chars_in, chars_out, usage, attempts or [])
        except LedgerUnavailable as exc:
            row["ledger_error"] = str(exc)[:200]
        self._append(row)

    def fail(self, reservation: Reservation, error: str, attempts: list[dict[str, Any]] | None) -> str:
        """No model answered. Returns `refused` (not counted: every attempt was declined) or `unknown` (counted: some attempt may have been processed, or nothing is known of the attempts)."""
        error = envelope.scrub(error)[:400]
        at = self._clock().isoformat()
        try:
            state = self._ledger.fail(reservation.id, at, error, attempts)
        except LedgerUnavailable as exc:
            state = "reserved"  # still counted
            error = f"{error} [ledger: {str(exc)[:120]}]"
        self._append({"at": at, "tier": reservation.tier, "failed": True, "error": error, "state": state, "reservation": reservation.id, "logical_id": reservation.logical_id, "attempts": len(attempts or [])})
        return state

    # --- compatibility: the earlier meter's two writers ---
    def record(self, tier: str, source: str, chars_in: int, chars_out: int, usage: dict[str, int] | None = None, failures: list[str] | None = None) -> None:
        """Append a finished request to the usage log without a reservation (kept for callers of the earlier interface); the ledger does not see it."""
        row = {"at": self._clock().isoformat(), "tier": tier, "model": source, "chars_in": chars_in, "chars_out": chars_out, **(usage or {})}
        if failures:
            row["failures"] = [str(f)[:200] for f in failures]
        self._append(row)

    def record_failure(self, tier: str, error: str) -> None:
        self._append({"at": self._clock().isoformat(), "tier": tier, "failed": True, "error": str(error)[:400]})

    # --- reading ---
    def calls_this_month(self, tier: str) -> int:
        """Logical requests counted against `tier` this month (answered, unknown-outcome and unresolved). From the ledger; if it cannot be read, from the usage log's readable rows."""
        try:
            self._ready()
            return self._ledger.counted(tier, _month(self._clock()))
        except (BudgetUnavailable, LedgerUnavailable):
            month = _month(self._clock())
            return sum(1 for r in self.rows() if r.get("tier") == tier and not r.get("failed") and str(r.get("at", "")).startswith(month))

    def unresolved(self) -> list[dict[str, Any]]:
        self._ready()
        return self._ledger.unresolved()

    def release(self, reservation_id: int, reason: str, by: str) -> None:
        self._ready()
        self._ledger.release(reservation_id, reason, by, self._clock().isoformat())

    def integrity(self) -> dict[str, Any]:
        """What can and cannot be trusted right now: the ceiling file, the ledger, the lines of the usage log that could not be read, and what is unresolved."""
        ceilings, state, why = self.ceiling_state()
        _, unreadable = self.read_usage()
        out: dict[str, Any] = {"ceiling_file": {"path": str(self._budget), "state": state, "why": why}, "usage_log": {"path": str(self._usage), "unreadable_lines": unreadable, "append_failures": self.log_errors}}
        try:
            self._ready()
            out["ledger"] = {"state": "ok", "unresolved": self._ledger.unresolved(), "imported": self._ledger.meta().get("legacy_imported"), "legacy_unreadable_lines": int(self._ledger.meta().get("legacy_unreadable_lines", "0"))}
        except BudgetUnavailable as exc:
            out["ledger"] = {"state": "unreadable", "why": str(exc)[:300]}
        return out

    def summary(self) -> dict[str, dict[str, Any]]:
        """This month by tier. `calls` are answered requests; `logical_requests` is what the ceiling counts (answered, unknown-outcome and unresolved); `provider_attempts` and `retries` are separate;
        tokens are summed over the calls whose provider reported them and `calls_without_usage` says how many did not."""
        month = _month(self._clock())
        try:
            self._ready()
            ledger = self._ledger.summary(month)
        except BudgetUnavailable:
            return self._summary_from_log(month)
        ceilings = self.ceilings()
        return {tier: {**v, "ceiling": (ceilings.get(tier) or {}).get("calls_per_month")} for tier, v in ledger.items()}

    def _summary_from_log(self, month: str) -> dict[str, dict[str, Any]]:
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
        ceilings = self.ceilings()
        return {tier: {**v, "models": dict(v["models"]), "logical_requests": v["calls"], "ceiling": (ceilings.get(tier) or {}).get("calls_per_month"), "source": "usage log (the ledger cannot be read)"} for tier, v in out.items()}


class MeteredLLM:
    """A model client that reserves a unit before each call and refuses past the ceiling, or when the ceilings or the ledger cannot be trusted. It passes `names`, `last_source`, `last_usage` and
    `last_attempts` through, so it stands wherever the client did. Each logical request is also noted for the run being recorded, if there is one (`athena.llm.envelope`)."""

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

    @property
    def last_attempts(self) -> list[dict[str, Any]] | None:
        attempts = getattr(self._inner, "last_attempts", None)
        return None if attempts is None else [dict(a) for a in attempts]

    def complete(self, system: str, user: str) -> str:
        reservation = self._meter.reserve(self._tier)  # raises before any model is asked
        seen = {"tier": self._tier, "logical_id": reservation.logical_id, "origin": "live", "prompt_sha256": envelope.digest(system), "input_sha256": envelope.digest(user), "chars_in": len(system) + len(user)}
        try:
            reply = self._inner.complete(system, user)
        except Exception as exc:  # a KeyboardInterrupt or exit leaves the reservation as it is: counted until a person releases it
            attempts = self.last_attempts
            state = self._meter.fail(reservation, f"{type(exc).__name__}: {exc}", attempts)
            envelope.note_call(**seen, responding_model=envelope.UNKNOWN, attempts=attempts if attempts is not None else envelope.UNKNOWN, usage=envelope.UNKNOWN,
                               outcome="refused" if state == "refused" else "unknown_outcome", error=f"{type(exc).__name__}: {exc}"[:240])
            raise
        failures = [f"{name}: {why}" for name, why in getattr(self._inner, "last_failures", ())]
        attempts = self.last_attempts
        usage = self.last_usage
        self._meter.settle(reservation, self.last_source, len(system) + len(user), len(reply), usage, failures, attempts)
        envelope.note_call(**seen, responding_model=self.last_source or envelope.UNKNOWN, attempts=attempts if attempts is not None else envelope.UNKNOWN, usage=usage or envelope.UNKNOWN,
                           chars_out=len(reply), outcome="answered", fallbacks=failures)
        return reply


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="this month's model requests against the owner's ceilings")
    parser.add_argument("--release", type=int, metavar="ID", help="close an unresolved reservation after checking the provider's side (needs --by and --why)")
    parser.add_argument("--by", default="")
    parser.add_argument("--why", default="")
    args = parser.parse_args(argv)
    meter = Meter()
    if args.release is not None:
        try:
            meter.release(args.release, args.why, args.by)
        except AthenaError as exc:
            print(f"not released: {exc}", file=sys.stderr)
            return 1
        print(f"reservation {args.release} released by {args.by}")
        return 0
    state = meter.integrity()
    if state["ceiling_file"]["state"] == "invalid":
        print(f"WARNING: the ceiling file cannot be trusted ({state['ceiling_file']['why']}): metered calls are refused until it is fixed")
    if state["usage_log"]["unreadable_lines"]:
        print(f"WARNING: {state['usage_log']['unreadable_lines']} line(s) of the usage log cannot be read; the rest are used")
    if state["ledger"]["state"] != "ok":
        print(f"WARNING: the ledger cannot be read: {state['ledger']['why']}")
    summary = meter.summary()
    if not summary:
        print("no calls recorded this month")
    for tier, v in sorted(summary.items()):
        limit = "no ceiling" if v.get("ceiling") is None else f"ceiling {v['ceiling']}"
        tokens = f", {v['prompt_tokens']:,} prompt and {v['completion_tokens']:,} completion tokens reported" if v.get("prompt_tokens") or v.get("completion_tokens") else ""
        unreported = f" ({v['calls_without_usage']} reported none)" if v.get("calls_without_usage") else ""
        attempts = f"; {v.get('provider_attempts', 0)} provider attempts, {v.get('retries', 0)} retries" if "provider_attempts" in v else ""
        print(f"{tier}: {v.get('logical_requests', v['calls'])} logical requests ({limit}), {v['calls']} answered{attempts}, {v['chars_in']:,} characters sent, {v['chars_out']:,} received{tokens}{unreported}; "
              + ", ".join(f"{m or 'unknown'} x{n}" for m, n in v["models"].items()))
        if v.get("unknown_outcome") or v.get("unresolved"):
            print(f"  {v.get('unknown_outcome', 0)} with an unknown outcome and {v.get('unresolved', 0)} unresolved still count against the ceiling")
    for row in state["ledger"].get("unresolved", []):
        print(f"  unresolved #{row['id']} ({row['tier']}, {row['state']}, since {row['reserved_at']}): release it with --release {row['id']} --by NAME --why TEXT once the provider's side is checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
