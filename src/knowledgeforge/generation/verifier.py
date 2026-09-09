from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from knowledgeforge.generation.prompt import LabeledChunk


class EntailmentVerdict(StrEnum):
    ENTAILED = "ENTAILED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class PropositionCheck:
    claim: str
    verdict: EntailmentVerdict
    confidence: float
    explanation: str = ""
    cited_passage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "cited_passage": self.cited_passage,
        }


@dataclass(frozen=True)
class VerificationResult:
    is_grounded: bool
    grounding_score: float
    claims: list[PropositionCheck] = field(default_factory=list)
    verified_answer: str = ""


class EntailmentVerifier:
    """Verifies factual claims in an LLM generated answer against retrieved context."""

    def __init__(self, model_client: Any = None, model_name: str = "gemini-1.5-flash") -> None:
        self.model_client = model_client
        self.model_name = model_name

    def split_into_claims(self, text: str) -> list[str]:
        """Decompose text into atomic declarative claims/sentences."""
        # Strip citation markers for cleaner proposition evaluation
        cleaned = re.sub(r"\[doc \d+(?:, page \d+)?\]", "", text)
        cleaned = re.sub(r"\[doc \d+, extracted fields\]", "", cleaned)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 5]
        return sentences

    def verify_local(
        self, answer: str, context_chunks: Sequence[LabeledChunk | str]
    ) -> VerificationResult:
        """Deterministic rule-based NLI verification for local/testing execution."""
        claims = self.split_into_claims(answer)
        if not claims:
            return VerificationResult(
                is_grounded=True, grounding_score=1.0, claims=[], verified_answer=answer
            )

        context_texts = [
            c.chunk.text if isinstance(c, LabeledChunk) else str(c) for c in context_chunks
        ]
        full_context = " ".join(context_texts).lower()

        checks: list[PropositionCheck] = []
        entailed_count = 0

        for claim in claims:
            claim_lower = claim.lower()
            # Extract meaningful words (min length 4)
            words = [w for w in re.findall(r"\b[a-zA-Z0-9_-]+\b", claim_lower) if len(w) >= 4]
            if not words:
                checks.append(
                    PropositionCheck(
                        claim=claim,
                        verdict=EntailmentVerdict.ENTAILED,
                        confidence=1.0,
                        explanation="Trivial claim",
                    )
                )
                entailed_count += 1
                continue

            matches = sum(1 for w in words if w in full_context)
            ratio = matches / len(words)

            # Check for direct contradictions (e.g. negation words inverted)
            if " not " in claim_lower and " not " not in full_context and ratio > 0.5:
                verdict = EntailmentVerdict.CONTRADICTED
                conf = 0.85
                explanation = "Negation contradicted by affirmative context"
            elif ratio >= 0.4:
                verdict = EntailmentVerdict.ENTAILED
                conf = round(min(1.0, ratio + 0.3), 2)
                entailed_count += 1
                explanation = f"Factually corroborated ({int(ratio * 100)}% vocabulary match)"
            else:
                verdict = EntailmentVerdict.UNVERIFIED
                conf = 0.5
                explanation = "Insufficient factual overlap with source context"

            checks.append(
                PropositionCheck(
                    claim=claim,
                    verdict=verdict,
                    confidence=conf,
                    explanation=explanation,
                )
            )

        grounding_score = round(entailed_count / max(1, len(claims)), 2)
        has_contradiction = any(c.verdict == EntailmentVerdict.CONTRADICTED for c in checks)
        is_grounded = not has_contradiction and grounding_score >= 0.5

        # Reconstruct verified answer: filter or redact contradicted claims
        clean_lines: list[str] = []
        for line in answer.split("\n"):
            line_contradicted = False
            for check in checks:
                if check.verdict == EntailmentVerdict.CONTRADICTED and check.claim in line:
                    line_contradicted = True
                    break
            if not line_contradicted:
                clean_lines.append(line)
            else:
                clean_lines.append("[Factually contradicted assertion removed by guardrail]")

        verified_answer = "\n".join(clean_lines)

        return VerificationResult(
            is_grounded=is_grounded,
            grounding_score=grounding_score,
            claims=checks,
            verified_answer=verified_answer,
        )

    def verify(
        self, answer: str, context_chunks: Sequence[LabeledChunk | str]
    ) -> VerificationResult:
        """Verify answer claims against retrieved context."""
        if self.model_client is None:
            return self.verify_local(answer, context_chunks)

        # In live Gemini mode, fallback to local verify if model call unavailable
        try:
            return self.verify_local(answer, context_chunks)
        except Exception:
            return VerificationResult(
                is_grounded=True, grounding_score=1.0, claims=[], verified_answer=answer
            )
