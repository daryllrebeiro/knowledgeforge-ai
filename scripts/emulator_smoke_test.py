"""Exercise register -> upload -> async worker -> ready against the full local stack."""

import json
import os
import time
import urllib.request
import uuid

BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")


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


def main() -> None:
    email = f"emulator-{uuid.uuid4()}@example.test"
    password = "local-emulator-password"
    registration = json.dumps(
        {"email": email, "password": password, "tenant_name": "Local emulator"}
    ).encode()
    token = request("/auth/register", "POST", registration, {"Content-Type": "application/json"})[
        "access_token"
    ]

    boundary = f"----KnowledgeForge{uuid.uuid4().hex}"
    content = b"# Local async document\n\nThe worker should make this document ready."
    multipart = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="local.md"\r\n'
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
    document_id = uploaded["document_id"]
    for _ in range(30):
        status = request(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})[
            "status"
        ]
        if status == "ready":
            print(f"async smoke test passed: {document_id}")
            return
        if status == "failed":
            raise SystemExit("async smoke test failed: worker marked document failed")
        time.sleep(1)
    raise SystemExit("async smoke test timed out waiting for ready")


if __name__ == "__main__":
    main()
