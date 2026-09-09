from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, create_model


@dataclass(frozen=True)
class TenantSchemaRow:
    id: UUID
    tenant_id: UUID
    schema_name: str
    schema_version: int
    description: str
    json_schema: dict[str, Any]
    field_descriptions: dict[str, str]
    active: bool
    created_at: datetime


_PYTHON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "number": float,
    "float": float,
    "boolean": bool,
    "bool": bool,
}


def compile_json_schema_to_pydantic(
    schema_name: str, json_schema: dict[str, Any]
) -> type[BaseModel]:
    """Dynamically compile a JSON Schema dictionary into a validated Pydantic model at runtime."""
    properties = json_schema.get("properties", {})
    required_fields = set(json_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for field_name, field_spec in properties.items():
        field_type_str = field_spec.get("type", "string").lower()
        py_type = _PYTHON_TYPE_MAP.get(field_type_str, str)

        if field_name in required_fields:
            field_definitions[field_name] = (py_type, ...)
        else:
            field_definitions[field_name] = (py_type | None, None)

    model_class_name = "".join(part.capitalize() for part in re.split(r"[-_]", schema_name))
    return create_model(model_class_name, **field_definitions)


def infer_schema_from_sample(
    sample_text: str, default_name: str = "custom_document"
) -> dict[str, Any]:
    """Infers an extraction schema structure from sample document text."""
    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []

    # Detect dates
    if re.search(
        r"\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}\b",
        sample_text,
    ):
        properties["document_date"] = {
            "type": "string",
            "description": "Document or execution date",
        }
        required.append("document_date")

    # Detect currency / financial amounts
    if re.search(r"[\$€£]\s*\d+(?:,\d{3})*(?:\.\d{2})?|\bUSD\b|\bEUR\b", sample_text):
        properties["total_amount"] = {
            "type": "number",
            "description": "Primary monetary or contract value",
        }
        properties["currency"] = {"type": "string", "description": "Currency code (e.g. USD, EUR)"}
        required.append("total_amount")

    # Detect parties/organizations
    if re.search(
        r"\b(Inc\.|LLC|Corp\.|Corporation|Ltd\.|Company|Party)\b", sample_text, re.IGNORECASE
    ):
        properties["party_a"] = {
            "type": "string",
            "description": "First contracting party or issuer",
        }
        properties["party_b"] = {"type": "string", "description": "Counterparty or recipient"}
        required.append("party_a")

    # Fallback generic properties if text is sparse
    if not properties:
        properties["title"] = {"type": "string", "description": "Title or headline of document"}
        properties["summary"] = {"type": "string", "description": "Brief factual summary"}
        properties["effective_date"] = {"type": "string", "description": "Effective date"}
        required.append("title")

    return {
        "title": default_name,
        "type": "object",
        "properties": properties,
        "required": required,
    }


def create_tenant_schema(
    connection: Connection,
    *,
    tenant_id: UUID,
    schema_name: str,
    json_schema: dict[str, Any],
    description: str = "",
    field_descriptions: dict[str, str] | None = None,
    version: int = 1,
) -> TenantSchemaRow:
    field_desc = field_descriptions or {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tenant_extraction_schemas (
                tenant_id, schema_name, schema_version, description, json_schema, field_descriptions
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, schema_name, schema_version)
            DO UPDATE SET
                description = EXCLUDED.description,
                json_schema = EXCLUDED.json_schema,
                field_descriptions = EXCLUDED.field_descriptions,
                active = true,
                updated_at = now()
            RETURNING id, tenant_id, schema_name, schema_version, description,
                      json_schema, field_descriptions, active, created_at
            """,
            (
                tenant_id,
                schema_name,
                version,
                description,
                Jsonb(json_schema),
                Jsonb(field_desc),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Failed to insert tenant extraction schema")
        return TenantSchemaRow(
            id=UUID(str(row[0])),
            tenant_id=UUID(str(row[1])),
            schema_name=str(row[2]),
            schema_version=int(row[3]),
            description=str(row[4]),
            json_schema=row[5] if isinstance(row[5], dict) else json.loads(row[5]),
            field_descriptions=row[6] if isinstance(row[6], dict) else json.loads(row[6]),
            active=bool(row[7]),
            created_at=row[8],
        )


def get_tenant_schema(
    connection: Connection,
    tenant_id: UUID,
    schema_name: str,
    version: int | None = None,
) -> TenantSchemaRow | None:
    with connection.cursor() as cursor:
        if version is not None:
            cursor.execute(
                """
                SELECT id, tenant_id, schema_name, schema_version, description,
                       json_schema, field_descriptions, active, created_at
                FROM tenant_extraction_schemas
                WHERE tenant_id = %s AND schema_name = %s AND schema_version = %s AND active = true
                """,
                (tenant_id, schema_name, version),
            )
        else:
            cursor.execute(
                """
                SELECT id, tenant_id, schema_name, schema_version, description,
                       json_schema, field_descriptions, active, created_at
                FROM tenant_extraction_schemas
                WHERE tenant_id = %s AND schema_name = %s AND active = true
                ORDER BY schema_version DESC
                LIMIT 1
                """,
                (tenant_id, schema_name),
            )
        row = cursor.fetchone()
        if row is None:
            return None
        return TenantSchemaRow(
            id=UUID(str(row[0])),
            tenant_id=UUID(str(row[1])),
            schema_name=str(row[2]),
            schema_version=int(row[3]),
            description=str(row[4]),
            json_schema=row[5] if isinstance(row[5], dict) else json.loads(row[5]),
            field_descriptions=row[6] if isinstance(row[6], dict) else json.loads(row[6]),
            active=bool(row[7]),
            created_at=row[8],
        )


def list_tenant_schemas(connection: Connection, tenant_id: UUID) -> list[TenantSchemaRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, schema_name, schema_version, description,
                   json_schema, field_descriptions, active, created_at
            FROM tenant_extraction_schemas
            WHERE tenant_id = %s AND active = true
            ORDER BY schema_name, schema_version DESC
            """,
            (tenant_id,),
        )
        rows = cursor.fetchall()
        return [
            TenantSchemaRow(
                id=UUID(str(row[0])),
                tenant_id=UUID(str(row[1])),
                schema_name=str(row[2]),
                schema_version=int(row[3]),
                description=str(row[4]),
                json_schema=row[5] if isinstance(row[5], dict) else json.loads(row[5]),
                field_descriptions=row[6] if isinstance(row[6], dict) else json.loads(row[6]),
                active=bool(row[7]),
                created_at=row[8],
            )
            for row in rows
        ]
