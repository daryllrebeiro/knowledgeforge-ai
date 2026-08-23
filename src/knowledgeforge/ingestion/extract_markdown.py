from typing import BinaryIO

from markdown_it import MarkdownIt


def extract_markdown(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract Markdown text while retaining heading/paragraph order as locations."""
    source = file.read().decode("utf-8")
    tokens = MarkdownIt().parse(source)
    locations: list[tuple[int, str]] = []
    for index, token in enumerate(tokens, start=1):
        if token.type in {"heading_open", "paragraph_open", "fence"}:
            content = token.content.strip()
            if content:
                locations.append((index, content))
        elif token.type == "inline" and token.content.strip():
            locations.append((index, token.content.strip()))
    return locations
