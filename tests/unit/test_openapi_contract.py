"""Unit tests for Item 13: OpenAPI Specification Contract Testing."""

import json
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from knowledgeforge.main import app

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"
OPENAPI_PATH = DOCS_DIR / "openapi.json"


def test_openapi_schema_file_exists():
    assert OPENAPI_PATH.exists(), f"Committed OpenAPI spec {OPENAPI_PATH} does not exist"


def test_openapi_spec_has_no_uncommitted_drift():
    """Verify that current FastAPI route definitions match the committed docs/openapi.json."""
    current_spec = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    serialized_current = json.dumps(current_spec, indent=2, sort_keys=True) + "\n"
    committed_content = OPENAPI_PATH.read_text(encoding="utf-8")

    assert serialized_current == committed_content, (
        "OpenAPI schema drift detected between live app routes and docs/openapi.json. "
        "Run 'python scripts/export_openapi.py' to update the committed contract."
    )


def test_v1_compatibility_routes_present():
    """Verify that core endpoints are available under the /v1 prefix."""
    current_spec = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    paths = current_spec["paths"]

    expected_v1_paths = [
        "/v1/auth/login",
        "/v1/auth/register",
        "/v1/auth/refresh",
        "/v1/auth/account/export",
        "/v1/documents",
        "/v1/ask",
        "/v1/extractions",
        "/v1/billing/create-checkout-session",
        "/v1/billing/subscription",
        "/v1/billing/create-portal-session",
    ]

    for expected_path in expected_v1_paths:
        assert expected_path in paths, f"Expected {expected_path} to be registered in OpenAPI spec"


def test_core_schemas_present_in_contract():
    """Verify that critical data contracts exist in the OpenAPI components schemas."""
    current_spec = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    schemas = current_spec["components"]["schemas"]

    expected_schemas = [
        "AskRequest",
        "AskResponse",
        "CitationResponse",
        "DocumentSummary",
        "DocumentUploadResponse",
        "ExtractionResponse",
        "CheckoutSessionRequest",
        "CheckoutSessionResponse",
        "SubscriptionResponse",
        "TokenResponse",
        "UsageResponse",
    ]

    for schema_name in expected_schemas:
        assert schema_name in schemas, f"Expected schema '{schema_name}' in OpenAPI components"
