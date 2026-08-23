from google.cloud import pubsub_v1, storage  # type: ignore[import-untyped]


class CloudStorageClient:
    def __init__(self, bucket_name: str) -> None:
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def upload(self, filename: str, content: bytes, content_type: str | None) -> str:
        blob = self.bucket.blob(filename)
        blob.upload_from_string(content, content_type=content_type)
        return f"gs://{self.bucket.name}/{filename}"

    def download(self, uri: str) -> bytes:
        prefix = f"gs://{self.bucket.name}/"
        if not uri.startswith(prefix):
            raise ValueError("storage URI does not belong to configured bucket")
        return bytes(self.bucket.blob(uri[len(prefix) :]).download_as_bytes())


class PubSubPublisher:
    def __init__(self, project_id: str, topic_name: str) -> None:
        self.client = pubsub_v1.PublisherClient()
        self.topic_path = self.client.topic_path(project_id, topic_name)

    def publish(self, payload: bytes) -> str:
        return str(self.client.publish(self.topic_path, payload).result())
