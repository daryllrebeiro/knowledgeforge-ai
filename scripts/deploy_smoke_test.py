"""Functional smoke test against a deployed KnowledgeForge stack.

Exercises the Phase 2.5 loop end-to-end with real services: register, upload
an invoice, wait for the extraction (outbox -> Pub/Sub -> extraction worker),
ask with structured_filters (cited extracted fields), reprocess (observable
job), and delete (cascades the extraction row). Stdlib only.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
TIMEOUT_SECONDS = int(os.getenv("SMOKE_TIMEOUT_SECONDS", "180"))


def request(
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main() -> int:
    email = f"deploy-smoke-{uuid.uuid4()}@example.test"
    code, body = request(
        "/auth/register",
        "POST",
        json.dumps(
            {"email": email, "password": "deploy-smoke-password", "tenant_name": "Deploy smoke"}
        ).encode(),
        {"Content-Type": "application/json"},
    )
    if code != 201:
        print(f"register failed: HTTP {code} {body[:200]!r}")
        return 1
    token = json.loads(body)["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    boundary = f"----KnowledgeForge{uuid.uuid4().hex}"
    content = (
        b"# ACME invoice\n\nInvoice number: INV-1001\n\n"
        b"Amount due: 250.00 USD\n\nPayment terms: net 30\n"
    )
    multipart = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="invoice-smoke.md"\r\n'
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
        print(f"upload failed: HTTP {code} {body[:200]!r}")
        return 1
    document_id = json.loads(body)["document_id"]

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code, body = request(f"/documents/{document_id}", headers=headers)
        if code == 200 and json.loads(body)["status"] == "ready":
            break
        time.sleep(2)
    else:
        print("document did not become ready")
        return 1

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code, body = request(f"/documents/{document_id}/extraction", headers=headers)
        if code == 200:
            break
        time.sleep(2)
    else:
        print("extraction did not appear (outbox/scheduler/extraction worker)")
        return 1
    extraction = json.loads(body)
    if extraction.get("schema_type") != "invoice":
        print(f"unexpected schema_type: {extraction}")
        return 1

    code, body = request(
        "/ask",
        "POST",
        json.dumps(
            {
                "question": "What is the total on the ACME invoice?",
                "structured_filters": {"schema_type": "invoice"},
            }
        ).encode(),
        headers,
    )
    if code != 200:
        print(f"structured ask failed: HTTP {code} {body[:200]!r}")
        return 1
    answer = json.loads(body)
    if not answer.get("citations"):
        print(f"structured ask returned no citations: {answer}")
        return 1

    code, body = request(
        f"/documents/{document_id}/extraction/reprocess", "POST", b"{}", headers
    )
    if code != 202:
        print(f"reprocess failed: HTTP {code} {body[:200]!r}")
        return 1
    job_id = json.loads(body)["job_id"]
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        code, body = request(f"/extraction-jobs/{job_id}", headers=headers)
        status = json.loads(body).get("status") if code == 200 else None
        if status in {"succeeded", "failed", "skipped"}:
            break
        time.sleep(2)
    else:
        print("reprocess job did not finish")
        return 1
    if status != "succeeded":
        print(f"reprocess job ended {status}")
        return 1

    code, _ = request(
        f"/documents/{document_id}", "DELETE", headers={"Authorization": f"Bearer {token}"}
    )
    if code != 204:
        print(f"delete failed: HTTP {code}")
        return 1
    code, _ = request(f"/documents/{document_id}/extraction", headers=headers)
    if code != 404:
        print(f"extraction row did not cascade with deletion: HTTP {code}")
        return 1
    print(f"deploy smoke test passed: {document_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
