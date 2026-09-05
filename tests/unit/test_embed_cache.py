"""Embedding cache behavior (F2): hits skip the provider, misses populate it."""

from knowledgeforge.ingestion import embed_cache
from knowledgeforge.ingestion.embed import EmbeddingResult


class FakeCursor:
    def __init__(self, cached_rows: list[tuple] | None = None) -> None:
        self.cached_rows = list(cached_rows or [])
        self.inserted: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self.last_sql = sql

    def fetchall(self) -> list[tuple]:
        return self.cached_rows

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.inserted.extend(rows)


class FakeConnection:
    def __init__(self, cached_rows: list[tuple] | None = None) -> None:
        self.cursor_result = FakeCursor(cached_rows)

    def cursor(self) -> FakeCursor:
        return self.cursor_result


def _patch_embeddings(monkeypatch, results: list[EmbeddingResult]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_embed(client, texts, *, model):
        calls.append(list(texts))
        return results.pop(0)

    monkeypatch.setattr(embed_cache, "embed_texts", fake_embed)
    monkeypatch.setattr(embed_cache, "register_vector", lambda connection: None)
    return calls


def test_cache_miss_embeds_and_stores(monkeypatch) -> None:
    calls = _patch_embeddings(
        monkeypatch, [EmbeddingResult(vectors=[[0.1], [0.2]], input_tokens=10)]
    )
    connection = FakeConnection()

    result = embed_cache.embed_texts_cached(
        connection, object(), ["alpha", "beta"], model="gemini-embedding-001"
    )

    assert result.vectors == [[0.1], [0.2]]
    assert result.input_tokens == 10
    assert calls == [["alpha", "beta"]]
    # Both misses were stored for future hits.
    assert len(connection.cursor_result.inserted) == 2


def test_cache_hit_skips_the_provider_call(monkeypatch) -> None:
    connection = FakeConnection()
    calls = _patch_embeddings(monkeypatch, [EmbeddingResult(vectors=[[0.9]], input_tokens=7)])
    embed_cache.embed_texts_cached(connection, object(), ["alpha"], model="m")

    stored = connection.cursor_result.inserted[0]
    hit_connection = FakeConnection(cached_rows=[(stored[0], "[0.9]")])
    calls.clear()

    result = embed_cache.embed_texts_cached(hit_connection, object(), ["alpha"], model="m")

    assert result.vectors == [[0.9]]
    # Cached hits cost zero embedding tokens.
    assert result.input_tokens == 0
    assert calls == []
    assert hit_connection.cursor_result.inserted == []


def test_cache_is_keyed_by_model(monkeypatch) -> None:
    connection = FakeConnection()
    _patch_embeddings(monkeypatch, [EmbeddingResult(vectors=[[0.9]], input_tokens=7)])
    embed_cache.embed_texts_cached(connection, object(), ["alpha"], model="model-a")
    stored = connection.cursor_result.inserted[0]

    calls = _patch_embeddings(monkeypatch, [EmbeddingResult(vectors=[[0.1]], input_tokens=3)])
    # Same text, different model: the cached vector must NOT be served.
    other_model_connection = FakeConnection(cached_rows=[(stored[0], "[0.9]")])
    result = embed_cache.embed_texts_cached(
        other_model_connection, object(), ["alpha"], model="model-b"
    )

    assert result.vectors == [[0.1]]
    assert calls == [["alpha"]]


def test_partial_hit_embeds_only_the_misses(monkeypatch) -> None:
    calls = _patch_embeddings(monkeypatch, [EmbeddingResult(vectors=[[0.9]], input_tokens=7)])
    connection = FakeConnection()
    embed_cache.embed_texts_cached(connection, object(), ["alpha"], model="m")
    alpha_digest = connection.cursor_result.inserted[0][0]

    calls.clear()
    _patch_embeddings(monkeypatch, [EmbeddingResult(vectors=[[0.3]], input_tokens=4)])
    partial_connection = FakeConnection(cached_rows=[(alpha_digest, "[0.9]")])
    result = embed_cache.embed_texts_cached(
        partial_connection, object(), ["alpha", "beta"], model="m"
    )

    assert result.vectors == [[0.9], [0.3]]
    assert calls == [["beta"]]
    assert result.input_tokens == 4
