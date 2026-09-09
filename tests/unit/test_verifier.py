from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.generation.verifier import (
    EntailmentVerdict,
    EntailmentVerifier,
)
from knowledgeforge.ingestion.chunk import TextChunk


def test_split_into_claims() -> None:
    verifier = EntailmentVerifier()
    text = "The vendor provides cloud hosting [doc 1, page 2]. The payment term is Net-30 [doc 2, page 4]."
    claims = verifier.split_into_claims(text)
    assert len(claims) == 2
    assert "The vendor provides cloud hosting" in claims[0]
    assert "The payment term is Net-30" in claims[1]


def test_verifier_entailed_facts() -> None:
    verifier = EntailmentVerifier()
    chunk = TextChunk(
        text="Acme Corp provides automated cloud hosting infrastructure with 99.9% SLA.",
        page=1,
    )
    answer = "Acme Corp provides automated cloud hosting infrastructure [doc 1, page 1]."
    result = verifier.verify_local(answer, [LabeledChunk(label="doc 1", chunk=chunk)])
    assert result.is_grounded is True
    assert result.grounding_score >= 0.7
    assert any(c.verdict == EntailmentVerdict.ENTAILED for c in result.claims)


def test_verifier_detects_contradiction() -> None:
    verifier = EntailmentVerifier()
    chunk = TextChunk(
        text="The software license requires an annual recurring subscription fee.",
        page=1,
    )
    # Claim with contradictory negation
    answer = "The software license does not require an annual recurring subscription fee."
    result = verifier.verify_local(answer, [LabeledChunk(label="doc 1", chunk=chunk)])
    assert result.is_grounded is False
    assert any(c.verdict == EntailmentVerdict.CONTRADICTED for c in result.claims)
    assert "removed by guardrail" in result.verified_answer


def test_verifier_unverified_unsupported_facts() -> None:
    verifier = EntailmentVerifier()
    chunk = TextChunk(text="The office is located in Seattle, Washington.", page=1)
    answer = "The CEO went to Tokyo for vacation."
    result = verifier.verify_local(answer, [LabeledChunk(label="doc 1", chunk=chunk)])
    assert result.is_grounded is False
    assert any(c.verdict == EntailmentVerdict.UNVERIFIED for c in result.claims)
