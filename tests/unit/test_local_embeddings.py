from knowledgeforge.ingestion.embed import embed_texts_local


def test_local_embeddings_are_deterministic_and_vector_sized() -> None:
    first = embed_texts_local(["tenant scoped retrieval"])
    second = embed_texts_local(["tenant scoped retrieval"])
    assert first == second
    assert len(first) == 1
    assert len(first[0]) == 768
