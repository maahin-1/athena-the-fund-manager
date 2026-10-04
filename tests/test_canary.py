import logging
from datetime import datetime, timezone

from athena.canary import CanaryCheck, run_canary
from athena.contracts import Record
from athena.fallback import HealthRegistry

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)  # Monday


def nav_record(as_of=datetime(2026, 10, 2, 18, 0, tzinfo=UTC), payload=None):
    return Record("mf.nav", "119551", as_of, "mftool", payload if payload is not None else {"nav": 10.5})


def check(fetch, keys=("nav",), name="mftool"):
    return CanaryCheck(name=name, dataset="mf.nav", fetch=fetch, required_payload_keys=keys)


def test_healthy_adapter_passes_and_clears_prior_degradation():
    health = HealthRegistry()
    health.mark_degraded("mftool", "old failure")
    results = run_canary([check(lambda: nav_record())], health, NOW)
    assert [(r.name, r.ok, r.reason) for r in results] == [("mftool", True, None)]
    assert not health.is_degraded("mftool")


def test_exception_degrades_adapter():
    def fetch():
        raise ConnectionError("timeout")

    health = HealthRegistry()
    results = run_canary([check(fetch)], health, NOW)
    assert not results[0].ok
    assert "ConnectionError: timeout" in results[0].reason
    assert health.is_degraded("mftool")


def test_none_and_empty_payload_degrade_adapter():
    health = HealthRegistry()
    results = run_canary(
        [check(lambda: None, name="a"), check(lambda: nav_record(payload={}), name="b")],
        health,
        NOW,
    )
    assert [r.reason for r in results] == ["empty response", "empty response"]
    assert health.degraded().keys() == {"a", "b"}


def test_missing_required_key_means_schema_changed():
    health = HealthRegistry()
    results = run_canary([check(lambda: nav_record(payload={"price": 1}))], health, NOW)
    assert results[0].reason == "schema changed: missing keys ['nav']"
    assert health.is_degraded("mftool")


def test_stale_data_degrades_adapter_with_staleness_message():
    old = nav_record(as_of=datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    health = HealthRegistry()
    results = run_canary([check(lambda: old)], health, NOW)
    assert not results[0].ok
    assert "mf.nav data is" in results[0].reason


def test_one_failure_does_not_stop_other_checks():
    def fetch():
        raise RuntimeError("x")

    health = HealthRegistry()
    results = run_canary([check(fetch, name="bad"), check(lambda: nav_record(), name="good")], health, NOW)
    assert [(r.name, r.ok) for r in results] == [("bad", False), ("good", True)]
    assert health.is_degraded("bad") and not health.is_degraded("good")


def test_failure_is_logged_as_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="athena.canary"):
        run_canary([check(lambda: None)], HealthRegistry(), NOW)
    assert any("mftool" in message and "empty response" in message for message in caplog.messages)
