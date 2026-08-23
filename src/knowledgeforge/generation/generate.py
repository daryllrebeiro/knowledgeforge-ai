import re
from dataclasses import dataclass
from typing import Protocol

from knowledgeforge.generation.prompt import build_prompt
from knowledgeforge.ingestion.chunk import TextChunk


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Citation:
    page: int


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    citations: list[Citation]


def generate_answer(
    generator: TextGenerator,
    question: str,
    chunks: list[TextChunk],
) -> GeneratedAnswer:
    response = generator.generate(build_prompt(question, chunks)).strip()
    citations = [
        Citation(page=int(page)) for page in dict.fromkeys(re.findall(r"\[page (\d+)\]", response))
    ]
    return GeneratedAnswer(answer=response, citations=citations)
