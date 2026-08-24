"""Exercise register -> upload -> async worker -> ready against the full local stack."""

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
    subscription = (
        "/v1/projects/local-project/subscriptions/"
        "knowledgeforge-ingestion-dead-letter-worker:pull"
    )
    for _ in range(45):
        pulled = emulator_json(subscription, "POST", b'{"maxMessages":10}')
        messages = pulled.get("receivedMessages", [])
        if messages:
            ack_ids = [message["ackId"] for message in messages]
            emulator_json(
                "/v1/projects/local-project/subscriptions/knowledgeforge-ingestion-dead-letter-worker:acknowledge",
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
            break
        if status == "failed":
            raise SystemExit("async smoke test failed: worker marked document failed")
        time.sleep(1)
    else:
        raise SystemExit("async smoke test timed out waiting for ready")

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
