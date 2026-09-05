"""Plain-text and HTML extraction (F3).

HTML is handled with the standard library parser — no new dependency — by
dropping script/style content and emitting each block-level element as one
"page" so chunk locations stay meaningful.
"""

from html.parser import HTMLParser
from typing import BinaryIO

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "details",
    "div",
    "dd",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "section",
    "table",
    "tr",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag == "br" and not self._skip_depth:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._current.append(data.strip())

    def _flush(self) -> None:
        if self._current:
            self._chunks.append(" ".join(self._current))
            self._current = []

    def blocks(self) -> list[str]:
        self._flush()
        return self._chunks


def extract_html(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract HTML text, block by block, as (block number, text) locations."""
    source = file.read().decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(source)
    parser.close()
    return [(index, text) for index, text in enumerate(parser.blocks(), start=1)]


def extract_text(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract plain text, one paragraph per "page", so chunks have locations."""
    source = file.read().decode("utf-8", errors="replace")
    paragraphs = [paragraph.strip() for paragraph in source.split("\n\n")]
    return [(index, text) for index, text in enumerate(paragraphs, start=1) if text]
