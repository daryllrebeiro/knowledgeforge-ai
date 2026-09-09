"""Pydantic schemas for billing, tiers, and subscription management."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

TierType = Literal["free", "pro", "enterprise"]
SubscriptionStatus = Literal["active", "past_due", "canceled", "unpaid", "trialing"]


class SubscriptionResponse(BaseModel):
    """Current subscription and usage state for a tenant."""

    tenant_id: UUID
    tier: str
    subscription_status: str
    current_period_end: datetime | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    daily_token_budget: int
    daily_extraction_budget: int
    current_token_usage: int
    current_extraction_usage: int
    is_email_verified: bool
    in_grace_period: bool = False


class CheckoutSessionRequest(BaseModel):
    """Request to initiate a Stripe checkout session."""

    tier: Literal["pro", "enterprise"]
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(BaseModel):
    """Checkout session URL and identifier."""

    session_id: str
    url: str


class CustomerPortalRequest(BaseModel):
    """Request to create a Stripe Customer Portal session."""

    return_url: str


class CustomerPortalResponse(BaseModel):
    """Stripe Customer Portal URL."""

    url: str


class TenantBillingAdminView(BaseModel):
    """Admin inspection view for tenant billing and limits."""

    tenant_id: UUID
    tenant_name: str
    tier: str
    subscription_status: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    current_period_end: datetime | None = None
    token_usage: int
    extraction_usage: int
    token_limit: int
    extraction_limit: int
    is_email_verified: bool


class UpdateTenantTierRequest(BaseModel):
    """Admin request to change a tenant's subscription tier."""

    tier: TierType
    subscription_status: str = "active"
