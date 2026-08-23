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


def test_redis_limiter_uses_atomic_script() -> None:
    client = FakeRedis(1)
    limiter = RedisTokenBucketLimiter(client)
    limiter.check("tenant-a", "ask", capacity=2)
    assert len(client.calls) == 1
    assert client.calls[0][1] == 1


def test_redis_limiter_rejects_when_script_denies() -> None:
    with pytest.raises(HTTPException) as error:
        RedisTokenBucketLimiter(FakeRedis(0)).check("tenant-a", "ask", capacity=1)
    assert error.value.status_code == 429


def test_limiter_factory_defaults_to_local() -> None:
    assert isinstance(build_limiter(), TokenBucketLimiter)
