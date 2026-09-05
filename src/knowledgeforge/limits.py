import logging
import time
from collections.abc import Hashable
from threading import Lock
from time import monotonic

from fastapi import HTTPException, status

logger = logging.getLogger("knowledgeforge.limits")


class TokenBucketLimiter:
    """Per-process token bucket with bounded memory.

    The bucket map is capped; once full, buckets that have fully refilled
    (idle subjects) are evicted to admit new ones, so a flood of one-off
    caller addresses cannot grow the map without bound.
    """

    MAX_BUCKETS = 10_000

    def __init__(self) -> None:
        self._buckets: dict[tuple[Hashable, str], tuple[float, float]] = {}
        self._lock = Lock()

    def check(self, subject: Hashable, key: str, capacity: int, window_seconds: float = 60) -> None:
        if capacity <= 0:
            raise ValueError("rate-limit capacity must be positive")
        now = monotonic()
        refill_rate = capacity / window_seconds
        bucket_key = (subject, key)
        with self._lock:
            tokens, updated = self._buckets.get(bucket_key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - updated) * refill_rate)
            if tokens < 1:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
                )
            if len(self._buckets) >= self.MAX_BUCKETS and bucket_key not in self._buckets:
                self._evict_idle(capacity, refill_rate)
            self._buckets[bucket_key] = (tokens - 1, now)

    def _evict_idle(self, capacity: int, refill_rate: float) -> None:
        """Make room for a new subject: drop refilled buckets, else the oldest.

        Fully refilled buckets are safe to drop (they re-create on demand with
        the same state). If nothing is idle, the oldest entry is dropped
        instead; it re-creates with full tokens, which can briefly grant one
        subject extra budget — a deliberate trade against unbounded memory
        under a distinct-subject flood.
        """
        now = monotonic()
        for key, (tokens, updated) in list(self._buckets.items()):
            if tokens + (now - updated) * refill_rate >= capacity:
                del self._buckets[key]
                if len(self._buckets) < self.MAX_BUCKETS:
                    return
        if self._buckets:
            self._buckets.pop(next(iter(self._buckets)))


class RedisTokenBucketLimiter:
    """Redis-backed limiter using one atomic Lua script per request.

    Falls back to a per-process limiter when Redis is unreachable, so a Redis
    outage degrades rate limiting instead of failing every protected request.
    """

    _SCRIPT = """
    local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
    local tokens = tonumber(state[1])
    local updated = tonumber(state[2])
    local now = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local refill = tonumber(ARGV[3])
    if not tokens or not updated then
      tokens = capacity
      updated = now
    end
    tokens = math.min(capacity, tokens + (now - updated) * refill)
    if tokens < 1 then
      return 0
    end
    redis.call('HSET', KEYS[1], 'tokens', tokens - 1, 'updated', now)
    redis.call('EXPIRE', KEYS[1], 120)
    return 1
    """

    def __init__(self, client: object) -> None:
        self._client = client
        self._fallback = TokenBucketLimiter()

    def check(self, subject: Hashable, key: str, capacity: int, window_seconds: float = 60) -> None:
        if capacity <= 0:
            raise ValueError("rate-limit capacity must be positive")
        try:
            allowed = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT,
                1,
                f"knowledgeforge:limit:{subject}:{key}",
                # Epoch time, not monotonic: monotonic clocks are per-process
                # and would corrupt refill arithmetic across replicas.
                time.time(),
                capacity,
                capacity / window_seconds,
            )
        except Exception:
            logger.warning(
                "Redis rate limiter unavailable for %s/%s; using local fallback",
                subject,
                key,
                exc_info=True,
            )
            self._fallback.check(subject, key, capacity, window_seconds)
            return
        if int(allowed) != 1:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
            )


def build_limiter(redis_url: str = "") -> TokenBucketLimiter | RedisTokenBucketLimiter:
    """Use shared Redis only when explicitly configured; remain local by default."""
    if not redis_url:
        return TokenBucketLimiter()
    try:
        import redis  # type: ignore[import-not-found]

        return RedisTokenBucketLimiter(redis.Redis.from_url(redis_url, decode_responses=True))
    except ImportError as exc:
        raise RuntimeError("REDIS_URL is configured but the redis package is unavailable") from exc


limiter = build_limiter()
