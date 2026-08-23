import os

import pytest
from fastapi import HTTPException

from knowledgeforge.limits import RedisTokenBucketLimiter

pytestmark = pytest.mark.integration
redis = pytest.importorskip("redis")


def test_two_limiter_instances_share_rate_limit() -> None:
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is not configured")
    first = RedisTokenBucketLimiter(redis.Redis.from_url(url, decode_responses=True))
    second = RedisTokenBucketLimiter(redis.Redis.from_url(url, decode_responses=True))
    subject = "integration-shared-subject"
    key = "integration-shared-key"
    client = redis.Redis.from_url(url, decode_responses=True)
    client.delete(f"knowledgeforge:limit:{subject}:{key}")
    try:
        first.check(subject, key, capacity=1)
        with pytest.raises(HTTPException) as error:
            second.check(subject, key, capacity=1)
        assert error.value.status_code == 429
    finally:
        client.delete(f"knowledgeforge:limit:{subject}:{key}")
