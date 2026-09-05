"""Chaos drills against the full local emulator stack (F6.3).

Drills (select with CHAOS_DRILLS, comma-separated; default: all):

- ``redis-loss`` — the API's REDIS_URL points at an unreachable host (via the
  docker-compose.chaos.yml override). Auth and document rate limiting must
  degrade to per-process buckets instead of failing requests.
- ``redelivery-storm`` — a burst of malformed Pub/Sub messages. All must reach
  the dead-letter subscription (retry exhaustion, no crash loops), and valid
  traffic must still flow after the storm.
- ``extraction-storm`` — a burst of malformed ``document.ready`` messages. All
  must reach the extraction dead-letter subscription, and a valid extraction
  must still complete afterward (Phase 2.5).

Run:
    docker compose -f docker-compose.full.yml -f docker-compose.chaos.yml up -d --build
    docker compose -f docker-compose.full.yml -f docker-compose.chaos.yml run --rm chaos
"""

import base64
import json
import os
import time
import urllib.request
import uuid

BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
PUBSUB_BASE_URL = f"http://{os.getenv('PUBSUB_EMULATOR_HOST', 'pubsub-emulator:8085')}"
DLQ_SUBSCRIPTION = "knowledgeforge-ingestion-dead-letter-worker"
EXTRACTION_DLQ_SUBSCRIPTION = "knowledgeforge-extraction-dead-letter-worker"
INGESTION_TOPIC = "knowledgeforge-ingestion"
EXTRACTION_TOPIC = "knowledgeforge-extraction"


def request(path: str, method: str = "GET", body: bytes | None = None, headers=None) -> dict:
    prepared = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers=headers or {}, method=method
    )
    with urllib.request.urlopen(prepared, timeout=30) as response:
        return json.loads(response.read())


def wait_for_api() -> None:
    for _ in range(60):
        try:
            if request("/health")["status"] == "ok":
                return
        except OSError:
            time.sleep(1)
    raise SystemExit("API did not become healthy")


def register_and_login() -> str:
    email = f"chaos-{uuid.uuid4()}@example.test"
    registration = json.dumps(
        {"email": email, "password": "chaos-drill-password", "tenant_name": "Chaos drill"}
    ).encode()
    return request("/auth/register", "POST", registration, {"Content-Type": "application/json"})[
        "access_token"
    ]


def upload_markdown(token: str) -> str:
    boundary = f"----KnowledgeForge{uuid.uuid4().hex}"
    content = f"# Chaos document {uuid.uuid4()}\n\nMust reach ready despite the drill.".encode()
    multipart = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="chaos-{uuid.uuid4()}.md"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return request(
        "/documents",
        "POST",
        multipart,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )["document_id"]


def poll_until_ready(token: str, document_id: str, timeout_seconds: int = 90) -> None:
    for _ in range(timeout_seconds):
        status = request(
            f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"}
        )["status"]
        if status == "ready":
            return
        if status == "failed":
            raise SystemExit(f"document {document_id} failed during the drill")
        time.sleep(1)
    raise SystemExit(f"document {document_id} did not become ready in time")


