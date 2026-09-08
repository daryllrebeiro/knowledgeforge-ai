import pytest
from fastapi import HTTPException

from knowledgeforge.security.budget import (
    BudgetExceeded,
    RedisBudgetCounter,
    estimate_token_cost,
    get_extraction_budget,
    get_platform_token_budget,
    get_token_budget,
)


class MockRedisBudget:
    """Mock Redis client simulating RedisBudgetCounter's Lua scripts and state."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple[object, ...]] = []

    def eval(self, script: str, numkeys: int, *args: object) -> list[int]:
        self.calls.append((script, numkeys, *args))
        key = str(args[0])

        if script == RedisBudgetCounter._SCRIPT_CHECK_AND_INCREMENT:
            limit = int(args[1])
            window_seconds = int(args[2])
            increment = int(args[3])
            # args[4] is now
            reservation_ttl = int(args[5])

            current = self.store.get(key, 0)
            ttl = self.ttls.get(key, -1)
            if ttl < 0:
                ttl = window_seconds

            new_total = current + increment
            if new_total > limit:
                return [0, current, ttl]

            self.store[key] = new_total
            effective_ttl = min(reservation_ttl, ttl)
            self.ttls[key] = effective_ttl
            return [1, new_total, effective_ttl]

        elif script == RedisBudgetCounter._SCRIPT_GET:
            current = self.store.get(key, 0)
            ttl = self.ttls.get(key, 0)
            return [current, max(0, ttl)]

        elif script == RedisBudgetCounter._SCRIPT_RECONCILE:
            actual_used = int(args[1])
            if key not in self.store:
                return [0]
            ttl = self.ttls.get(key, -1)
            if ttl < 0:
                return [0]
            self.store[key] = actual_used
            return [1, actual_used]

        elif script == RedisBudgetCounter._SCRIPT_RELEASE:
            decrement = int(args[1])
            reservation_ttl = int(args[2])
            if key not in self.store:
                return [0]
            current = self.store[key]
            new_total = max(0, current - decrement)
            self.store[key] = new_total
            self.ttls[key] = reservation_ttl
            return [1, new_total]

        raise NotImplementedError("Unknown Lua script in MockRedisBudget")


class FailingRedis:
    """Simulates Redis outage / network error."""

    def eval(self, *args: object) -> None:
        raise ConnectionError("Redis connection lost")


def test_budget_exceeded_exception() -> None:
    exc = BudgetExceeded("token", 1000, 1050)
    assert exc.budget_type == "token"
    assert exc.limit == 1000
    assert exc.used == 1050
    assert "token budget exceeded: 1050/1000" in str(exc)


def test_check_and_reserve_within_budget() -> None:
    client = MockRedisBudget()
    counter = RedisBudgetCounter(client, "token", daily_limit=1000, reservation_ttl=300)

    allowed, used, ttl = counter.check_and_reserve("tenant-1", 400)
    assert allowed is True
    assert used == 400
    assert ttl == 300


def test_check_and_reserve_rejects_over_limit() -> None:
    client = MockRedisBudget()
    counter = RedisBudgetCounter(client, "token", daily_limit=1000)

    allowed, used, _ = counter.check_and_reserve("tenant-1", 800)
    assert allowed is True
    assert used == 800

    # Next reserve exceeds 1000 limit
    allowed, current, _ = counter.check_and_reserve("tenant-1", 300)
    assert allowed is False
    assert current == 800  # not incremented


def test_release_reservation_restores_capacity() -> None:
    client = MockRedisBudget()
    counter = RedisBudgetCounter(client, "token", daily_limit=1000)

    allowed, used, _ = counter.check_and_reserve("tenant-1", 800)
    assert allowed is True
    assert used == 800

    # Over limit for 300 tokens
    allowed, _, _ = counter.check_and_reserve("tenant-1", 300)
    assert allowed is False

    # Release the 800 reservation
    success = counter.release_reservation("tenant-1", 800)
    assert success is True

    # Usage is now 0
    usage, _ = counter.get_usage("tenant-1")
    assert usage == 0

    # Now 300 tokens reserve succeeds
    allowed, used, _ = counter.check_and_reserve("tenant-1", 300)
    assert allowed is True
    assert used == 300


def test_reconcile_replaces_estimate_with_actual() -> None:
    client = MockRedisBudget()
    counter = RedisBudgetCounter(client, "token", daily_limit=1000)

    # Reserve estimated 500
    counter.check_and_reserve("tenant-1", 500)

    # Actual cost was only 220
    success = counter.reconcile("tenant-1", 220)
    assert success is True

    usage, _ = counter.get_usage("tenant-1")
    assert usage == 220


def test_budget_fails_closed_on_redis_error() -> None:
    counter = RedisBudgetCounter(FailingRedis(), "token", daily_limit=1000)

    # Must raise HTTPException 503 (fail closed: deny request)
    with pytest.raises(HTTPException) as exc_info:
        counter.check_and_reserve("tenant-1", 100)
    assert exc_info.value.status_code == 503
    assert "Budget service unavailable" in exc_info.value.detail


def test_release_reservation_handles_redis_error() -> None:
    counter = RedisBudgetCounter(FailingRedis(), "token", daily_limit=1000)
    # release_reservation should return False, not crash
    assert counter.release_reservation("tenant-1", 100) is False


def test_reconcile_handles_redis_error() -> None:
    counter = RedisBudgetCounter(FailingRedis(), "token", daily_limit=1000)
    # reconcile should return False, not crash
    assert counter.reconcile("tenant-1", 100) is False


def test_get_usage_handles_redis_error() -> None:
    counter = RedisBudgetCounter(FailingRedis(), "token", daily_limit=1000)
    assert counter.get_usage("tenant-1") == (0, 0)


def test_estimate_token_cost() -> None:
    cost = estimate_token_cost("What is the capital of France?", max_context_chars=1000)
    assert cost > 0
    assert isinstance(cost, int)


def test_get_budget_factories(monkeypatch) -> None:
    from knowledgeforge.security import budget

    fake_client = MockRedisBudget()
    monkeypatch.setattr(budget, "_get_redis_client", lambda: fake_client)
    monkeypatch.setattr(budget, "_token_budget", None)
    monkeypatch.setattr(budget, "_extraction_budget", None)
    monkeypatch.setattr(budget, "_platform_token_budget", None)

    tb = get_token_budget()
    assert tb is not None
    assert tb._budget_type == "tokens"

    eb = get_extraction_budget()
    assert eb is not None
    assert eb._budget_type == "extractions"

    pb = get_platform_token_budget()
    assert pb is not None
    assert pb._budget_type == "platform_tokens"
