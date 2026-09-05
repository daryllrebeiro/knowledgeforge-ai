"""Measure retrieval and answer quality over the repository-owned golden set.

Modes:

- ``--local``: deterministic local embeddings, retrieval-only metrics (no
  generation, so correctness/refusal checks are skipped and reported as such).
- Gemini (default): real embeddings plus end-to-end answer generation using the
  same prompt/citation pipeline the API serves (R1.1's corrected citations),
  with keyword-based answer correctness and refusal checks.

Every miss is classified as one of:

- ``retrieval-miss`` — the expected source was not in the top-5 chunks.
- ``generation-miss`` — the source was retrieved but key facts are absent.
- ``false-refusal`` — the model refused an answerable question.
- ``citation-miss`` — the answer was correct but cited the wrong document.
- ``false-answer`` — the model answered a question designed to be unanswerable.
- ``hybrid-search-candidate`` / ``reranking-candidate`` — local-mode retrieval
  misses split by whether the question lexically overlaps the source at all.

Run from the repository root: ``uv run python -m evaluation.run_phase12_eval``
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

from knowledgeforge.config import get_settings
from knowledgeforge.generation.gemini import GeminiTextGenerator
from knowledgeforge.generation.generate import GeneratedAnswer, generate_answer
from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.ingestion.chunk import TextChunk, chunk_pages
from knowledgeforge.ingestion.embed import embed_texts, embed_texts_local

from evaluation.chunking_profiles import PROFILES

ROOT = Path(__file__).resolve().parents[1]
REFUSAL_MARKER = "i don't have enough information"


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(a * a for a in right))
    return numerator / denominator if denominator else 0.0


def lexical_overlap(question: str, text: str) -> float:
    question_terms = set(question.lower().split())
    text_terms = set(text.lower().split())
    return len(question_terms & text_terms) / len(question_terms) if question_terms else 0.0


def load_sources(golden: list[dict[str, Any]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for case in golden:
        source = case.get("source")
        if source is None:
            continue
        path = ROOT / source
        sources[source] = path.read_text(encoding="utf-8")
    return sources


def answer_contains_facts(answer: str, keywords: list[str]) -> bool:
    lowered = answer.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


def generate_for_case(
    generator: GeminiTextGenerator,
    question: str,
    ranked: list[tuple[str, TextChunk]],
) -> tuple[GeneratedAnswer, dict[str, int]]:
    """Build the API-shaped prompt over the top-5 chunks and generate an answer.

    Returns the answer plus the source-to-document-number mapping used in the
    prompt, so parsed citations can be attributed back to sources.
    """
    source_numbers: dict[str, int] = {}
    labeled: list[LabeledChunk] = []
    for source, chunk in ranked:
        if source not in source_numbers:
            source_numbers[source] = len(source_numbers) + 1
        labeled.append(LabeledChunk(label=f"doc {source_numbers[source]}", chunk=chunk))
    return generate_answer(generator, question, labeled), source_numbers


def evaluate_profile(
    golden: list[dict[str, Any]],
    sources: dict[str, str],
    *,
    chunk_size: int,
    overlap: int,
    section_aware: bool,
    local: bool,
    hybrid: bool,
    generator: GeminiTextGenerator | None,
) -> dict[str, Any]:
    chunks: list[tuple[str, TextChunk]] = []
    for source, text in sources.items():
        for chunk in chunk_pages(
            [(1, text)], chunk_size=chunk_size, overlap=overlap, section_aware=section_aware
        ):
            chunks.append((source, chunk))
    texts = [chunk.text for _, chunk in chunks]
    settings = get_settings()
    if local:
        document_embeddings = embed_texts_local(texts)
        question_embeddings = embed_texts_local([case["question"] for case in golden])
    else:
        from google import genai

        if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
            raise SystemExit("Set GEMINI_API_KEY or pass --local.")
        client = genai.Client(api_key=settings.gemini_api_key)
        document_embeddings = embed_texts(client, texts, model=settings.gemini_embedding_model)
        question_embeddings = embed_texts(
            client, [case["question"] for case in golden], model=settings.gemini_embedding_model
        )

    hits = 0
    answerable = 0
    correct = 0
    graded = 0
    refusal_total = 0
    refusal_graded = 0
    refusal_correct = 0
    errors: list[dict[str, str]] = []
    for case, question_embedding in zip(golden, question_embeddings, strict=True):
        ranked = sorted(
            zip(chunks, document_embeddings, strict=True),
            key=lambda item: (
                cosine(question_embedding, item[1])
                + (0.15 * lexical_overlap(case["question"], item[0][1].text) if hybrid else 0.0)
            ),
            reverse=True,
        )
        top = ranked[:5]
        expected_source = case.get("source")
        keywords = case.get("expected_answer_keywords") or []
        expect_refusal = bool(case.get("expected_refusal"))

        if expect_refusal:
            refusal_total += 1
            if generator is None:
                continue
            answer, _ = generate_for_case(generator, case["question"], top)
            refusal_graded += 1
            if REFUSAL_MARKER in answer.answer.lower():
                refusal_correct += 1
            else:
                errors.append(
                    {
                        "question": case["question"],
                        "classification": "false-answer",
                        "answer_excerpt": answer.answer[:200],
                    }
                )
            continue

        answerable += 1
        top_sources = [source for source, _ in top]
        hit = expected_source in top_sources
        hits += int(hit)
        if not hit:
            if local:
                overlap_terms = set(case["question"].lower().split()) & set(
                    sources[expected_source].lower().split()
                )
                classification = (
                    "hybrid-search-candidate" if not overlap_terms else "reranking-candidate"
                )
            else:
                classification = "retrieval-miss"
            errors.append(
                {
                    "question": case["question"],
                    "expected_source": expected_source,
                    "classification": classification,
                    "top_sources": ",".join(top_sources),
                }
            )
            continue

        if generator is None or not keywords:
            # Local mode (or a case without recorded key facts): retrieval-only.
            continue

        graded += 1
        answer, source_numbers = generate_for_case(generator, case["question"], top)
        refused = REFUSAL_MARKER in answer.answer.lower()
        if refused:
            errors.append(
                {
                    "question": case["question"],
                    "expected_source": expected_source,
                    "classification": "false-refusal",
                    "answer_excerpt": answer.answer[:200],
                }
            )
            continue
        if not answer_contains_facts(answer.answer, keywords):
            errors.append(
                {
                    "question": case["question"],
                    "expected_source": expected_source,
                    "classification": "generation-miss",
                    "missing_keywords": ",".join(
                        keyword
                        for keyword in keywords
                        if keyword.lower() not in answer.answer.lower()
                    ),
                }
            )
            continue
        cited_numbers = {citation.document_index for citation in answer.citations}
        cited_sources = {
            source for source, number in source_numbers.items() if number in cited_numbers
        }
        if expected_source not in cited_sources:
            errors.append(
                {
                    "question": case["question"],
                    "expected_source": expected_source,
                    "classification": "citation-miss",
                    "answer_excerpt": answer.answer[:200],
                }
            )
            continue
        correct += 1

    return {
        "hit_at_5": hits / answerable if answerable else 0.0,
        "hits": hits,
        "retrieval_total": answerable,
        "answer_correctness": correct / graded if graded else None,
        "correct_answers": correct,
        "graded_answers": graded,
        "refusal_accuracy": refusal_correct / refusal_graded if refusal_graded else None,
        "correct_refusals": refusal_correct,
        "refusal_graded": refusal_graded,
        "refusal_total": refusal_total,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Use deterministic local embeddings")
    parser.add_argument(
        "--profiles",
        default=",".join(profile.name for profile in PROFILES),
        help="Comma-separated profile names from evaluation/chunking_profiles.py",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/phase12-evaluation.json")
    args = parser.parse_args()
    selected = {name.strip() for name in args.profiles.split(",")}
    profiles = [profile for profile in PROFILES if profile.name in selected]
    if not profiles:
        raise SystemExit(f"No matching profiles in: {args.profiles}")

    golden = json.loads((Path(__file__).parent / "phase12-golden-set.json").read_text())
    sources = load_sources(golden)
    generator: GeminiTextGenerator | None = None
    if not args.local:
        from google import genai

        settings = get_settings()
        if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
            raise SystemExit("Set GEMINI_API_KEY or pass --local.")
        generator = GeminiTextGenerator(
            genai.Client(api_key=settings.gemini_api_key), settings.gemini_model
        )

    results: dict[str, Any] = {}
    for profile in profiles:
        results[f"{profile.name}-vector"] = evaluate_profile(
            golden,
            sources,
            chunk_size=profile.chunk_size,
            overlap=profile.overlap,
            section_aware=profile.section_aware,
            local=args.local,
            hybrid=False,
            generator=generator,
        )
        results[f"{profile.name}-hybrid"] = evaluate_profile(
            golden,
            sources,
            chunk_size=profile.chunk_size,
            overlap=profile.overlap,
            section_aware=profile.section_aware,
            local=args.local,
            hybrid=True,
            generator=generator,
        )
    payload = {
        "mode": "local" if args.local else "gemini",
        "golden_set_size": len(golden),
        "profiles": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for name, result in results.items():
        correctness = result["answer_correctness"]
        correctness_text = "n/a" if correctness is None else f"{correctness:.1%}"
        refusal = result["refusal_accuracy"]
        refusal_text = "n/a" if refusal is None else f"{refusal:.1%}"
        print(
            f"{name}: Hit@5={result['hits']}/{result['retrieval_total']}"
            f" ({result['hit_at_5']:.1%}) correctness={correctness_text}"
            f" ({result['correct_answers']}/{result['graded_answers']})"
            f" refusal={refusal_text} ({result['correct_refusals']}/{result['refusal_total']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
