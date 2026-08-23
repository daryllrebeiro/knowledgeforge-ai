from collections.abc import Hashable
from threading import Lock
from time import monotonic

from fastapi import HTTPException, status


class TokenBucketLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[Hashable, str], tuple[float, float]] = {}
        self._lock = Lock()

    def check(self, subject: Hashable, key: str, capacity: int, window_seconds: float = 60) -> None:
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
            self._buckets[bucket_key] = (tokens - 1, now)


class RedisTokenBucketLimiter:
    """Redis-backed limiter using one atomic Lua script per request."""

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

    def check(self, subject: Hashable, key: str, capacity: int, window_seconds: float = 60) -> None:
        if capacity <= 0:
            raise ValueError("rate-limit capacity must be positive")
        allowed = self._client.eval(  # type: ignore[attr-defined]
            self._SCRIPT,
            1,
            f"knowledgeforge:limit:{subject}:{key}",
            monotonic(),
            capacity,
            capacity / window_seconds,
        )
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
