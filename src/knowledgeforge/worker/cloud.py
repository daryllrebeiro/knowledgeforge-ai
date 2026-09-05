import os

from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1, storage  # type: ignore[import-untyped]

from knowledgeforge.reliability import with_retry


class CloudStorageClient:
    def __init__(self, bucket_name: str, project_id: str = "") -> None:
        credentials = (
            AnonymousCredentials()  # type: ignore[no-untyped-call]
            if os.getenv("STORAGE_EMULATOR_HOST")
            else None
        )
        self.client = storage.Client(project=project_id or None, credentials=credentials)
        self.bucket = self.client.bucket(bucket_name)

    @with_retry
    def upload(self, filename: str, content: bytes, content_type: str | None) -> str:
        blob = self.bucket.blob(filename)
        blob.upload_from_string(content, content_type=content_type)
        return f"gs://{self.bucket.name}/{filename}"

    @with_retry
    def download(self, uri: str) -> bytes:
        prefix = f"gs://{self.bucket.name}/"
        if not uri.startswith(prefix):
            raise ValueError("storage URI does not belong to configured bucket")
        return bytes(self.bucket.blob(uri[len(prefix) :]).download_as_bytes())

    @with_retry
    def delete(self, uri: str) -> None:
        prefix = f"gs://{self.bucket.name}/"
        if not uri.startswith(prefix):
            raise ValueError("storage URI does not belong to configured bucket")
        self.bucket.blob(uri[len(prefix) :]).delete()


class PubSubPublisher:
    def __init__(self, project_id: str, topic_name: str) -> None:
        credentials = (
            AnonymousCredentials()  # type: ignore[no-untyped-call]
            if os.getenv("PUBSUB_EMULATOR_HOST")
            else None
        )
        self.client = pubsub_v1.PublisherClient(credentials=credentials)
        self.topic_path = self.client.topic_path(project_id, topic_name)

    @with_retry
    def publish(self, payload: bytes) -> str:
        return str(self.client.publish(self.topic_path, payload).result())
