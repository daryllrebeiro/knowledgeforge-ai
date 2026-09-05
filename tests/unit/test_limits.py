from uuid import UUID

import pytest
from fastapi import HTTPException

from knowledgeforge.limits import RedisTokenBucketLimiter, TokenBucketLimiter, build_limiter


def test_limiter_rejects_after_capacity() -> None:
    limiter = TokenBucketLimiter()
    tenant_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    limiter.check(tenant_id, "ask", capacity=1)
    with pytest.raises(HTTPException) as error:
        limiter.check(tenant_id, "ask", capacity=1)
    assert error.value.status_code == 429


class FakeRedis:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def eval(self, *args: object) -> int:
        self.calls.append(args)
        return self.result


class BrokenRedis:
    def eval(self, *args: object) -> int:
        raise ConnectionError("redis is down")


def test_redis_limiter_uses_atomic_script() -> None:
    client = FakeRedis(1)
    limiter = RedisTokenBucketLimiter(client)
    limiter.check("tenant-a", "ask", capacity=2)
    assert len(client.calls) == 1
    assert client.calls[0][1] == 1


def test_redis_limiter_stores_epoch_time_not_monotonic() -> None:
    """Regression: a per-process monotonic clock corrupts shared refill state."""
    client = FakeRedis(1)
    limiter = RedisTokenBucketLimiter(client)
    limiter.check("tenant-a", "ask", capacity=2)
    # ARGV[1] is the "now" argument; epoch seconds are ~1.7e9, monotonic is small.
    assert float(client.calls[0][2]) > 1_000_000_000


def test_redis_limiter_rejects_when_script_denies() -> None:
    with pytest.raises(HTTPException) as error:
        RedisTokenBucketLimiter(FakeRedis(0)).check("tenant-a", "ask", capacity=1)
    assert error.value.status_code == 429


def test_redis_limiter_falls_back_locally_when_redis_is_down() -> None:
    """Regression: a Redis outage must degrade rate limiting, not 500 requests."""
    limiter = RedisTokenBucketLimiter(BrokenRedis())

    limiter.check("tenant-a", "ask", capacity=1)  # served by local fallback
    with pytest.raises(HTTPException) as error:
        limiter.check("tenant-a", "ask", capacity=1)  # local bucket exhausted
    assert error.value.status_code == 429


def test_limiter_factory_defaults_to_local() -> None:
    assert isinstance(build_limiter(), TokenBucketLimiter)


def test_local_limiter_memory_is_bounded_under_distinct_subject_flood() -> None:
    """A flood of one-off caller addresses must not grow the bucket map forever."""
    limiter = TokenBucketLimiter()
    limiter.MAX_BUCKETS = 5

    for index in range(50):
        limiter.check(f"subject-{index}", "ask", capacity=1000)

    assert len(limiter._buckets) <= 5
