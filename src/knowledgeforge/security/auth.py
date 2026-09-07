import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from knowledgeforge.config import get_settings
from knowledgeforge.db import get_connection
from knowledgeforge.security.api_keys import verify_api_key

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# Cookie names for token storage
ACCESS_TOKEN_COOKIE = "kf_access_token"
REFRESH_TOKEN_COOKIE = "kf_refresh_token"

Role = Literal["owner", "member"]
INVITATION_TOKEN_BYTES = 32
INVITATION_EXPIRY_DAYS = 7


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_context.verify(password, hashed_password)


def create_access_token(user_id: UUID, tenant_id: UUID, role: Role = "member", is_platform_admin: bool = False) -> str:
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": str(user_id), "tenant_id": str(tenant_id), "role": role, "is_platform_admin": is_platform_admin, "exp": expires},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def set_auth_cookies(response: Response, access_token: str, refresh_token: str, settings) -> None:
    """Set secure, HttpOnly, SameSite=lax cookies for access and refresh tokens."""
    secure = settings.environment != "development"
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/",
    )


def clear_auth_cookies(response: Response, settings) -> None:
    """Clear auth cookies on logout."""
    secure = settings.environment != "development"
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/", secure=secure, samesite="lax")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path="/", secure=secure, samesite="lax")


def get_user_memberships(user_id: UUID) -> list[tuple[UUID, Role]]:
    """Return list of (tenant_id, role) for a user."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tenant_id, role FROM tenant_memberships WHERE user_id = %s",
                (user_id,),
            )
            rows = cursor.fetchall()
    return [(UUID(str(row[0])), row[1]) for row in rows]


def get_user_role_for_tenant(user_id: UUID, tenant_id: UUID) -> Role | None:
    """Return the user's role for a specific tenant, or None if not a member."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role FROM tenant_memberships WHERE user_id = %s AND tenant_id = %s",
                (user_id, tenant_id),
            )
            row = cursor.fetchone()
    return row[0] if row else None


def get_user_platform_admin(user_id: UUID) -> bool:
    """Return whether the user is a platform admin."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_platform_admin FROM users WHERE id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
    return row[0] if row else False


def count_owners(tenant_id: UUID) -> int:
    """Return the number of owners for a tenant."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM tenant_memberships WHERE tenant_id = %s AND role = 'owner'",
                (tenant_id,),
            )
            row = cursor.fetchone()
    return row[0] if row else 0


def ensure_owner_remaining(tenant_id: UUID, exclude_user_id: UUID | None = None) -> None:
    """Raise HTTPException if the tenant would be left with zero owners.

    Call this before demoting or removing an owner. Pass exclude_user_id to
    exclude a specific user from the count (the user being demoted/removed).

    This function acquires a transaction-scoped advisory lock on the tenant_id
    to serialize concurrent membership changes for the same tenant, preventing
    TOCTOU races where two concurrent removals each see 2 owners and both proceed.
    The lock is automatically released at transaction end (commit or rollback).
    """
    # Acquire advisory lock to serialize membership changes for this tenant
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s::text))",
                (str(tenant_id),),
            )
    count = count_owners(tenant_id)
    if exclude_user_id is not None:
        # Check if the excluded user is currently an owner
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM tenant_memberships WHERE tenant_id = %s AND user_id = %s AND role = 'owner'",
                    (tenant_id, exclude_user_id),
                )
                if cursor.fetchone():
                    count -= 1
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove or demote the last owner of a tenant",
        )


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    access_token_cookie: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
) -> tuple[UUID, UUID, Role, bool]:
    # Try Authorization header first, then cookie
    token = credentials.credentials if credentials else access_token_cookie
    if token is not None:
        settings = get_settings()
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
            )
            user_id = UUID(str(payload["sub"]))
            tenant_id = UUID(str(payload["tenant_id"]))
            role = payload.get("role", "member")
            is_platform_admin = payload.get("is_platform_admin", False)
            request.state.tenant_id = tenant_id
            request.state.user_role = role
            request.state.is_platform_admin = is_platform_admin
            return user_id, tenant_id, role, is_platform_admin
        except (JWTError, KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            ) from exc
    api_key = request.headers.get("x-api-key")
    if api_key:
        with get_connection() as connection:
            identity = verify_api_key(connection, api_key)
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        request.state.tenant_id = identity[1]
        # API keys are tenant-scoped; fetch role from membership
        role = get_user_role_for_tenant(identity[0], identity[1]) or "member"
        is_platform_admin = get_user_platform_admin(identity[0])
        request.state.user_role = role
        request.state.is_platform_admin = is_platform_admin
        return identity[0], identity[1], role, is_platform_admin
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def require_tenant_role(allowed_roles: list[Role]):
    """Dependency factory that requires the current user to have one of the allowed roles for the tenant."""

    def checker(
        request: Request,
        user: tuple[UUID, UUID, Role, bool] = Depends(get_current_user),
    ) -> tuple[UUID, UUID, Role, bool]:
        user_id, tenant_id, role, _ = user
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(allowed_roles)}",
            )
        return user

    return checker


