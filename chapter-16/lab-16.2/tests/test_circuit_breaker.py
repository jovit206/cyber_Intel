import pytest

from app.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_opens_after_three_failures_and_rejects_calls():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            breaker.call("mongodb", lambda: (_ for _ in ()).throw(ConnectionError()))
    assert breaker.state("mongodb") == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.call("mongodb", lambda: "not called")


def test_half_open_probe_recovers_after_timeout():
    now = [100.0]
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30, clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(ConnectionError):
            breaker.call("cassandra", lambda: (_ for _ in ()).throw(ConnectionError()))
    now[0] += 30
    assert breaker.call("cassandra", lambda: "recovered") == "recovered"
    assert breaker.state("cassandra") == CircuitState.CLOSED


def test_backends_track_state_independently_and_half_open_failure_reopens():
    now = [0.0]
    breaker = CircuitBreaker(recovery_timeout=30, clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(OSError):
            breaker.call("neo4j", lambda: (_ for _ in ()).throw(OSError()))
    now[0] = 31
    with pytest.raises(OSError):
        breaker.call("neo4j", lambda: (_ for _ in ()).throw(OSError()))
    assert breaker.state("neo4j") == CircuitState.OPEN
    assert breaker.state("mongodb") == CircuitState.CLOSED
