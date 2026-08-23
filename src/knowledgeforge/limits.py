from threading import Lock
from time import monotonic
from uuid import UUID

from fastapi import HTTPException, status


class TokenBucketLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[UUID, str], tuple[float, float]] = {}
        self._lock = Lock()

    def check(self, tenant_id: UUID, key: str, capacity: int, window_seconds: float = 60) -> None:
        now = monotonic()
        refill_rate = capacity / window_seconds
        bucket_key = (tenant_id, key)
        with self._lock:
            tokens, updated = self._buckets.get(bucket_key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - updated) * refill_rate)
            if tokens < 1:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded"
                )
            self._buckets[bucket_key] = (tokens - 1, now)


limiter = TokenBucketLimiter()
