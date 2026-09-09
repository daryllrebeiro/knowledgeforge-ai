import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

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
    grounding_score: float = 1.0
    is_grounded: bool = True


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
    verifier: Any = None,
) -> GeneratedAnswer:
    result = generator.generate(build_prompt(question, chunks, extractions))
    response = result.text.strip()
    grounding_score = 1.0
    is_grounded = True
    final_answer = response
    if verifier is not None:
        v_res = verifier.verify(response, chunks)
        grounding_score = v_res.grounding_score
        is_grounded = v_res.is_grounded
        final_answer = v_res.verified_answer

    return GeneratedAnswer(
        answer=final_answer,
        citations=parse_citations(final_answer),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        grounding_score=grounding_score,
        is_grounded=is_grounded,
    )
