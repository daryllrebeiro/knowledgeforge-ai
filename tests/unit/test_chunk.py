import pytest

from knowledgeforge.ingestion.chunk import chunk_pages


def test_chunk_pages_preserves_page_and_overlap() -> None:
    chunks = chunk_pages([(3, "one two three four five six")], chunk_size=4, overlap=2)

    assert [(chunk.page, chunk.text) for chunk in chunks] == [
        (3, "one two three four"),
        (3, "three four five six"),
    ]


def test_chunk_pages_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_pages([(1, "text")], chunk_size=4, overlap=4)


def test_section_aware_chunking_preserves_heading() -> None:
    chunks = chunk_pages(
        [(1, "# Installation\nRun the installer."), (2, "# Usage\nAsk a question.")],
        chunk_size=20,
        overlap=2,
        section_aware=True,
    )

    assert [(chunk.section, chunk.text) for chunk in chunks] == [
        ("Installation", "Run the installer."),
        ("Usage", "Ask a question."),
    ]
