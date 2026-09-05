"""Deterministic local answer generation for emulator-only environments."""

from collections.abc import Sequence

from knowledgeforge.generation.prompt import LabeledChunk, LabeledExtraction

REFUSAL = "I don't have enough information."


def local_answer(
    question: str,
    chunks: Sequence[LabeledChunk],
    extractions: Sequence[LabeledExtraction] = (),
) -> str:
    """Answer without Gemini by citing every retrieved chunk (LOCAL_GENERATION=true).

    Citation markers use the same ``[doc N, page M]`` and ``[doc N, extracted
    fields]`` formats the Gemini prompt asks for, so ``parse_citations`` and the
    rest of the ask pipeline (telemetry, conversation history, SSE events) are
    exercised unchanged — including the extracted-fields citation path. With no
    retrieved context this returns the exact refusal phrase the system prompt
    prescribes, so refusal handling is covered too.

    Emulator-only: local stack, chaos drills, load tests. Never for production.
    """
    markers: list[str] = [f"[{labeled.label}, page {labeled.chunk.page}]" for labeled in chunks]
    markers.extend(f"[{labeled.label}, extracted fields]" for labeled in extractions)
    if not markers:
        return REFUSAL
    return f"Local answer for: {question} {' '.join(markers)}"
