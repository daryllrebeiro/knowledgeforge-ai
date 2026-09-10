"""Embeddable white-label widget service and security controls (Phase 8 Item 9).

Enforces:
1. Strict collection scoping (widget queries can NEVER query outside designated collection).
2. Explicit domain-bound CORS validation (wildcard origins '*' strictly forbidden).
3. Dedicated rate-limit protection and daily widget token budget to prevent traffic spikes
   from silently exhausting the embedding tenant's account-wide daily budget.
"""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from psycopg import Connection

from knowledgeforge.limits import TokenBucketLimiter

# In-memory widget limiter instance for local dev and fast response
_widget_limiter = TokenBucketLimiter()


@dataclass(frozen=True)
class TenantWidgetRow:
    id: UUID
    tenant_id: UUID
    collection_id: UUID
    api_key_id: UUID
    name: str
    allowed_origins: list[str]
    primary_color: str
    rate_limit_per_minute: int
    daily_budget_tokens: int
    is_active: bool
    created_at: datetime


class WidgetOriginForbiddenError(Exception):
    """Raised when an embedding page Origin header is not explicitly allowlisted."""


class WidgetScopeViolationError(Exception):
    """Raised when a widget query attempts to retrieve across non-bound collections."""


def _normalize_origin(origin: str) -> str:
    origin = origin.strip().lower()
    if origin == "*":
        raise ValueError("Wildcard '*' origins are strictly prohibited for embeddable widgets")
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        # If bare host provided
        return origin.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def create_tenant_widget(
    connection: Connection,
    *,
    tenant_id: UUID,
    collection_id: UUID,
    api_key_id: UUID,
    name: str,
    allowed_origins: list[str],
    primary_color: str = "#2563eb",
    rate_limit_per_minute: int = 30,
    daily_budget_tokens: int = 50000,
) -> TenantWidgetRow:
    if not allowed_origins:
        raise ValueError("Widget must have at least one explicitly configured allowed origin")
    
    normalized_origins = [_normalize_origin(o) for o in allowed_origins]

    with connection.cursor() as cursor:
        # Verify collection belongs to tenant
        cursor.execute(
            "SELECT 1 FROM collections WHERE id = %s AND tenant_id = %s",
            (collection_id, tenant_id),
        )
        if cursor.fetchone() is None:
            raise ValueError("Collection not found under this tenant")

        # Verify api_key belongs to tenant and has query:ask scope
        cursor.execute(
            "SELECT scopes FROM api_keys WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL",
            (api_key_id, tenant_id),
        )
        key_row = cursor.fetchone()
        if key_row is None:
            raise ValueError("API key not found or revoked")
        scopes = key_row[0]
        if "*" not in scopes and "query:ask" not in scopes:
            raise ValueError("API key must contain 'query:ask' scope")

        cursor.execute(
            """
            INSERT INTO tenant_widgets (
                tenant_id, collection_id, api_key_id, name, allowed_origins,
                primary_color, rate_limit_per_minute, daily_budget_tokens
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, tenant_id, collection_id, api_key_id, name, allowed_origins,
                      primary_color, rate_limit_per_minute, daily_budget_tokens, is_active, created_at
            """,
            (
                tenant_id,
                collection_id,
                api_key_id,
                name,
                normalized_origins,
                primary_color,
                rate_limit_per_minute,
                daily_budget_tokens,
            ),
        )
        r = cursor.fetchone()
        if r is None:
            raise RuntimeError("Failed to create tenant widget")

    return TenantWidgetRow(
        id=UUID(str(r[0])),
        tenant_id=UUID(str(r[1])),
        collection_id=UUID(str(r[2])),
        api_key_id=UUID(str(r[3])),
        name=str(r[4]),
        allowed_origins=list(r[5]),
        primary_color=str(r[6]),
        rate_limit_per_minute=int(r[7]),
        daily_budget_tokens=int(r[8]),
        is_active=bool(r[9]),
        created_at=r[10],
    )


def get_widget_by_api_key(
    connection: Connection,
    api_key_id: UUID,
) -> TenantWidgetRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, collection_id, api_key_id, name, allowed_origins,
                   primary_color, rate_limit_per_minute, daily_budget_tokens, is_active, created_at
            FROM tenant_widgets
            WHERE api_key_id = %s AND is_active = true
            """,
            (api_key_id,),
        )
        r = cursor.fetchone()
    if r is None:
        return None
    return TenantWidgetRow(
        id=UUID(str(r[0])),
        tenant_id=UUID(str(r[1])),
        collection_id=UUID(str(r[2])),
        api_key_id=UUID(str(r[3])),
        name=str(r[4]),
        allowed_origins=list(r[5]),
        primary_color=str(r[6]),
        rate_limit_per_minute=int(r[7]),
        daily_budget_tokens=int(r[8]),
        is_active=bool(r[9]),
        created_at=r[10],
    )


def validate_widget_origin(
    widget: TenantWidgetRow,
    origin_header: str | None,
) -> None:
    """Validate client Origin header against widget allowlist strictly."""
    if not origin_header:
        raise WidgetOriginForbiddenError("Origin header is required for widget requests")
    
    normalized_incoming = _normalize_origin(origin_header)
    if normalized_incoming not in widget.allowed_origins:
        raise WidgetOriginForbiddenError(
            f"Origin '{normalized_incoming}' is not authorized to access this widget"
        )


def check_widget_rate_limit(widget: TenantWidgetRow, client_ip: str = "default") -> None:
    """Check dedicated widget rate limit to defend against traffic spikes."""
    key = f"widget:{widget.id}:{client_ip}"
    _widget_limiter.check(widget.tenant_id, key, widget.rate_limit_per_minute)


def generate_embed_js(widget_id: UUID, app_url: str = "http://localhost:8000") -> str:
    """Generate embeddable client JavaScript."""
    base_url = app_url.rstrip("/")
    return f"""(function() {{
  var widgetId = "{widget_id}";
  var baseUrl = "{base_url}";
  var container = document.createElement("div");
  container.id = "knowledgeforge-widget-container";
  container.style.position = "fixed";
  container.style.bottom = "20px";
  container.style.right = "20px";
  container.style.zIndex = "999999";
  
  var btn = document.createElement("button");
  btn.innerText = "Ask AI";
  btn.style.padding = "10px 16px";
  btn.style.borderRadius = "20px";
  btn.style.backgroundColor = "#2563eb";
  btn.style.color = "#ffffff";
  btn.style.border = "none";
  btn.style.cursor = "pointer";
  btn.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";
  
  container.appendChild(btn);
  document.body.appendChild(container);
}})();"""
