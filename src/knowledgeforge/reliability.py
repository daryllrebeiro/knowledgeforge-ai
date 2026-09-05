from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

T = TypeVar("T")


def with_retry(
    function: Callable[..., T], *, attempts: int = 2  # noqa: UP047
) -> Callable[..., T]:
    """Retry an idempotent external call once (by default) with backoff."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    return retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(attempts),
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

    def _ensure_available(self) -> None:
        if self.opened_at is not None:
            if monotonic() - self.opened_at < self.recovery_seconds:
                raise CircuitOpenError("external service circuit is open")
            self.opened_at = None

    def ensure_available(self) -> None:
        """Raise CircuitOpenError while the circuit is open.

        Streaming callers use this before their first token, then
        ``record_success``/``record_failure`` — ``call`` cannot wrap them
        because a generator body runs lazily.
        """
        with self._lock:
            self._ensure_available()

    def record_success(self) -> None:
        """Mark a call successful; exposed for streaming, which ``call`` cannot wrap."""
        with self._lock:
            self.failures = 0

    def record_failure(self) -> None:
        """Mark a call failed and open the circuit at the threshold."""
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = monotonic()

    def call(self, function: Callable[[], T]) -> T:
        with self._lock:
            self._ensure_available()
        try:
            result = function()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result
