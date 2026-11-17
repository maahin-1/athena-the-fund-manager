"""The run envelope (WP06): what a run was made from and what it asked of which model, recorded without changing its callers, with `unknown` for what was not reported and no credential inside.

Fakes only; no provider is called and no environment value is read."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.agents.base import Specialist, SpecialistSpec
from athena.council import debate, judge
from athena.llm import envelope
from athena.llm.budget import Meter, MeteredLLM
from athena.llm.cache import CachedLLM, ReplyCache
from athena.verdicts.calibration import Calibration
from athena.verdicts.runner import VerdictRunner
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class Model:
    names = ["nvidia:big"]
    last_source = "nvidia:big"
    last_usage = {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48}
    last_failures = (("openai:gone", "HTTP 404"),)
    last_attempts = [{"n": 1, "model": "openai:gone", "outcome": "http_error", "status": 404, "retry": False}, {"n": 1, "model": "nvidia:big", "outcome": "ok", "status": 200, "retry": False}]

    def complete(self, system, user):
        return "reply"


class Silent(Model):
    last_usage = {}
    last_attempts = None
    last_failures = ()


def meter(tmp_path):
    return Meter(tmp_path / "u.jsonl", tmp_path / "b.json", lambda: NOW)


# --- what a call adds to the run being recorded ---------------------------------------------------------------------------------------------------------------------------------------------
def test_a_run_records_each_logical_request_with_the_exact_model_the_attempts_the_usage_and_digests_of_the_prompts(tmp_path):
    with envelope.record_run("run-1", lambda: NOW) as run:
        assert MeteredLLM(Model(), "strong", meter(tmp_path)).complete("system text", "user text") == "reply"
    (call,) = run.calls
    assert call["tier"] == "strong" and call["origin"] == "live" and call["responding_model"] == "nvidia:big" and call["outcome"] == "answered"
    assert call["usage"] == {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48} and [a["outcome"] for a in call["attempts"]] == ["http_error", "ok"]
    assert call["prompt_sha256"] == envelope.digest("system text") and call["input_sha256"] == envelope.digest("user text") and call["fallbacks"] == ["openai:gone: HTTP 404"]
    assert call["chars_in"] == len("system text") + len("user text") and call["chars_out"] == 5 and "system text" not in json.dumps(run.envelope())  # a digest, never the prompt


def test_what_a_provider_did_not_report_is_unknown_never_an_invented_number(tmp_path):
    with envelope.record_run(clock=lambda: NOW) as run:
        MeteredLLM(Silent(), "strong", meter(tmp_path)).complete("s", "u")
    (call,) = run.calls
    assert call["usage"] == "unknown" and call["attempts"] == "unknown" and call["responding_model"] == "nvidia:big"

    class Anonymous(Silent):
        last_source = ""

    with envelope.record_run(clock=lambda: NOW) as run:
        MeteredLLM(Anonymous(), "strong", meter(tmp_path)).complete("s", "u")
    assert run.calls[0]["responding_model"] == "unknown"


def test_a_failed_request_is_recorded_with_how_it_ended(tmp_path):
    class Declined(Model):
        last_attempts = [{"n": 1, "model": "a", "outcome": "http_error", "status": 401, "retry": False}]

        def complete(self, system, user):
            raise RuntimeError("HTTP 401 Authorization: Bearer sk-livekey123456789012345")

    class Slow(Declined):
        last_attempts = [{"n": 1, "model": "a", "outcome": "timeout", "status": None, "retry": False}]

    m = meter(tmp_path)
    with envelope.record_run(clock=lambda: NOW) as run:
        for model in (Declined(), Slow()):
            with pytest.raises(RuntimeError):
                MeteredLLM(model, "strong", m).complete("s", "u")
    assert [c["outcome"] for c in run.calls] == ["refused", "unknown_outcome"]
    assert "sk-livekey" not in json.dumps(run.envelope())  # the provider's error text may carry a credential: scrubbed on the way in


def test_a_run_that_is_not_being_recorded_costs_nothing_and_changes_nothing(tmp_path):
    assert envelope.current() is None
    assert MeteredLLM(Model(), "strong", meter(tmp_path)).complete("s", "u") == "reply"
    envelope.note_call(tier="x")
    envelope.note_validation("x", ["y"])


def test_recorders_nest_and_the_outer_one_is_active_again_afterwards(tmp_path):
    with envelope.record_run("outer", lambda: NOW) as outer:
        with envelope.record_run("inner", lambda: NOW) as inner:
            envelope.note_call(tier="a")
        envelope.note_call(tier="b")
    assert [c["tier"] for c in inner.calls] == ["a"] and [c["tier"] for c in outer.calls] == ["b"] and envelope.current() is None


def test_the_cache_says_where_a_reply_came_from_and_which_model_first_made_it(tmp_path):
    cache = ReplyCache(tmp_path / "c.sqlite")
    with envelope.record_run(clock=lambda: NOW) as run:
        llm = CachedLLM(MeteredLLM(Model(), "strong", meter(tmp_path)), "strong", cache)
        llm.complete("s", "u")
        llm.complete("s", "u")
    first, second = run.calls
    assert first["origin"] == "live" and first["cache"] == "miss" and first["responding_model"] == "nvidia:big"
    assert second["origin"] == "cache" and second["cache_origin_model"] == "nvidia:big" and second["responding_model"] == "cache (nvidia:big)" and second["attempts"] == [] and second["usage"].startswith("none")
    assert run.envelope()["summary"] == {"logical_requests": 2, "answered_live": 1, "from_cache": 1, "provider_attempts": 2, "provider_attempts_known": 2, "calls_with_unknown_attempts": 0, "validation_failures": 0}


def _totals(*attempts):
    rec = envelope.RunRecorder("r", lambda: NOW)
    for a in attempts:
        rec.add_call(**({} if a is _ABSENT else {"attempts": a}))
    return {k: v for k, v in rec.envelope(code="x")["summary"].items() if k.startswith(("provider_attempts", "calls_with"))}


_ABSENT = object()
_ONE = [{"n": 1, "outcome": "ok", "status": 200, "retry": False}]


def test_attempt_totals_count_only_calls_that_reported_a_list():
    assert _totals(_ONE, _ONE + _ONE, []) == {"provider_attempts": 3, "provider_attempts_known": 3, "calls_with_unknown_attempts": 0}
    assert _totals([]) == {"provider_attempts": 0, "provider_attempts_known": 0, "calls_with_unknown_attempts": 0}  # a known zero: nothing was sent


def test_an_unknown_attempt_history_is_never_counted_as_seven_or_zero():
    assert _totals("unknown") == {"provider_attempts": "unknown", "provider_attempts_known": 0, "calls_with_unknown_attempts": 1}
    assert _totals(_ABSENT) == {"provider_attempts": "unknown", "provider_attempts_known": 0, "calls_with_unknown_attempts": 1}
    assert _totals(None, 7, {"n": 1}) == {"provider_attempts": "unknown", "provider_attempts_known": 0, "calls_with_unknown_attempts": 3}


def test_a_mix_gives_a_known_subtotal_that_is_not_presented_as_the_total():
    assert _totals(_ONE + _ONE, "unknown", [], _ABSENT) == {"provider_attempts": "unknown", "provider_attempts_known": 2, "calls_with_unknown_attempts": 2}


def test_an_unmetered_client_behind_the_cache_is_still_part_of_the_run(tmp_path):
    with envelope.record_run(clock=lambda: NOW) as run:
        CachedLLM(Model(), "strong", ReplyCache(tmp_path / "c.sqlite")).complete("s", "u")
    assert run.calls[0]["metered"] is False and run.calls[0]["responding_model"] == "nvidia:big" and run.calls[0]["origin"] == "live"


# --- validation failures ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, system, user):
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_a_reply_that_failed_validation_is_in_the_run_even_when_a_repair_succeeded():
    spec = SpecialistSpec("Test", "persona", critical=("a",))
    bad = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "a is 99.9."})
    good = json.dumps({"signal": "bullish", "confidence": 70, "reasoning": "a is 42.5."})
    with envelope.record_run(clock=lambda: NOW) as run:
        Specialist(spec, Scripted(bad, good)).analyze({"metrics": {"a": {"value": 42.5}}})
    assert len(run.validation) == 1 and run.validation[0]["source"] == "specialist:Test" and "99.9" in run.validation[0]["errors"][0]


def test_council_replies_that_failed_validation_are_in_the_run():
    from test_council_evidence import sheet

    with envelope.record_run(clock=lambda: NOW) as run:
        debate.argue("bull", sheet(), Scripted(json.dumps({"case": "x", "evidence": [], "weakest_point": "y"}), json.dumps({"case": "x", "evidence": [], "weakest_point": "y"})))
        judge.decide(sheet(), None, Scripted("not json"))
    assert {v["source"] for v in run.validation} == {"council:bull", "council:judge"}


# --- no credential in a frozen record ----------------------------------------------------------------------------------------------------------------------------------------------------------
def test_scrub_removes_credentials_in_the_shapes_they_arrive_in_and_leaves_counts_alone():
    secret = "hunter2-local-secret"
    text = f"HTTP 401 Authorization: Bearer abcdefghijklmnop12345 x-api-key: AIzaSyDfake1234567890abcdefghijk key=sk-proj-abcdefghijklmnopqrstu nvapi-abcdefghijklmnopqrstuv password={secret}"
    cleaned = envelope.scrub(text, [secret])
    for leaked in ("abcdefghijklmnop12345", "AIzaSyDfake", "sk-proj-abcdef", "nvapi-abcdef", secret):
        assert leaked not in cleaned
    assert envelope.REDACTED in cleaned
    body = {"prompt_tokens": 12, "Authorization": "Bearer x", "headers": {"x-api-key": "k", "Content-Type": "application/json"}, "api_key": "k", "note": f"token {secret}", "list": [{"password": "p"}, "ok"]}
    out = envelope.redact(body, [secret])
    assert out["prompt_tokens"] == 12 and out["Authorization"] == out["api_key"] == out["headers"]["x-api-key"] == out["list"][0]["password"] == envelope.REDACTED
    assert out["headers"]["Content-Type"] == "application/json" and secret not in json.dumps(out)


def test_the_frozen_envelope_holds_no_environment_value_and_scrubs_the_secrets_the_caller_names(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-environment-secret-value-123456")  # never read by the recorder
    with envelope.record_run(clock=lambda: NOW) as run:
        run.inputs["source"] = "packet hash only"
        run.add_call(tier="strong", error="the key local-test-secret-9876 was refused", responding_model="m")
    frozen = json.dumps(run.envelope(secrets=["local-test-secret-9876"]))
    assert "environment-secret" not in frozen and "local-test-secret" not in frozen and "NVIDIA_API_KEY" not in frozen


# --- the code version and the record's shape -----------------------------------------------------------------------------------------------------------------------------------------------
def test_the_code_version_is_read_from_the_git_files_without_starting_a_process(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert envelope.code_version(tmp_path) == "0123456789ab"
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack-refs\nfedcba9876543210fedcba9876543210fedcba98 refs/heads/main\n")
    assert envelope.code_version(tmp_path) == "fedcba987654"
    (git / "HEAD").write_text("aaaaaaaaaaaabbbbbbbbbbbbccccccccccccdddd\n")
    assert envelope.code_version(tmp_path) == "aaaaaaaaaaaa"
    (git / "HEAD").write_text("ref: refs/heads/gone\n")
    (git / "packed-refs").unlink()
    assert envelope.code_version(tmp_path) == "unknown"


def test_the_envelope_has_a_schema_identity_times_and_says_unknown_for_what_nobody_set():
    with envelope.record_run("r1", lambda: NOW) as run:
        run.schemas["timing"] = "timing/1"
    body = run.envelope(code="abc123")
    assert body["schema"] == envelope.SCHEMA and body["run_id"] == "r1" and body["code"] == {"version": "abc123"} and body["schemas"] == {"timing": "timing/1"}
    assert body["times"] == {"decision_completed_at": "unknown", "entry_policy": "unknown"} and body["calls"] == [] and body["validation_failures"] == []
    json.dumps(body)


# --- the verdict carries its envelope ----------------------------------------------------------------------------------------------------------------------------------------------------------
DAY = date(2026, 10, 9)
RUN_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


class MeteredAnalyst:
    """An analyst whose model call goes through a metered client: the run records it without the runner or the analyst knowing."""

    def __init__(self, meter_, signal="bullish"):
        self.llm, self.signal = MeteredLLM(Model(), "mid", meter_), signal

    def analyze(self, packet):
        self.llm.complete("persona and contract", json.dumps(packet, sort_keys=True))
        return {"signal": self.signal, "confidence": 80, "reasoning": "r", "data_coverage": "full", "missing": [], "verification": {"scheme": "numeric_consistency/1", "figures_checked": 0, "binds_claims": False}}


def test_a_stored_verdict_carries_the_envelope_of_the_run_that_made_it(tmp_path):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.set_universe([("TCS", "stock", "TCS Ltd", "INE000")], RUN_NOW)
        w.put_packets([("TCS", "technical", DAY, [], {"metrics": {"last_close": {"value": 100.0}}}), ("TCS", "fundamentals", DAY, [], {"latest_annual_period": "2026-03-31", "latest_quarter": "2026-06-30", "metrics": {}})], RUN_NOW)
    m = meter(tmp_path)
    specialists = {name: MeteredAnalyst(m) for name in ("quant_technical", "valuation", "moat_quality", "earnings_intelligence")}
    stored = VerdictRunner(path, specialists, Calibration(), lambda: RUN_NOW + timedelta(minutes=1), model="fake").make("TCS")
    run = stored["payload"]["run"]
    assert run["schema"] == envelope.SCHEMA and run["summary"]["logical_requests"] == 4 and run["summary"]["answered_live"] == 4 and {c["responding_model"] for c in run["calls"]} == {"nvidia:big"}
    assert set(run["inputs"]) >= {"technical_packet_sha256", "fundamentals_packet_sha256", "data_as_of", "packet_built_at"} and len(run["inputs"]["technical_packet_sha256"]) == 64
    assert run["policies"]["entry"] == stored["payload"]["timing"]["entry_policy"] and "settlement" in run["policies"]["costs"] and run["policies"]["calendar"]["source"]
    assert run["times"]["decision_completed_at"] == stored["payload"]["timing"]["decision_completed_at"] and run["schemas"]["specialist_verification"]["valuation"] == "numeric_consistency/1"
    assert run["code"]["version"] and "credential" not in json.dumps(run).lower()
    json.dumps(stored["payload"])  # the whole payload still stores


def test_an_older_stored_verdict_without_an_envelope_is_still_readable(tmp_path):
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.set_universe([("TCS", "stock", "TCS Ltd", "INE000")], RUN_NOW)
        w.put_verdict("TCS", RUN_NOW, DAY, 100.0, date(2026, 3, 31), date(2026, 6, 30), {"status": "ok", "verdict": {"verdict": "Hold"}})
    with Warehouse(path) as w:
        assert "run" not in w.latest_verdict("TCS")["payload"] and w.latest_verdict("TCS")["payload"]["verdict"]["verdict"] == "Hold"
