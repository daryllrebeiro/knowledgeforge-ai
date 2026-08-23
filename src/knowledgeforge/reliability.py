from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

T = TypeVar("T")


def with_retry(function: Callable[..., T]) -> Callable[..., T]:  # noqa: UP047
    return retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.2, max=2),
        reraise=True,
    )(function)


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 30) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = Lock()

    def call(self, function: Callable[[], T]) -> T:
        with self._lock:
            if self.opened_at is not None:
                if monotonic() - self.opened_at < self.recovery_seconds:
                    raise CircuitOpenError("external service circuit is open")
                self.opened_at = None
        try:
            result = function()
        except Exception:
            with self._lock:
                self.failures += 1
                if self.failures >= self.failure_threshold:
                    self.opened_at = monotonic()
            raise
        with self._lock:
            self.failures = 0
        return result
