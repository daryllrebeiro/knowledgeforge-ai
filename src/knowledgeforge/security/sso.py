"""Enterprise OIDC Single Sign-On (SSO) integration module."""

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from jose import JWTError, jwt

from knowledgeforge.config import get_settings
from knowledgeforge.security.auth import hash_password

logger = logging.getLogger("knowledgeforge.sso")

STATE_EXPIRY_SECONDS = 600  # 10 minutes


class SSOTierError(HTTPException):
    def __init__(self, detail: str = "OIDC SSO is an Enterprise-tier feature"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class SSOValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def validate_enterprise_tier(connection, tenant_id: UUID) -> None:
    """Verify that the tenant is on the Enterprise subscription tier."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT tier FROM tenants WHERE id = %s", (tenant_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tier = str(row[0]).lower()
        if tier != "enterprise":
            raise SSOTierError(f"OIDC SSO is restricted to Enterprise tenants; current tier is '{tier}'")


def create_sso_state(tenant_id: UUID) -> str:
    """Create a signed, time-limited CSRF state token for OIDC initiation."""
    settings = get_settings()
    payload = {
        "tenant_id": str(tenant_id),
        "nonce": secrets.token_hex(16),
        "exp": datetime.now(UTC) + timedelta(seconds=STATE_EXPIRY_SECONDS),
        "type": "sso_state",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_sso_state(state_token: str) -> UUID:
    """Validate CSRF state token and return the encoded tenant_id."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            state_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "sso_state":
            raise SSOValidationError("Invalid state token type")
        return UUID(payload["tenant_id"])
    except (JWTError, KeyError, ValueError) as exc:
        raise SSOValidationError(f"Invalid or expired SSO state token: {exc}") from exc


def get_sso_config(connection, tenant_id: UUID) -> dict[str, Any] | None:
    """Retrieve tenant SSO configuration with redacted client secret."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tenant_id, enabled, issuer_url, client_id, client_secret, created_at, updated_at
            FROM tenant_sso_configs
            WHERE tenant_id = %s;
            """,
            (tenant_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

    has_secret = bool(row[4])
    return {
        "tenant_id": str(row[0]),
        "enabled": row[1],
        "issuer_url": row[2],
        "client_id": row[3],
        "has_client_secret": has_secret,
        "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
        "updated_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
    }


def save_sso_config(
    connection,
    tenant_id: UUID,
    *,
    issuer_url: str,
    client_id: str,
    client_secret: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Upsert tenant SSO configuration after validating enterprise tier and issuer URL."""
    validate_enterprise_tier(connection, tenant_id)

    parsed = urlparse(issuer_url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise SSOValidationError(f"Invalid issuer_url '{issuer_url}'; must be a valid HTTP(S) URL")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenant_sso_configs (tenant_id, enabled, issuer_url, client_id, client_secret, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    issuer_url = EXCLUDED.issuer_url,
                    client_id = EXCLUDED.client_id,
                    client_secret = COALESCE(EXCLUDED.client_secret, tenant_sso_configs.client_secret),
                    updated_at = now()
            RETURNING tenant_id, enabled, issuer_url, client_id, created_at, updated_at;
            """,
            (tenant_id, enabled, issuer_url, client_id, client_secret),
        )
        row = cursor.fetchone()
        connection.commit()

    return {
        "tenant_id": str(row[0]),
        "enabled": row[1],
        "issuer_url": row[2],
        "client_id": row[3],
        "has_client_secret": bool(client_secret),
        "created_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        "updated_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
    }


def build_authorization_url(
    sso_config: dict[str, Any],
    redirect_uri: str,
    state: str,
) -> str:
    """Build the IdP authorization endpoint redirect URL."""
    issuer = sso_config["issuer_url"].rstrip("/")
    auth_endpoint = f"{issuer}/protocol/openid-connect/auth" if "keycloak" in issuer else f"{issuer}/authorize"
    params = {
        "response_type": "code",
        "client_id": sso_config["client_id"],
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    return f"{auth_endpoint}?{urlencode(params)}"


def process_sso_claims(
    connection,
    tenant_id: UUID,
    claims: dict[str, Any],
) -> tuple[UUID, UUID, str]:
    """Verify claims, perform Just-In-Time (JIT) provisioning or linking, and return user identity."""
    email = claims.get("email")
    if not email or "@" not in email:
        raise SSOValidationError("OIDC ID token is missing a valid 'email' claim")

    email = email.lower().strip()

    with connection.cursor() as cursor:
        # 1. Check if user exists by email
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user_row = cursor.fetchone()

        if user_row:
            user_id = user_row[0]
            # Ensure email is marked verified since IdP validated it
            cursor.execute("UPDATE users SET email_verified = true WHERE id = %s", (user_id,))
        else:
            # JIT Provisioning
            user_id = uuid4()
            random_pw_hash = hash_password(secrets.token_hex(32))
            cursor.execute(
                """
                INSERT INTO users (id, email, hashed_password, email_verified)
                VALUES (%s, %s, %s, true)
                """,
                (user_id, email, random_pw_hash),
            )

        # 2. Check tenant membership
        cursor.execute(
            "SELECT role FROM tenant_memberships WHERE tenant_id = %s AND user_id = %s",
            (tenant_id, user_id),
        )
        membership_row = cursor.fetchone()

        if membership_row:
            role = membership_row[0]
        else:
            # Add to tenant as member
            role = "member"
            cursor.execute(
                """
                INSERT INTO tenant_memberships (tenant_id, user_id, role)
                VALUES (%s, %s, %s)
                """,
                (tenant_id, user_id, role),
            )

        connection.commit()

    return user_id, tenant_id, role