def require_platform_admin(
    user: tuple[UUID, UUID, Role, bool] = Depends(get_current_user),
) -> tuple[UUID, UUID, Role, bool]:
    """Require the current user to be a platform admin (separate from tenant roles)."""
    user_id, tenant_id, role, is_platform_admin = user
    if not is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires platform admin",
        )
    return user


# Convenience dependencies
require_owner = require_tenant_role(["owner"])
require_owner_or_member = require_tenant_role(["owner", "member"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invitation(
    connection,
    tenant_id: UUID,
    role: Role,
    email: str | None = None,
) -> tuple[UUID, str]:
    """Create an invitation and return (invitation_id, plaintext_token).

    The plaintext token is returned once and never stored; only its hash is kept.
    """
    token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
    token_hash = _hash_token(token)
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_EXPIRY_DAYS)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO invitations (tenant_id, role, token_hash, email, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, role, token_hash, email, expires_at),
        )
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("invitation insert did not return an ID")
    return UUID(str(row[0])), token


def accept_invitation(
    connection,
    token: str,
    user_id: UUID,
    user_email: str | None = None,
) -> tuple[UUID, UUID, Role]:
    """Accept an invitation by token, adding the user to the tenant.

    Returns (invitation_id, tenant_id, role).
    Raises HTTPException if token is invalid, expired, or already accepted.
    If the invitation has an email set, the caller's email must match (case-insensitive).
    """
    token_hash = _hash_token(token)
    # Atomic accept: only succeeds if invitation is valid, unaccepted, and not expired.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE invitations
            SET accepted_at = now(), accepted_by_user_id = %s
            WHERE token_hash = %s
              AND accepted_at IS NULL
              AND expires_at > now()
            RETURNING id, tenant_id, role, email
            """,
            (user_id, token_hash),
        )
        row = cursor.fetchone()
    if row is None:
        # Distinguish the failure reason for a helpful error code.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, expires_at, accepted_at, email FROM invitations WHERE token_hash = %s",
                (token_hash,),
            )
            probe = cursor.fetchone()
        if probe is None:
            raise HTTPException(status_code=404, detail="Invalid invitation token")
        _, expires_at, accepted_at, inv_email = probe
        if accepted_at is not None:
            raise HTTPException(status_code=409, detail="Invitation already accepted")
        if datetime.now(UTC) > expires_at:
            raise HTTPException(status_code=410, detail="Invitation expired")
        # Should not reach here — the UPDATE would have matched.
        raise HTTPException(status_code=409, detail="Invitation could not be accepted")
    invitation_id, tenant_id, role, inv_email = row
    # Enforce email match if the invitation was pre-assigned to an email.
    if inv_email is not None and user_email is not None:
        if inv_email.lower() != user_email.lower():
            raise HTTPException(status_code=403, detail="Invitation email does not match your account")
    elif inv_email is not None and user_email is None:
        # Invitation requires a specific email but caller has none on record.
        raise HTTPException(status_code=403, detail="Invitation requires email verification")
    # Add membership (upsert preserves existing role if user already a member).
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenant_memberships (tenant_id, user_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role
            """,
            (tenant_id, user_id, role),
        )
    return invitation_id, tenant_id, role
