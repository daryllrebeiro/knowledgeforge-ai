"""Plain-text and HTML extraction (F3)."""

from io import BytesIO

from knowledgeforge.ingestion.extract_text import extract_html, extract_text


def test_extract_text_splits_paragraphs_into_locations() -> None:
    content = b"First paragraph.\n\nSecond paragraph.\n\n\nThird paragraph."

    pages = extract_text(BytesIO(content))

    assert pages == [(1, "First paragraph."), (2, "Second paragraph."), (3, "Third paragraph.")]


def test_extract_text_skips_empty_paragraphs() -> None:
    assert extract_text(BytesIO(b"\n\n\n")) == []


def test_extract_html_drops_script_and_style_content() -> None:
    content = (
        b"<html><head><style>body { color: red }</style></head>"
        b"<body><p>Visible text.</p><script>alert('hidden')</script></body></html>"
    )

    pages = extract_html(BytesIO(content))

    assert pages == [(1, "Visible text.")]
    assert all("hidden" not in text for _, text in pages)
    assert all("color" not in text for _, text in pages)


def test_extract_html_emits_each_block_element_separately() -> None:
    content = b"<div><h1>Title</h1><p>Intro paragraph.</p><p>Body paragraph.</p></div>"

    pages = extract_html(BytesIO(content))

    assert [text for _, text in pages] == ["Title", "Intro paragraph.", "Body paragraph."]


def test_extract_html_unescapes_entities() -> None:
    pages = extract_html(BytesIO(b"<p>Fish &amp; chips &lt;3</p>"))

    assert pages == [(1, "Fish & chips <3")]
