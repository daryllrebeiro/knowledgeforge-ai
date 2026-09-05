"""Locust load profile for KnowledgeForge (R6: P95 /ask latency, error rate).

Against the local emulator stack (LOCAL_EMBEDDINGS + LOCAL_GENERATION) this
exercises the full ask pipeline without Gemini; against a deployed staging
environment it measures real provider latency.

Usage (locust is not a permanent dependency — pull it ad hoc):
    uv run --with locust locust -f scripts/locustfile.py --host http://localhost:8000

Headless example (numbers for docs/validation-status.md):
    uv run --with locust locust -f scripts/locustfile.py --host http://localhost:8000 \
        --headless -u 20 -r 2 -t 2m --csv docs/evidence/load-run
"""

import io
import random
import uuid

from locust import HttpUser, between, task
from locust.exception import StopUser

QUESTIONS = (
    "What is the answer?",
    "Summarize the local document.",
    "What should the worker make this local async document?",
)


class KnowledgeForgeUser(HttpUser):
    """One simulated tenant: registers once, then asks and occasionally uploads."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        email = f"load-{uuid.uuid4()}@example.test"
        with self.client.post(
            "/auth/register",
            json={
                "email": email,
                "password": "load-test-password",
                "tenant_name": "Load test",
            },
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                self.token = response.json()["access_token"]
            else:
                response.failure(f"register returned {response.status_code}")
                raise StopUser

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @task(8)
    def ask(self) -> None:
        self.client.post(
            "/ask",
            json={"question": random.choice(QUESTIONS)},
            headers=self._auth,
            name="/ask",
        )

    @task(1)
    def upload(self) -> None:
        content = f"# Load document {uuid.uuid4()}\n\nBody text for load testing.".encode()
        self.client.post(
            "/documents",
            files={"file": (f"load-{uuid.uuid4()}.md", io.BytesIO(content), "text/markdown")},
            headers=self._auth,
            name="/documents [upload]",
        )

    @task(1)
    def list_documents(self) -> None:
        self.client.get("/documents", headers=self._auth, name="/documents [list]")
