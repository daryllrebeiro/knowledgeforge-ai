"""Redis-backed budget counters for per-tenant cost control.

Provides atomic daily budget tracking for token usage (/ask) and extraction calls.
"""

import time
from typing import Optional

from knowledgeforge.config import get_settings
from knowledgeforge.reliability import make_redis_key


class BudgetExceeded(Exception):
    """Raised when a tenant's budget is exceeded."""

    def __init__(self, budget_type: str, limit: int, used: int):
        self.budget_type = budget_type
        self.limit = limit
        self.used = used
        super().__init__(f"{budget_type} budget exceeded: {used}/{limit}")


class RedisBudgetCounter:
    """Redis-backed daily budget counter with atomic increments.

    Uses a single Redis key per tenant per budget type with TTL aligned to
    the daily window. All operations are atomic via Lua script.
    """

    _SCRIPT_CHECK_AND_INCREMENT = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local window_seconds = tonumber(ARGV[2])
    local increment = tonumber(ARGV[3])
    local now = tonumber(ARGV[4])

    local current = tonumber(redis.call('GET', key) or '0')
    local ttl = redis.call('TTL', key)

    -- If key doesn't exist or expired, set TTL to window_seconds
    if ttl < 0 then
        ttl = window_seconds
    end

    local new_total = current + increment
    if new_total > limit then
        return {0, current, ttl}
    end

    redis.call('INCRBY', key, increment)
    redis.call('EXPIRE', key, ttl)
    return {1, new_total, ttl}
    """

    _SCRIPT_GET = """
    local key = KEYS[1]
    local current = tonumber(redis.call('GET', key) or '0')
    local ttl = redis.call('TTL', key)
    if ttl < 0 then
        ttl = 0
    end
    return {current, ttl}
    """

    _SCRIPT_RECONCILE = """
    local key = KEYS[1]
    local actual_used = tonumber(ARGV[1])
    local ttl = redis.call('TTL', key)
    if ttl < 0 then
        return {0}
    end
    redis.call('SET', key, actual_used)
    redis.call('EXPIRE', key, ttl)
    return {1, actual_used}
    """

    def __init__(self, client: object, budget_type: str, daily_limit: int, window_seconds: int = 86400):
        self._client = client
        self._budget_type = budget_type
        self._daily_limit = daily_limit
        self._window_seconds = window_seconds

    def _key(self, tenant_id: str) -> str:
        return make_redis_key(f"budget:{self._budget_type}:{tenant_id}")

    def check_and_reserve(self, tenant_id: str, estimated_cost: int) -> tuple[bool, int, int]:
        """Atomically check budget and reserve estimated cost.

        Returns (allowed, current_usage, ttl_seconds).
        If allowed is False, the reservation was not made.
        """
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT_CHECK_AND_INCREMENT,
                1,
                self._key(tenant_id),
                self._daily_limit,
                self._window_seconds,
                estimated_cost,
                time.time(),
            )
            allowed = int(result[0]) == 1
            current = int(result[1])
            ttl = int(result[2])
            return allowed, current, ttl
        except Exception:
            # On Redis failure, allow the request (fail open for budget)
            # but log the failure
            import logging
            logging.getLogger("knowledgeforge.budget").warning(
                "Redis budget counter unavailable for %s/%s; allowing request",
                self._budget_type,
                tenant_id,
                exc_info=True,
            )
            return True, 0, 0

    def get_usage(self, tenant_id: str) -> tuple[int, int]:
        """Get current usage and TTL for a tenant."""
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT_GET,
                1,
                self._key(tenant_id),
            )
            return int(result[0]), int(result[1])
        except Exception:
            return 0, 0

    def reconcile(self, tenant_id: str, actual_cost: int) -> bool:
        """Reconcile the counter with actual usage after a call completes.

        Replaces the estimated reservation with the actual cost.
        Returns True if reconciliation succeeded, False if Redis unavailable.
        """
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT_RECONCILE,
                1,
                self._key(tenant_id),
                actual_cost,
            )
            return int(result[0]) == 1
        except Exception:
            import logging
            logging.getLogger("knowledgeforge.budget").warning(
                "Redis budget reconcile failed for %s/%s",
                self._budget_type,
                tenant_id,
                exc_info=True,
            )
            return False


def _get_redis_client() -> Optional[object]:
    """Build Redis client for budget counters."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except ImportError:
        return None


# Global budget counter instances (lazy-initialized)
_token_budget: Optional[RedisBudgetCounter] = None
_extraction_budget: Optional[RedisBudgetCounter] = None


def get_token_budget() -> Optional[RedisBudgetCounter]:
    """Get or create the token budget counter for /ask calls."""
    global _token_budget
    if _token_budget is None:
        client = _get_redis_client()
        if client is None:
            return None
        settings = get_settings()
        # Default to 1M tokens/day if not configured
        daily_limit = getattr(settings, 'daily_token_budget', 1_000_000)
        _token_budget = RedisBudgetCounter(client, "tokens", daily_limit)
    return _token_budget


def get_extraction_budget() -> Optional[RedisBudgetCounter]:
    """Get or create the extraction budget counter."""
    global _extraction_budget
    if _extraction_budget is None:
        client = _get_redis_client()
        if client is None:
            return None
        settings = get_settings()
        # Default to 1000 extractions/day if not configured
        daily_limit = getattr(settings, 'daily_extraction_budget', 1000)
        _extraction_budget = RedisBudgetCounter(client, "extractions", daily_limit)
    return _extraction_budget


def estimate_token_cost(question: str, max_context_chars: int = 10000) -> int:
    """Rough heuristic: ~4 chars per token for input, plus estimated output.

    This is a pre-flight estimate; actual usage is reconciled after the call.
    """
    # Input tokens: question + context (roughly 10k chars max)
    input_chars = min(len(question), max_context_chars) + max_context_chars
    input_tokens = input_chars // 4
    # Output tokens: estimate ~500 tokens for answer
    output_tokens = 500
    return input_tokens + output_tokens