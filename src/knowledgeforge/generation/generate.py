import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from knowledgeforge.generation.gemini import GenerationResult
from knowledgeforge.generation.prompt import LabeledChunk, LabeledExtraction, build_prompt

CITATION_PATTERN = re.compile(r"\[doc (\d+), page (\d+)\]")
EXTRACTED_CITATION_PATTERN = re.compile(r"\[doc (\d+), extracted fields\]")


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> GenerationResult: ...


@dataclass(frozen=True)
class Citation:
    document_index: int
    page: int | None = None


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    citations: list[Citation]
    input_tokens: int = 0
    output_tokens: int = 0


def parse_citations(text: str) -> list[Citation]:
    """Extract cited (doc, page) and (doc, extracted fields) pairs, deduplicated in order."""
    citations: list[Citation] = []
    seen: set[tuple[int, int | None]] = set()
    for document, page in CITATION_PATTERN.findall(text):
        key = (int(document), int(page))
        if key not in seen:
            seen.add(key)
            citations.append(Citation(document_index=key[0], page=key[1]))
    for (document,) in EXTRACTED_CITATION_PATTERN.findall(text):
        key = (int(document), None)
        if key not in seen:
            seen.add(key)
            citations.append(Citation(document_index=key[0], page=None))
    return citations


def generate_answer(
    generator: TextGenerator,
    question: str,
    chunks: Sequence[LabeledChunk],
    extractions: Sequence[LabeledExtraction] = (),
) -> GeneratedAnswer:
    result = generator.generate(build_prompt(question, chunks, extractions))
    response = result.text.strip()
    return GeneratedAnswer(
        answer=response,
        citations=parse_citations(response),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
