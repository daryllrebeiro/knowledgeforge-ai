from uuid import uuid4

from knowledgeforge.ingestion.dedup import decide_dedup


def test_dedup_new_file() -> None:
    assert decide_dedup(None, None).action == "new"
    assert decide_dedup(None, None).version == 1


def test_dedup_duplicate_file() -> None:
    existing = (uuid4(), 2)

    decision = decide_dedup(existing, None)

    assert decision.action == "duplicate"
    assert decision.version == 2


def test_dedup_changed_file_creates_next_version() -> None:
    previous = (uuid4(), 2)

    decision = decide_dedup(None, previous)

    assert decision.action == "new_version"
    assert decision.version == 3
