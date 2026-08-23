from knowledgeforge.ingestion.dedup import decide_dedup


def test_dedup_new_file() -> None:
    assert decide_dedup(None, None, "new").action == "new"


def test_dedup_duplicate_file() -> None:
    decision = decide_dedup("same", 2, "same")
    assert decision == decision.__class__("duplicate", 2)


def test_dedup_changed_file_creates_version() -> None:
    assert decide_dedup("old", 2, "new").version == 3
