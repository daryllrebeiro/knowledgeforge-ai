from uuid import UUID

import pytest
from fastapi import HTTPException

from knowledgeforge.limits import TokenBucketLimiter


def test_limiter_rejects_after_capacity() -> None:
    limiter = TokenBucketLimiter()
    tenant_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    limiter.check(tenant_id, "ask", capacity=1)
    with pytest.raises(HTTPException) as error:
        limiter.check(tenant_id, "ask", capacity=1)
    assert error.value.status_code == 429
