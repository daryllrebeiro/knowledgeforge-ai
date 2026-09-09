"""Redis-backed budget counters for per-tenant cost control.

Provides atomic daily budget tracking for token usage (/ask) and extraction calls.
"""

import time

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
    local reservation_ttl = tonumber(ARGV[5])

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
    -- Use the shorter of reservation_ttl or window_seconds as TTL
    -- This ensures reservations self-expire quickly if not reconciled
    local effective_ttl = math.min(reservation_ttl, ttl)
    redis.call('EXPIRE', key, effective_ttl)
    return {1, new_total, effective_ttl}
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

    _SCRIPT_RELEASE = """
    local key = KEYS[1]
    local decrement = tonumber(ARGV[1])
    local reservation_ttl = tonumber(ARGV[2])
    local current = tonumber(redis.call('GET', key) or '0')
    local ttl = redis.call('TTL', key)
    if ttl < 0 then
        return {0}
    end
    local new_total = current - decrement
    if new_total < 0 then
        new_total = 0
    end
    redis.call('SET', key, new_total)
    redis.call('EXPIRE', key, reservation_ttl)
    return {1, new_total}
    """

    def __init__(
        self,
        client: object,
        budget_type: str,
        daily_limit: int,
        window_seconds: int = 86400,
        reservation_ttl: int = 300,
    ):
        self._client = client
        self._budget_type = budget_type
        self._daily_limit = daily_limit
        self._window_seconds = window_seconds
        self._reservation_ttl = reservation_ttl  # TTL for unreconciled reservations (default 5 min)

    def _key(self, tenant_id: str) -> str:
        return make_redis_key(f"budget:{self._budget_type}:{tenant_id}")

    def check_and_reserve(
        self, tenant_id: str, estimated_cost: int, limit: int | None = None
    ) -> tuple[bool, int, int]:
        """Atomically check budget and reserve estimated cost.

        Returns (allowed, current_usage, ttl_seconds).
        If allowed is False, the reservation was not made.
        Raises HTTPException if Redis is unavailable (fail closed for budget safety).
        """
        effective_limit = self._daily_limit if limit is None else limit
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT_CHECK_AND_INCREMENT,
                1,
                self._key(tenant_id),
                effective_limit,
                self._window_seconds,
                estimated_cost,
                time.time(),
                self._reservation_ttl,
            )
            allowed = int(result[0]) == 1
            current = int(result[1])
            ttl = int(result[2])
            return allowed, current, ttl
        except Exception:
            # On Redis failure, DENY the request (fail closed for budget safety)
            # Budget is a safety control; failing open would allow unlimited spend.
            import logging

            logging.getLogger("knowledgeforge.budget").error(
                "Redis budget counter unavailable for %s/%s; denying request",
                self._budget_type,
                tenant_id,
                exc_info=True,
            )
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Budget service unavailable; please retry",
            ) from None

    def release_reservation(self, tenant_id: str, estimated_cost: int) -> bool:
        """Release a reservation by decrementing the counter.

        Used when a call fails after reservation but before reconciliation.
        Returns True if release succeeded, False if Redis unavailable.
        """
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                self._SCRIPT_RELEASE,
                1,
                self._key(tenant_id),
                estimated_cost,
                self._reservation_ttl,
            )
            return int(result[0]) == 1
        except Exception:
            import logging

            logging.getLogger("knowledgeforge.budget").warning(
                "Redis budget release failed for %s/%s",
                self._budget_type,
                tenant_id,
                exc_info=True,
            )
            return False

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


