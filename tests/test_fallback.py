import pytest

from athena.contracts import AllSourcesFailed
from athena.fallback import FallbackChain, HealthRegistry


def ok(value):
    return lambda *a, **k: value


def boom(*a, **k):
    raise ConnectionError("endpoint moved")


def test_first_healthy_source_wins_and_is_named():
    chain = FallbackChain([("jugaad", ok(101.5)), ("yahoo", ok(999))])
    result = chain.run("SBIN")
    assert result.value == 101.5
    assert result.source == "jugaad"
    assert result.failures == ()


def test_falls_through_on_exception_and_records_failure():
    chain = FallbackChain([("jugaad", boom), ("nsepython", ok(102.0))])
    result = chain.run("SBIN")
    assert result.source == "nsepython"
    assert result.failures == (("jugaad", "ConnectionError: endpoint moved"),)


def test_none_result_counts_as_failure():
    chain = FallbackChain([("jugaad", ok(None)), ("yahoo", ok(5))])
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "returned no data"),)


def test_empty_sequence_counts_as_failure():
    chain = FallbackChain([("a", ok([])), ("b", ok([1]))])
    result = chain.run()
    assert result.source == "b"
    assert result.failures == (("a", "returned no data"),)


def test_arguments_are_forwarded():
    chain = FallbackChain([("a", lambda symbol, timeframe="1d": (symbol, timeframe))])
    assert chain.run("SBIN", timeframe="1h").value == ("SBIN", "1h")


def test_degraded_sources_are_skipped():
    health = HealthRegistry()
    health.mark_degraded("jugaad", "canary: empty response")
    chain = FallbackChain([("jugaad", ok(1)), ("yahoo", ok(2))])
    result = chain.run("SBIN", health=health)
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "skipped, degraded: canary: empty response"),)


def test_all_failed_raises_with_every_reason():
    health = HealthRegistry()
    health.mark_degraded("a", "down")
    chain = FallbackChain([("a", ok(1)), ("b", boom), ("c", ok(None))])
    with pytest.raises(AllSourcesFailed) as err:
        chain.run("X", health=health)
    message = str(err.value)
    assert "a: skipped, degraded: down" in message
    assert "b: ConnectionError: endpoint moved" in message
    assert "c: returned no data" in message


def test_empty_chain_is_rejected():
    with pytest.raises(ValueError, match="at least one source"):
        FallbackChain([])


def test_health_registry_lifecycle():
    health = HealthRegistry()
    assert not health.is_degraded("x")
    health.mark_degraded("x", "bad")
    assert health.is_degraded("x")
    assert health.reason("x") == "bad"
    assert health.degraded() == {"x": "bad"}
    health.mark_healthy("x")
    assert not health.is_degraded("x")
    assert health.reason("x") is None
    assert health.degraded() == {}
