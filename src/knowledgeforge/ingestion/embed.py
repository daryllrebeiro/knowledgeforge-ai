import hashlib
from collections.abc import Sequence
from typing import Any, cast

from google import genai


def embed_texts_local(texts: Sequence[str], dimensions: int = 768) -> list[list[float]]:
    """Create deterministic local vectors for emulator-only integration tests."""
    vectors: list[list[float]] = []
    for text in texts:
        vector = [0.0] * dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        vectors.append(vector)
    return vectors


def embed_texts(
    client: genai.Client,
    texts: Sequence[str],
    *,
    model: str,
    batch_size: int = 100,
) -> list[list[float]]:
    """Embed texts in bounded batches so ingestion never makes one call per chunk."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    embeddings: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        response = client.models.embed_content(model=model, contents=cast(Any, batch))
        if response.embeddings is None:
            raise RuntimeError("Gemini returned no embeddings")
        for item in response.embeddings:
            if item.values is None:
                raise RuntimeError("Gemini returned an embedding without values")
            embeddings.append(list(item.values))
    return embeddings