def _get_redis_client() -> object | None:
    """Build Redis client for budget counters."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except ImportError:
        return None


# Global budget counter instances (lazy-initialized)
_token_budget: RedisBudgetCounter | None = None
_extraction_budget: RedisBudgetCounter | None = None
_platform_token_budget: RedisBudgetCounter | None = None


def get_token_budget() -> RedisBudgetCounter | None:
    """Get or create the token budget counter for /ask calls."""
    global _token_budget
    if _token_budget is None:
        client = _get_redis_client()
        if client is None:
            return None
        settings = get_settings()
        # Default to 1M tokens/day if not configured
        daily_limit = getattr(settings, "daily_token_budget", 1_000_000)
        _token_budget = RedisBudgetCounter(client, "tokens", daily_limit)
    return _token_budget


def get_platform_token_budget() -> RedisBudgetCounter | None:
    """Get or create the platform-wide token budget counter.

    This provides a hard ceiling on total platform token spend per day,
    independent of per-tenant budgets. When this limit is approached,
    all /ask calls are rejected regardless of individual tenant budgets.
    """
    global _platform_token_budget
    if _platform_token_budget is None:
        client = _get_redis_client()
        if client is None:
            return None
        settings = get_settings()
        # Default to 10M tokens/day platform-wide (10x per-tenant default)
        daily_limit = getattr(settings, "platform_daily_token_budget", 10_000_000)
        _platform_token_budget = RedisBudgetCounter(client, "platform_tokens", daily_limit)
    return _platform_token_budget


def get_extraction_budget() -> RedisBudgetCounter | None:
    """Get or create the extraction budget counter."""
    global _extraction_budget
    if _extraction_budget is None:
        client = _get_redis_client()
        if client is None:
            return None
        settings = get_settings()
        # Default to 1000 extractions/day if not configured
        daily_limit = getattr(settings, "daily_extraction_budget", 1000)
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


def estimate_research_token_cost(brief: str, max_iterations: int = 3) -> int:
    """Estimate token cost for autonomous multi-round research jobs.

    Scales proportionally with max_iterations to account for iterative sub-goal
    execution, document retrieval, and dossier synthesis.
    """
    iterations = max(1, max_iterations)
    base_cost = estimate_token_cost(brief, max_context_chars=10000)
    return iterations * base_cost


def invalidate_tenant_budget_cache(tenant_id: object, redis_client: object | None = None) -> None:
    """Invalidate cached tenant budget tier information in Redis."""
    client = redis_client or _get_redis_client()
    if client is not None:
        try:
            key = make_redis_key(f"cache:tenant_budget:{tenant_id}")
            client.delete(key)  # type: ignore[attr-defined]
        except Exception:
            pass


def get_tenant_budget_limits(
    tenant_id: object,
    connection: object | None = None,
    redis_client: object | None = None,
) -> tuple[int, int, str, bool]:
    """Resolve dynamic token budget, extraction budget, tier, and email verification status.

    Returns:
        (token_limit, extraction_limit, tier, is_verified)

    Checks Redis cache first (TTL 60s).
    Falls back to DB query.
    Falls back to config defaults on DB/Redis error.
    """
    settings = get_settings()
    client = redis_client or _get_redis_client()
    cache_key = make_redis_key(f"cache:tenant_budget:{tenant_id}")

    if client is not None:
        try:
            cached = client.get(cache_key)  # type: ignore[attr-defined]
            if cached:
                import json

                data = json.loads(cached)
                return (
                    int(data["token_limit"]),
                    int(data["extraction_limit"]),
                    str(data["tier"]),
                    bool(data["is_verified"]),
                )
        except Exception:
            pass

    # Query DB
    try:
        from knowledgeforge.billing.service import get_tenant_billing_info
        from knowledgeforge.db import get_connection

        if connection is not None:
            info = get_tenant_billing_info(connection, tenant_id)  # type: ignore[arg-type]
        else:
            with get_connection() as conn:
                info = get_tenant_billing_info(conn, tenant_id)  # type: ignore[arg-type]

        token_limit = int(info["daily_token_budget"])
        extraction_limit = int(info["daily_extraction_budget"])
        tier = str(info["tier"])
        is_verified = bool(info["is_email_verified"])

        # Cache in Redis with 60s TTL
        if client is not None:
            try:
                import json

                payload = json.dumps(
                    {
                        "token_limit": token_limit,
                        "extraction_limit": extraction_limit,
                        "tier": tier,
                        "is_verified": is_verified,
                    }
                )
                client.setex(cache_key, 60, payload)  # type: ignore[attr-defined]
            except Exception:
                pass

        return token_limit, extraction_limit, tier, is_verified
    except Exception:
        # Graceful fallback to config defaults
        return (
            settings.daily_token_budget,
            settings.daily_extraction_budget,
            "free",
            True,
        )
