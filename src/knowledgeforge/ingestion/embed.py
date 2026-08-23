from collections.abc import Sequence
from typing import Any, cast

from google import genai


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
