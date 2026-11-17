"""The durable budget ledger (audit A5, WP06): reservations in one transaction, a count one bad line cannot reset, integrity that is never assumed, and units that are named for what they are.

Fakes only: a fake HTTP post for the client chain, scripted inner clients, temporary files. No provider is called and no credential is read."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from athena.contracts import AthenaError
from athena.fallback import HealthRegistry
from athena.llm import budget as budget_module
from athena.llm.budget import BudgetExceeded, BudgetUnavailable, Meter, MeteredLLM
from athena.llm.client import OpenAICompatibleClient
from athena.llm.ledger import classify_failure
from athena.llm.router import FallbackLLM

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
SRC = str(Path(__file__).resolve().parents[1] / "src")


class Fake:
    names = ["fake-model"]
    last_source = "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return "reply"


def meter(tmp_path, ceiling=None, tier="strong", clock=lambda: NOW):
    if ceiling is not None:
        (tmp_path / "b.json").write_text(json.dumps({tier: {"calls_per_month": ceiling}}), encoding="utf-8")
    return Meter(tmp_path / "u.jsonl", tmp_path / "b.json", clock)


# --- two processes competing for the last slot -------------------------------------------------------------------------------------------------------------------------------------------------
RACE = """
import sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, {src!r})
from athena.llm.budget import BudgetExceeded, Meter
tmp = Path({tmp!r})
while not (tmp / "go").exists():
    time.sleep(0.002)
