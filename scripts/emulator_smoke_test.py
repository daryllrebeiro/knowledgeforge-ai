"""Exercise register -> upload -> async worker -> ready -> ask against the local stack."""

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
PUBSUB_BASE_URL = f"http://{os.getenv('PUBSUB_EMULATOR_HOST', 'pubsub-emulator:8085')}"
GCS_BASE_URL = os.getenv("GCS_BASE_URL", "http://fake-gcs:4443")
DLQ_SUBSCRIPTION = "knowledgeforge-ingestion-dead-letter-worker"


def upload_markdown(token: str, filename: str, content: bytes) -> dict:
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
    uploaded = request(
        "/documents",
        "POST",
        multipart,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return uploaded


def emulator_json(path: str, method: str = "GET", body: bytes | None = None) -> dict:
    request = urllib.request.Request(
        f"{PUBSUB_BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def gcs_object_names() -> list[str]:
    request = urllib.request.Request(f"{GCS_BASE_URL}/storage/v1/b/knowledgeforge/o")
    with urllib.request.urlopen(request, timeout=30) as response:
        return [str(item["name"]) for item in json.loads(response.read()).get("items", [])]


def assert_dead_letter_delivery() -> None:
    topic = "projects/local-project/topics/knowledgeforge-ingestion"
    body = json.dumps(
        {"messages": [{"data": base64.b64encode(b"not-json").decode("ascii")}]}
    ).encode()
    emulator_json(f"/v1/{topic}:publish", "POST", body)
    subscription = f"/v1/projects/local-project/subscriptions/{DLQ_SUBSCRIPTION}:pull"
    for _ in range(45):
        pulled = emulator_json(subscription, "POST", b'{"maxMessages":10}')
        messages = pulled.get("receivedMessages", [])
        if messages:
            ack_ids = [message["ackId"] for message in messages]
            emulator_json(
                f"/v1/projects/local-project/subscriptions/{DLQ_SUBSCRIPTION}:acknowledge",
                "POST",
                json.dumps({"ackIds": ack_ids}).encode(),
            )
            return
        time.sleep(1)
    raise SystemExit("malformed delivery did not reach the dead-letter subscription")


def request(
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers or {}, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def request_status(
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def ask_and_stream(token: str) -> None:
    """Full /ask round trip with LOCAL_GENERATION: retrieval, citations, SSE."""
    question = json.dumps(
        {"question": "What should the worker make this local async document?"}
    ).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    answer = request("/ask", "POST", question, headers)
    if not answer["answer"] or not answer["citations"]:
        raise SystemExit(f"ask returned no cited answer: {answer}")

    stream = urllib.request.Request(f"{BASE_URL}/ask/stream", data=question, headers=headers)
    with urllib.request.urlopen(stream, timeout=30) as response:
        events = response.read().decode()
    if "event: token" not in events or "event: done" not in events:
        raise SystemExit("streaming ask did not produce token and done events")


def extraction_lifecycle(token: str) -> str:
    """Phase 2.5 loop via the local stub: invoice -> extraction -> structured ask -> cascade."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    filename = f"invoice-{uuid.uuid4().hex}.md"
    content = (
        b"# ACME invoice\n\nInvoice number: INV-1001\n\n"
        b"Amount due: 250.00 USD\n\nPayment terms: net 30\n"
    )
    document_id = upload_markdown(token, filename, content)["document_id"]
    for _ in range(30):
        status = request(f"/documents/{document_id}", headers=headers)["status"]
        if status == "ready":
            break
        if status == "failed":
            raise SystemExit("extraction fixture document failed ingestion")
        time.sleep(1)
    else:
        raise SystemExit("extraction fixture did not become ready")

    # Outbox dispatcher -> extraction worker -> document_extractions row.
    for _ in range(45):
        code, body = request_status(
            f"/documents/{document_id}/extraction", headers=headers
        )
        if code == 200:
            extraction = json.loads(body)
            break
        time.sleep(1)
    else:
        raise SystemExit("extraction did not appear for the invoice fixture")
    if extraction["schema_type"] != "invoice" or not extraction["fields"].get("vendor_name"):
        raise SystemExit(f"unexpected extraction payload: {extraction}")

    # Reprocess: 202 + observable job lifecycle, worker replaces the row.
    reprocess = request(
        f"/documents/{document_id}/extraction/reprocess",
        "POST",
        b"{}",
        headers,
    )
    job_id = reprocess["job_id"]
    for _ in range(45):
        job = request(f"/extraction-jobs/{job_id}", headers=headers)
        if job["status"] in {"succeeded", "failed", "skipped"}:
            break
        time.sleep(1)
    else:
        raise SystemExit("reprocess job did not finish")
    if job["status"] != "succeeded":
        raise SystemExit(f"reprocess job ended {job['status']}: {job['detail']}")
    # An active-job conflict while none is running is a contract break; a
    # second reprocess right after completion must be accepted again.
    conflict, _ = request_status(
        f"/documents/{document_id}/extraction/reprocess", "POST", b"{}", headers
    )
    if conflict not in {202, 409}:
        raise SystemExit(f"second reprocess returned HTTP {conflict}")

    # Structured-filter ask: extraction rows scope retrieval and join the
    # prompt; citations must include the extracted-fields block.
    question = json.dumps(
        {
            "question": "What did we pay Acme?",
            "structured_filters": {
                "schema_type": "invoice",
                "vendor_name": "Acme Corporation",
            },
        }
    ).encode()
    answer = request("/ask", "POST", question, headers)
    if not answer["answer"] or not answer["citations"]:
        raise SystemExit(f"structured ask returned no cited answer: {answer}")
    if not any(citation.get("page") is None for citation in answer["citations"]):
        raise SystemExit(
            f"structured ask did not cite extracted fields: {answer['citations']}"
        )
    return document_id


def main() -> None:
    for _ in range(30):
        try:
            if request("/health")["status"] == "ok":
                break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit("API did not become healthy")

    email = f"emulator-{uuid.uuid4()}@example.test"
    password = "local-emulator-password"
    registration = json.dumps(
        {"email": email, "password": password, "tenant_name": "Local emulator"}
    ).encode()
    token = request("/auth/register", "POST", registration, {"Content-Type": "application/json"})[
        "access_token"
    ]

    content = b"# Local async document\n\nThe worker should make this document ready."
    document_id = upload_markdown(token, "local.md", content)["document_id"]
    duplicate = upload_markdown(token, "local.md", content)
    if duplicate["status"] != "duplicate" or duplicate["document_id"] != document_id:
        raise SystemExit("duplicate upload was not idempotent")

    for _ in range(30):
        status = request(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})[
            "status"
        ]
        if status == "ready":
            break
        if status == "failed":
            raise SystemExit("async smoke test failed: worker marked document failed")
        time.sleep(1)
    else:
        raise SystemExit("async smoke test timed out waiting for ready")

    # R6 end-to-end evidence: ask (plain + streaming) returns a cited answer
    # against the local stack, exercising retrieval and citation parsing.
    ask_and_stream(token)

    # Phase 2.5 end-to-end: the full extraction loop via the local stub.
    invoice_document_id = extraction_lifecycle(token)

    deleted, _ = request_status(
        f"/documents/{invoice_document_id}",
        "DELETE",
        headers={"Authorization": f"Bearer {token}"},
    )
    if deleted != 204:
        raise SystemExit(f"invoice deletion returned HTTP {deleted}")
    gone, _ = request_status(
        f"/documents/{invoice_document_id}/extraction",
        headers={"Authorization": f"Bearer {token}"},
    )
    if gone != 404:
        raise SystemExit(f"extraction row did not cascade with document deletion: HTTP {gone}")

    deleted, _ = request_status(
        f"/documents/{document_id}",
        "DELETE",
        headers={"Authorization": f"Bearer {token}"},
    )
    if deleted != 204:
        raise SystemExit(f"document deletion returned HTTP {deleted}")
    missing, _ = request_status(
        f"/documents/{document_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if missing != 404:
        raise SystemExit(f"deleted document returned HTTP {missing}")

    assert_dead_letter_delivery()
    account_filename = f"account-delete-{uuid.uuid4().hex}.md"
    account_document = upload_markdown(
        token,
        account_filename,
        b"# Account deletion\n\nThis object must be removed with its tenant.",
    )["document_id"]
    for _ in range(30):
        status = request(
            f"/documents/{account_document}",
            headers={"Authorization": f"Bearer {token}"},
        )["status"]
        if status == "ready":
            break
        time.sleep(1)
    else:
        raise SystemExit("account deletion fixture did not become ready")
    if not any(name.endswith(f"/{account_filename}") for name in gcs_object_names()):
        raise SystemExit("account deletion fixture was not stored in fake GCS")
    deleted, _ = request_status(
        "/auth/account", "DELETE", headers={"Authorization": f"Bearer {token}"}
    )
    if deleted != 204:
        raise SystemExit(f"account deletion returned HTTP {deleted}")
    unauthorized, _ = request_status(
        f"/documents/{account_document}", headers={"Authorization": f"Bearer {token}"}
    )
    if unauthorized != 404:
        raise SystemExit(f"deleted account token returned HTTP {unauthorized}")
    if any(name.endswith(f"/{account_filename}") for name in gcs_object_names()):
        raise SystemExit("account deletion left a raw fake-GCS object")
    print(f"full emulator lifecycle test passed: {document_id}")


if __name__ == "__main__":
    main()
