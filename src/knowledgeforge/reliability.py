import logging
import time
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypeVar

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from knowledgeforge.config import get_settings

T = TypeVar("T")


def _redis_key_namespace() -> str:
    """Return the Redis key namespace including environment for isolation."""
    settings = get_settings()
    return f"knowledgeforge:{settings.environment}"


def make_redis_key(suffix: str) -> str:
    """Build a Redis key with environment namespace prefix."""
    return f"{_redis_key_namespace()}:{suffix}"


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
    """Per-process circuit breaker.

    Used when Redis is unavailable or not configured. State is not shared
    across processes — see RedisCircuitBreaker for shared-state variant.
    """

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
            self.opened_at = None

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


class RedisCircuitBreaker:
    """Redis-backed circuit breaker with atomic Lua operations.

    Falls back to per-process CircuitBreaker when Redis is unreachable.
    Uses epoch time (time.time()) for cross-process consistency.
    """

    _SCRIPT = """
    local key = KEYS[1]
    local failure_threshold = tonumber(ARGV[1])
    local recovery_seconds = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local operation = ARGV[4]

    local state = redis.call('HMGET', key, 'failures', 'opened_at')
    local failures = tonumber(state[1]) or 0
    local opened_at = tonumber(state[2])

    if operation == 'ensure_available' then
        if opened_at and (now - opened_at) < recovery_seconds then
            return {0, failures, opened_at}
        else
            if opened_at and (now - opened_at) >= recovery_seconds then
                failures = 0
                opened_at = nil
            end
            return {1, failures, opened_at}
        end
    elseif operation == 'record_success' then
        failures = 0
        opened_at = nil
        redis.call('HSET', key, 'failures', failures, 'opened_at', '')
        redis.call('EXPIRE', key, 120)
        return {1, failures, nil}
    elseif operation == 'record_failure' then
        failures = failures + 1
        if failures >= failure_threshold then
            opened_at = now
        else
            opened_at = nil
        end
        redis.call('HSET', key, 'failures', failures, 'opened_at', opened_at or '')
        redis.call('EXPIRE', key, 120)
        return {1, failures, opened_at}
    end
    return {0, failures, opened_at}
    """

    def __init__(self, client: object, key: str, failure_threshold: int, recovery_seconds: float) -> None:
        self._client = client
        self._key = key
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._fallback = CircuitBreaker(failure_threshold, recovery_seconds)

    def _ensure_available(self) -> None:
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT,
                1,
                self._key,
                self._failure_threshold,
                self._recovery_seconds,
                time.time(),
                "ensure_available",
            )
            if int(result[0]) == 0:
                raise CircuitOpenError("external service circuit is open")
        except Exception:
            logging.getLogger("knowledgeforge.reliability").warning(
                "Redis circuit breaker unavailable; using local fallback", exc_info=True
            )
            self._fallback.ensure_available()

    def record_success(self) -> None:
        try:
            self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT,
                1,
                self._key,
                self._failure_threshold,
                self._recovery_seconds,
                time.time(),
                "record_success",
            )
        except Exception:
            logging.getLogger("knowledgeforge.reliability").warning(
                "Redis circuit breaker unavailable; using local fallback", exc_info=True
            )
            self._fallback.record_success()

    def record_failure(self) -> None:
        try:
            self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT,
                1,
                self._key,
                self._failure_threshold,
                self._recovery_seconds,
                time.time(),
                "record_failure",
            )
        except Exception:
            logging.getLogger("knowledgeforge.reliability").warning(
                "Redis circuit breaker unavailable; using local fallback", exc_info=True
            )
            self._fallback.record_failure()

    def call(self, function: Callable[[], T]) -> T:
        self._ensure_available()
        try:
            result = function()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


def build_circuit_breaker(
    redis_client: object | None,
    key: str,
    failure_threshold: int,
    recovery_seconds: float,
) -> CircuitBreaker | RedisCircuitBreaker:
    """Build a circuit breaker, using Redis if a client is provided."""
    if redis_client is None:
        return CircuitBreaker(failure_threshold, recovery_seconds)
    return RedisCircuitBreaker(redis_client, key, failure_threshold, recovery_seconds)