m = Meter(tmp / "u.jsonl", tmp / "b.json", lambda: datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
try:
    m.reserve("strong")
    print("reserved")
except BudgetExceeded:
    print("exceeded")
"""


@pytest.mark.parametrize("ceiling, processes", [(1, 6), (3, 8)])
def test_processes_competing_for_the_last_slots_cannot_all_take_them(tmp_path, ceiling, processes):
    (tmp_path / "b.json").write_text(json.dumps({"strong": {"calls_per_month": ceiling}}), encoding="utf-8")
    code = RACE.format(src=SRC, tmp=str(tmp_path))
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(processes)]
    time.sleep(1.5)  # every process is waiting at the gate: the ledger does not exist yet, so the first reservations race the creation of the tables too
    (tmp_path / "go").write_text("go")
    results = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [r[1] for r in results]
    outcomes = [r[0].strip() for r in results]
    assert outcomes.count("reserved") == ceiling and outcomes.count("exceeded") == processes - ceiling
    assert meter(tmp_path).calls_this_month("strong") == ceiling


# --- one malformed line cannot reset the count -------------------------------------------------------------------------------------------------------------------------------------------------
def test_an_unreadable_line_in_the_usage_log_does_not_reset_the_count_the_audit_reproduced(tmp_path):
    m = meter(tmp_path, ceiling=1)
    (tmp_path / "u.jsonl").write_text(json.dumps({"at": NOW.isoformat(), "tier": "strong", "model": "m", "chars_in": 5, "chars_out": 5}) + "\n", encoding="utf-8")
    with (tmp_path / "u.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{partial\n")
    inner = Fake()
    with pytest.raises(BudgetExceeded):
        MeteredLLM(inner, "strong", m).complete("a", "b")  # one valid row for this month is still one call, whatever follows it in the file
    assert inner.calls == 0 and m.calls_this_month("strong") == 1
    state = m.integrity()
    assert state["usage_log"]["unreadable_lines"] == 1 and state["ledger"]["legacy_unreadable_lines"] == 1


def test_garbage_appended_to_the_log_after_the_import_changes_nothing(tmp_path):
    m = meter(tmp_path, ceiling=2)
    MeteredLLM(Fake(), "strong", m).complete("a", "b")
    with (tmp_path / "u.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{partial\n\x00\x00 not json\n")
    assert m.calls_this_month("strong") == 1 and m.integrity()["usage_log"]["unreadable_lines"] == 2
    MeteredLLM(Fake(), "strong", m).complete("a", "b")
    with pytest.raises(BudgetExceeded):
        MeteredLLM(Fake(), "strong", m).complete("a", "b")


def test_the_earlier_usage_log_is_imported_once_and_the_month_does_not_start_again(tmp_path):
    rows = [{"at": NOW.isoformat(), "tier": "strong", "model": "m", "chars_in": 1, "chars_out": 2, "prompt_tokens": 10, "completion_tokens": 5}] * 3
    rows += [{"at": "2026-09-30T10:00:00+00:00", "tier": "strong", "model": "m", "chars_in": 1, "chars_out": 2}]  # last month: not this month's
    rows += [{"at": NOW.isoformat(), "tier": "strong", "failed": True, "error": "x"}]  # a failed call was never counted
    (tmp_path / "u.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    m = meter(tmp_path, ceiling=5)
    assert m.calls_this_month("strong") == 3
    again = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)  # another process, later: reads the ledger, does not import the log a second time
    with (tmp_path / "u.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": NOW.isoformat(), "tier": "strong", "model": "late", "chars_in": 1, "chars_out": 1}) + "\n")
    assert again.calls_this_month("strong") == 3
    assert m.summary()["strong"]["prompt_tokens"] == 30 and m.integrity()["ledger"]["imported"].startswith("5 valid rows")


# --- integrity is never assumed ----------------------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "content", ["", "not json", "[]", '"strong"', '{"strong": 5}', '{"strong": {"calls_per_month": "300"}}', '{"strong": {"calls_per_month": -1}}', '{"strong": {"calls_per_month": 2.5}}', '{"strong": {"calls_per_month": true}}'],
    ids=repr,
)
def test_a_ceiling_file_that_exists_but_cannot_be_trusted_refuses_metered_work_instead_of_meaning_no_ceiling(tmp_path, content):
    (tmp_path / "b.json").write_text(content, encoding="utf-8")
    inner = Fake()
    with pytest.raises(BudgetUnavailable):
        MeteredLLM(inner, "strong", Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)).complete("a", "b")
    assert inner.calls == 0


def test_a_ceiling_file_with_a_byte_order_mark_is_still_read(tmp_path):
    (tmp_path / "b.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"strong": {"calls_per_month": 1}}).encode())
    m = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)
    MeteredLLM(Fake(), "strong", m).complete("a", "b")
    with pytest.raises(BudgetExceeded):
        MeteredLLM(Fake(), "strong", m).complete("a", "b")


def test_a_tier_without_a_ceiling_is_only_counted_and_a_zero_ceiling_refuses_everything(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({"strong": {"calls_per_month": 0}, "mid": {}}), encoding="utf-8")
    m = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)
    MeteredLLM(Fake(), "mid", m).complete("a", "b")
    MeteredLLM(Fake(), "cheap", m).complete("a", "b")
    with pytest.raises(BudgetExceeded):
        MeteredLLM(Fake(), "strong", m).complete("a", "b")


def test_an_unreadable_ledger_refuses_metered_work_and_is_never_read_as_empty(tmp_path):
    m = meter(tmp_path, ceiling=1)
    (tmp_path / "llm_ledger.sqlite").write_bytes(b"this is not a database " * 40)
    inner = Fake()
    with pytest.raises(BudgetUnavailable, match="ledger"):
        MeteredLLM(inner, "strong", m).complete("a", "b")
    assert inner.calls == 0 and m.integrity()["ledger"]["state"] == "unreadable"
    assert m.summary() == {}  # nothing in the (unreadable) log either; the reading falls back to the log, it does not invent a count


def test_a_ledger_that_cannot_be_written_after_the_reply_does_not_lose_the_reply_and_keeps_the_unit_counted(tmp_path, monkeypatch):
    m = meter(tmp_path, ceiling=3)
    real = m._ledger.settle

    def broken(*a, **k):
        raise budget_module.LedgerUnavailable("disk full")

    monkeypatch.setattr(m._ledger, "settle", broken)
    assert MeteredLLM(Fake(), "strong", m).complete("a", "b") == "reply"
    monkeypatch.setattr(m._ledger, "settle", real)
    assert m.calls_this_month("strong") == 1 and m.unresolved()[0]["state"] == "reserved" and "ledger_error" in m.rows()[0]


def test_a_failing_usage_log_does_not_fail_the_call_and_the_ledger_has_it(tmp_path):
    (tmp_path / "u.jsonl").mkdir()  # the log path is a directory: every append fails
    m = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW, ledger_path=tmp_path / "ledger.sqlite")
    assert MeteredLLM(Fake(), "strong", m).complete("a", "b") == "reply"
    assert m.calls_this_month("strong") == 1 and m.log_errors == 1 and m.integrity()["usage_log"]["append_failures"] == 1


# --- a crash or interrupt keeps its unit until a person says otherwise -----------------------------------------------------------------------------------------------------------------------
def test_a_reservation_whose_process_died_keeps_counting_until_it_is_released_with_a_reason(tmp_path):
    m = meter(tmp_path, ceiling=1)
    reservation = m.reserve("strong")  # the process dies here: never settled, never failed
    other = Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)  # a restart
    with pytest.raises(BudgetExceeded):
        MeteredLLM(Fake(), "strong", other).complete("a", "b")
    assert other.unresolved()[0]["id"] == reservation.id
    for reason, by in (("", "A"), ("checked", ""), ("  ", "A")):
        with pytest.raises(AthenaError):
            other.release(reservation.id, reason, by)
    other.release(reservation.id, "the provider dashboard shows no request at that time", "A. Owner")
    MeteredLLM(Fake(), "strong", other).complete("a", "b")
    with pytest.raises(AthenaError):
        other.release(reservation.id, "again", "A")  # only an unresolved reservation can be released


def test_an_interrupt_in_the_middle_of_a_call_leaves_the_unit_counted(tmp_path):
    class Interrupted(Fake):
        def complete(self, system, user):
            raise KeyboardInterrupt

    m = meter(tmp_path, ceiling=5)
    with pytest.raises(KeyboardInterrupt):
        MeteredLLM(Interrupted(), "strong", m).complete("a", "b")
    assert m.calls_this_month("strong") == 1 and m.unresolved()[0]["state"] == "reserved"


# --- which failures are free, and which are not -----------------------------------------------------------------------------------------------------------------------------------------------
def a(outcome, status=None, n=1):
    return {"n": n, "model": "m", "outcome": outcome, "status": status, "retry": n > 1}


@pytest.mark.parametrize(
    "attempts, state",
    [
        ([a("http_error", 404)], "refused"), ([a("http_error", 401), a("http_error", 404)], "refused"), ([a("http_error", 429), a("http_error", 429, 2)], "refused"), ([], "refused"),
        (None, "unknown"), ([a("timeout")], "unknown"), ([a("network_error")], "unknown"), ([a("http_error", 500)], "unknown"), ([a("http_error", 408)], "unknown"),
        ([a("bad_reply", 200)], "unknown"), ([a("http_error", 404), a("timeout")], "unknown"), ([a("http_error", None)], "unknown"),
    ],
    ids=lambda v: str(v)[:60],
)
def test_a_failure_is_free_only_when_every_attempt_is_known_to_have_been_declined(attempts, state):
    assert classify_failure(attempts) == state


# --- the units: a logical request is not a provider attempt, a retry, a token or a rupee --------------------------------------------------------------------------------------------------
class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body if not isinstance(self._body, str) else json.loads(self._body)


OK = Response(200, {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
OK_NO_USAGE = Response(200, {"choices": [{"message": {"content": "hello"}}]})


def provider(name, steps, retries=1):
    queue = list(steps)

    def post(url, **kw):
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return OpenAICompatibleClient(name, "m", "https://x/v1", "KEY", post=post, sleep=lambda s: None, retries=retries)


def chain(*clients):
    return FallbackLLM(list(clients), HealthRegistry())


def test_a_retry_and_a_fallback_are_provider_attempts_of_one_logical_request(tmp_path):
    m = meter(tmp_path, ceiling=1)
    llm = chain(provider("nvidia", [Response(404, '{"detail": "no"}')]), provider("openrouter", [requests.Timeout("slow"), OK]))
    assert MeteredLLM(llm, "strong", m).complete("a", "b") == "hello"
    got = m.summary()["strong"]
    assert got["logical_requests"] == 1 and got["calls"] == 1 and got["provider_attempts"] == 3 and got["retries"] == 1
    assert got["prompt_tokens"] == 7 and got["completion_tokens"] == 3 and got["calls_without_usage"] == 0 and got["models"] == {"openrouter:m": 1}
    with pytest.raises(BudgetExceeded):  # three attempts used one unit; the second logical request is the one past the ceiling
        MeteredLLM(chain(provider("nvidia", [OK])), "strong", m).complete("a", "b")


def test_a_provider_that_reports_no_usage_is_recorded_as_unreported_not_as_zero(tmp_path):
    m = meter(tmp_path)
    MeteredLLM(chain(provider("openai", [OK])), "mid", m).complete("a", "b")
    MeteredLLM(chain(provider("openai", [OK_NO_USAGE])), "mid", m).complete("a", "b")
    got = m.summary()["mid"]
    assert got["calls"] == 2 and got["calls_without_usage"] == 1 and got["prompt_tokens"] == 7
    with m._ledger._con() as con:
        rows = con.execute("SELECT prompt_tokens, usage_reported FROM reservations ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(7, 1), (None, 0)]


def test_a_chain_every_model_of_which_declined_costs_nothing_and_one_that_timed_out_still_counts(tmp_path):
    m = meter(tmp_path, ceiling=5)
    with pytest.raises(AthenaError):
        MeteredLLM(chain(provider("nvidia", [Response(404, "no")]), provider("openrouter", [Response(401, "no")])), "strong", m).complete("a", "b")
    assert m.calls_this_month("strong") == 0 and m.summary()["strong"]["refused"] == 1
    with pytest.raises(AthenaError):
        MeteredLLM(chain(provider("nvidia", [Response(404, "no")]), provider("openrouter", [requests.Timeout("slow"), requests.Timeout("slow")])), "strong", m).complete("a", "b")
    assert m.calls_this_month("strong") == 1 and m.summary()["strong"]["unknown_outcome"] == 1  # a timed-out request may have been processed


def test_a_cache_hit_reserves_nothing_and_counts_nothing(tmp_path):
    from athena.llm.cache import CachedLLM, ReplyCache

    m = meter(tmp_path, ceiling=1)
    cached = CachedLLM(MeteredLLM(Fake(), "strong", m), "strong", ReplyCache(tmp_path / "c.sqlite"))
    assert cached.complete("a", "b") == "reply" and cached.complete("a", "b") == "reply" and cached.complete("a", "b") == "reply"
    assert m.calls_this_month("strong") == 1  # the two hits were served before the meter; the one miss used the one unit


def test_a_new_month_starts_a_new_count_in_the_ledger(tmp_path):
    day = {"now": NOW}
    m = meter(tmp_path, ceiling=1, clock=lambda: day["now"])
    MeteredLLM(Fake(), "strong", m).complete("a", "b")
    with pytest.raises(BudgetExceeded):
        MeteredLLM(Fake(), "strong", m).complete("a", "b")
    day["now"] = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    MeteredLLM(Fake(), "strong", m).complete("a", "b")
    assert m.calls_this_month("strong") == 1


# --- the command ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_command_prints_the_units_and_what_is_unresolved_and_releases_only_with_a_name_and_a_reason(tmp_path, monkeypatch, capsys):
    m = meter(tmp_path, ceiling=5)
    MeteredLLM(chain(provider("openai", [OK])), "strong", m).complete("a", "b")
    reservation = m.reserve("strong")
    monkeypatch.setattr(budget_module, "Meter", lambda: Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW))
    assert budget_module.main([]) == 0
    out = capsys.readouterr().out
    assert "2 logical requests (ceiling 5)" in out and "1 answered" in out and "1 provider attempts, 0 retries" in out and f"unresolved #{reservation.id}" in out
    assert budget_module.main(["--release", str(reservation.id)]) == 1
    assert budget_module.main(["--release", str(reservation.id), "--by", "A. Owner", "--why", "checked the provider's usage page"]) == 0
    assert m.calls_this_month("strong") == 1