def emulator_json(path: str, method: str = "GET", body: bytes | None = None) -> dict:
    prepared = urllib.request.Request(
        f"{PUBSUB_BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(prepared, timeout=30) as response:
        return json.loads(response.read())


def ask(token: str) -> None:
    question = json.dumps(
        {"question": "What must this chaos document do despite the drill?"}
    ).encode()
    answer = request(
        "/ask",
        "POST",
        question,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if not answer["answer"] or not answer["citations"]:
        raise SystemExit(f"ask returned no cited answer during the drill: {answer}")


def redis_loss_drill() -> None:
    """Register, upload, and ask while Redis is unreachable — all must still work."""
    token = register_and_login()
    document_id = upload_markdown(token)
    poll_until_ready(token, document_id)
    ask(token)
    print("redis-loss: auth + document + ask rate limiting degraded gracefully")


def redelivery_storm_drill(burst: int = 10) -> None:
    """Malformed burst → all dead-lettered; valid traffic flows afterwards."""
    publish_path = f"/v1/projects/local-project/topics/{INGESTION_TOPIC}:publish"
    for _ in range(burst):
        body = json.dumps(
            {"messages": [{"data": base64.b64encode(b"not-json").decode("ascii")}]}
        ).encode()
        emulator_json(publish_path, "POST", body)

    pull_path = f"/v1/projects/local-project/subscriptions/{DLQ_SUBSCRIPTION}:pull"
    dead_lettered = 0
    for _ in range(90):
        pulled = emulator_json(pull_path, "POST", b'{"maxMessages":10}')
        messages = pulled.get("receivedMessages", [])
        if messages:
            dead_lettered += len(messages)
            emulator_json(
                f"/v1/projects/local-project/subscriptions/{DLQ_SUBSCRIPTION}:acknowledge",
                "POST",
                json.dumps({"ackIds": [m["ackId"] for m in messages]}).encode(),
            )
        if dead_lettered >= burst:
            break
        time.sleep(1)
    if dead_lettered < burst:
        raise SystemExit(
            f"redelivery storm: only {dead_lettered}/{burst} messages reached the dead letter"
        )

    token = register_and_login()
    document_id = upload_markdown(token)
    poll_until_ready(token, document_id)
    print(f"redelivery-storm: {burst} messages dead-lettered; valid traffic unaffected")


def extraction_storm_drill(burst: int = 5) -> None:
    """Malformed document.ready burst → extraction DLQ; a valid extraction still completes."""
    publish_path = f"/v1/projects/local-project/topics/{EXTRACTION_TOPIC}:publish"
    for _ in range(burst):
        body = json.dumps(
            {"messages": [{"data": base64.b64encode(b"not-json").decode("ascii")}]}
        ).encode()
        emulator_json(publish_path, "POST", body)

    pull_path = f"/v1/projects/local-project/subscriptions/{EXTRACTION_DLQ_SUBSCRIPTION}:pull"
    dead_lettered = 0
    for _ in range(90):
        pulled = emulator_json(pull_path, "POST", b'{"maxMessages":10}')
        messages = pulled.get("receivedMessages", [])
        if messages:
            dead_lettered += len(messages)
            emulator_json(
                f"/v1/projects/local-project/subscriptions/{EXTRACTION_DLQ_SUBSCRIPTION}:acknowledge",
                "POST",
                json.dumps({"ackIds": [m["ackId"] for m in messages]}).encode(),
            )
        if dead_lettered >= burst:
            break
        time.sleep(1)
    if dead_lettered < burst:
        raise SystemExit(
            f"extraction storm: only {dead_lettered}/{burst} messages reached the dead letter"
        )

    # A valid invoice still extracts end-to-end after the storm.
    token = register_and_login()
    document_id = upload_invoice(token)
    poll_until_ready(token, document_id)
    for _ in range(60):
        try:
            request(
                f"/documents/{document_id}/extraction",
                headers={"Authorization": f"Bearer {token}"},
            )
            print(
                "extraction-storm: burst dead-lettered; a valid extraction still completed"
            )
            return
        except SystemExit:
            raise
        except Exception:
            time.sleep(1)
    raise SystemExit("extraction storm: valid extraction never appeared")


def upload_invoice(token: str) -> str:
    """Upload an invoice-shaped markdown doc (the local classifier's fixture)."""
    boundary = f"----KnowledgeForgeChaos{uuid.uuid4().hex}"
    content = (
        b"# ACME invoice\n\nInvoice number: INV-CHAOS\n\n"
        b"Amount due: 42.00 USD\n\nPayment terms: net 30\n"
    )
    multipart = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="invoice-chaos.md"\r\n'
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
    return uploaded["document_id"]


DRILLS = {
    "redis-loss": redis_loss_drill,
    "redelivery-storm": redelivery_storm_drill,
    "extraction-storm": extraction_storm_drill,
}


def main() -> None:
    wait_for_api()
    selected = [
        name.strip()
        for name in os.getenv("CHAOS_DRILLS", ",".join(DRILLS)).split(",")
        if name.strip()
    ]
    for name in selected:
        drill = DRILLS.get(name)
        if drill is None:
            raise SystemExit(f"unknown drill: {name}")
        drill()
    print(f"chaos drills passed: {', '.join(selected)}")


if __name__ == "__main__":
    main()
