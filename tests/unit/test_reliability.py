import pytest

from knowledgeforge.reliability import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60)

    def fail() -> None:
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        breaker.call(fail)
    with pytest.raises(RuntimeError):
        breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: None)


def test_circuit_breaker_resets_after_success() -> None:
    breaker = CircuitBreaker(failure_threshold=2)
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.failures == 0
