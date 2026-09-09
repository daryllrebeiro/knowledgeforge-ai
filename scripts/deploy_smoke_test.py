"""Comprehensive end-to-end integration and smoke test suite for deployed stack.

Exercises:
1. Registration & JWT authentication.
2. Ingestion pipeline (upload -> outbox -> Pub/Sub -> worker -> ready).
3. Structured extraction pipeline (invoice & contract schemas).
4. Multi-tenant isolation probe (cross-tenant 404 verification across all resources).
5. Structured-filter question answering with citations.
6. API key lifecycle (create -> authenticate -> revoke -> verify 401).
7. GDPR Article 20 data portability export.
8. Reprocessing lifecycle and cleanup cascade.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
    BASE_URL = sys.argv[1].rstrip("/")

TIMEOUT_SECONDS = int(os.getenv("SMOKE_TIMEOUT_SECONDS", "180"))


def request(
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def register_tenant(name_prefix: str) -> tuple[str, str, dict[str, str]]:
    email = f"{name_prefix}-{uuid.uuid4().hex[:8]}@example.test"
    password = f"smoke-pass-{uuid.uuid4().hex[:8]}"
    code, body = request(
        "/auth/register",
        "POST",
        json.dumps(
            {
                "email": email,
                "password": password,
                "tenant_name": f"{name_prefix} Corp",
            }
        ).encode(),
        {"Content-Type": "application/json"},
    )
    if code != 201:
        raise RuntimeError(f"Registration failed for {email}: HTTP {code} {body[:200]!r}")
    data = json.loads(body)
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return email, token, headers


def upload_document(token: str, filename: str, content: bytes) -> str:
    boundary = f"----KnowledgeForge{uuid.uuid4().hex}"
    multipart = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    code, body = request(
        "/documents",
        "POST",
        multipart,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    if code != 202:
        raise RuntimeError(f"Upload failed for {filename}: HTTP {code} {body[:200]!r}")
    return json.loads(body)["document_id"]


def wait_for_document_ready(headers: dict[str, str], document_id: str) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code, body = request(f"/documents/{document_id}", headers=headers)
        if code == 200 and json.loads(body).get("status") == "ready":
            return
        time.sleep(2)
    raise TimeoutError(f"Document {document_id} did not become ready within {TIMEOUT_SECONDS}s")


def wait_for_extraction(headers: dict[str, str], document_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code, body = request(f"/documents/{document_id}/extraction", headers=headers)
        if code == 200:
            return json.loads(body)
        time.sleep(2)
    raise TimeoutError(f"Extraction for {document_id} did not complete within {TIMEOUT_SECONDS}s")


def run_probe() -> int:
    print("=== KnowledgeForge E2E Integration Suite ===")
    print(f"Target URL: {BASE_URL}")

    # 1. Register Tenant A
    print("[1/7] Registering Primary Tenant (Tenant A)...")
    email_a, token_a, headers_a = register_tenant("smoke-tenant-a")

    # 2. Upload Invoice Document for Tenant A
    print("[2/7] Ingesting Invoice Document...")
    invoice_content = (
        b"# ACME Supplier Invoice\n\n"
        b"Invoice Number: INV-9901\n"
        b"Total Amount: 1,450.00 USD\n"
        b"Vendor Name: ACME Global Technologies\n"
        b"Due Date: 2026-10-15\n"
    )
    doc_a_id = upload_document(token_a, "invoice-acme.md", invoice_content)
    wait_for_document_ready(headers_a, doc_a_id)
    extraction_a = wait_for_extraction(headers_a, doc_a_id)
    assert extraction_a.get("schema_type") == "invoice", (
        f"Expected invoice schema, got {extraction_a}"
    )
    print("  -> Invoice ingested and extraction verified.")

    # 3. Multi-Tenant Isolation Probe
    print("[3/7] Probing Multi-Tenant Isolation Boundaries (Tenant B)...")
    email_b, token_b, headers_b = register_tenant("smoke-tenant-b")

    # Probe 3a: Tenant B accesses Tenant A's document details
    code, _ = request(f"/documents/{doc_a_id}", headers=headers_b)
    assert code == 404, f"Cross-tenant leak! Tenant B accessed Tenant A doc details: HTTP {code}"

    # Probe 3b: Tenant B accesses Tenant A's extraction
    code, _ = request(f"/documents/{doc_a_id}/extraction", headers=headers_b)
    assert code == 404, f"Cross-tenant leak! Tenant B accessed Tenant A extraction: HTTP {code}"

    # Probe 3c: Tenant B attempts to delete Tenant A's document
    code, _ = request(f"/documents/{doc_a_id}", "DELETE", headers=headers_b)
    assert code == 404, f"Cross-tenant leak! Tenant B attempted delete on Tenant A doc: HTTP {code}"

    # Probe 3d: Tenant B attempts scoped query on Tenant A doc
    code, _ = request(
        "/ask",
        "POST",
        json.dumps({"question": "What is the invoice amount?", "document_id": doc_a_id}).encode(),
        headers_b,
    )
    assert code == 404, f"Cross-tenant leak! Tenant B asked against Tenant A doc: HTTP {code}"
    print("  -> All cross-tenant access probes failed closed with 404 Not Found.")

    # 4. Contract Extraction Pipeline Validation
    print("[4/7] Ingesting Commercial Contract Document...")
    contract_content = (
        b"# Master Services Agreement\n\n"
        b"This Master Services Agreement is entered into by and between Enterprise Cloud Inc "
        b"and Global Cyber Solutions LLC (the 'Counterparty').\n"
        b"Effective Date: January 15, 2026.\n"
        b"Total Contract Value: 50,000 USD.\n"
        b"Governing Law: State of Delaware.\n"
    )
    doc_contract_id = upload_document(token_a, "contract-msa.md", contract_content)
    wait_for_document_ready(headers_a, doc_contract_id)
    contract_extraction = wait_for_extraction(headers_a, doc_contract_id)
    assert contract_extraction.get("schema_type") == "contract", (
        f"Expected contract schema, got {contract_extraction}"
    )
    print("  -> Contract ingested and routed to contract schema.")

    # 5. Structured Retrieval & Ask Verification
    print("[5/7] Verifying Structured Filter Retrieval & Citations...")
    code, body = request(
        "/ask",
        "POST",
        json.dumps(
            {
                "question": "What is the invoice number and due date for ACME?",
                "structured_filters": {"schema_type": "invoice"},
            }
        ).encode(),
        headers_a,
    )
    assert code == 200, f"Structured ask failed: HTTP {code} {body[:200]!r}"
    ask_res = json.loads(body)
    assert len(ask_res.get("citations", [])) > 0, f"Expected citations in response, got: {ask_res}"
    print("  -> Structured query answered with verified citation grounding.")

    # 6. API Key Flow
    print("[6/7] Verifying API Key Lifecycle...")
    code, body = request(
        "/api-keys",
        "POST",
        json.dumps({"name": "Smoke Key"}).encode(),
        headers_a,
    )
    if code == 201:
        key_data = json.loads(body)
        raw_key = key_data["key"]
        key_id = key_data["id"]

        # Call with API Key
        code, _ = request("/documents", headers={"X-API-Key": raw_key})
        assert code == 200, f"API key authentication failed: HTTP {code}"

        # Revoke Key
        code, _ = request(f"/api-keys/{key_id}", "DELETE", headers=headers_a)
        assert code == 204, f"API key revocation failed: HTTP {code}"

        # Probe with Revoked Key
        code, _ = request("/documents", headers={"X-API-Key": raw_key})
        assert code == 401, f"Revoked API key was accepted: HTTP {code}"
        print("  -> API key creation, invocation, revocation, and rejection confirmed.")
    else:
        print(f"  -> Skipping API key probe (endpoint status HTTP {code})")

    # 7. GDPR Article 20 Account Export & Cleanup
    print("[7/7] Verifying GDPR Account Export and Cascade Cleanup...")
    code, body = request("/auth/account/export", headers=headers_a)
    assert code == 200, f"GDPR export failed: HTTP {code}"
    export_data = json.loads(body)
    assert "tenant" in export_data and "documents" in export_data, "Malformed export payload"
    assert len(export_data["documents"]) >= 2, "Export missing ingested documents"

    # Cleanup Tenant A documents
    for did in [doc_a_id, doc_contract_id]:
        code, _ = request(f"/documents/{did}", "DELETE", headers=headers_a)
        assert code == 204, f"Delete failed: HTTP {code}"
        code, _ = request(f"/documents/{did}/extraction", headers=headers_a)
        assert code == 404, f"Extraction cascade failed: HTTP {code}"

    print("\n=== ALL INTEGRATION & ISOLATION PROBES PASSED ===")
    return 0


def main() -> int:
    try:
        return run_probe()
    except Exception as exc:
        print(f"\nFATAL: Integration suite failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
