"""The deterministic local answer generator (LOCAL_GENERATION)."""

from knowledgeforge.generation.generate import parse_citations
from knowledgeforge.generation.local import local_answer
from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.ingestion.chunk import TextChunk


def _labeled(label: str, page: int) -> LabeledChunk:
    return LabeledChunk(label=label, chunk=TextChunk(f"chunk on page {page}", page))


def test_local_answer_cites_every_chunk_in_parseable_format() -> None:
    chunks = [_labeled("doc 1", 3), _labeled("doc 2", 11)]

    answer = local_answer("What is the answer?", chunks)

    assert answer == "Local answer for: What is the answer? [doc 1, page 3] [doc 2, page 11]"
    assert [(c.document_index, c.page) for c in parse_citations(answer)] == [(1, 3), (2, 11)]


def test_local_answer_refuses_without_retrieved_chunks() -> None:
    assert local_answer("Anything?", []) == "I don't have enough information."
    assert parse_citations(local_answer("Anything?", [])) == []
