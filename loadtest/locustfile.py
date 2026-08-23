import os

from locust import HttpUser, between, task


class KnowledgeForgeUser(HttpUser):
    wait_time = between(1, 3)
    host = os.getenv("KNOWLEDGEFORGE_URL", "http://localhost:8000")

    def on_start(self) -> None:
        token = os.getenv("KNOWLEDGEFORGE_TOKEN")
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task(5)
    def ask(self) -> None:
        self.client.post("/ask", json={"question": "What does the corpus explain?"})

    @task(1)
    def health(self) -> None:
        self.client.get("/health")
