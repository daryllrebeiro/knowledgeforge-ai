"""Measure retrieval quality over the repository-owned Phase 12 corpus."""

import argparse
import json
import math
from pathlib import Path

from knowledgeforge.config import get_settings
from knowledgeforge.ingestion.chunk import chunk_pages
from knowledgeforge.ingestion.embed import embed_texts, embed_texts_local

ROOT = Path(__file__).resolve().parents[1]
PROFILES = (("baseline-500-100", 500, 100), ("large-800-150", 800, 150))


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def lexical_overlap(question: str, text: str) -> float:
    question_terms = set(question.lower().split())
    text_terms = set(text.lower().split())
    return len(question_terms & text_terms) / len(question_terms) if question_terms else 0.0


def load_sources(golden: list[dict[str, str]]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for case in golden:
        source = case["source"]
        path = ROOT / source
        sources[source] = path.read_text(encoding="utf-8")
    return sources


def evaluate_profile(
    golden: list[dict[str, str]],
    sources: dict[str, str],
    *,
    chunk_size: int,
    overlap: int,
    local: bool,
    hybrid: bool,
) -> dict[str, object]:
    chunks = []
    for source, text in sources.items():
        for chunk in chunk_pages([(1, text)], chunk_size=chunk_size, overlap=overlap):
            chunks.append((source, chunk.text))
    texts = [text for _, text in chunks]
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
    errors: list[dict[str, str]] = []
    for case, question_embedding in zip(golden, question_embeddings, strict=True):
        ranked = sorted(
            zip(chunks, document_embeddings, strict=True),
            key=lambda item: (
                cosine(question_embedding, item[1])
                + (0.15 * lexical_overlap(case["question"], item[0][1]) if hybrid else 0.0)
            ),
            reverse=True,
        )
        top_sources = [source for (source, _), _ in ranked[:5]]
        hit = case["source"] in top_sources
        hits += int(hit)
        if not hit:
            overlap_terms = set(case["question"].lower().split()) & set(
                sources[case["source"]].lower().split()
            )
            errors.append(
                {
                    "question": case["question"],
                    "expected_source": case["source"],
                    "classification": "hybrid-search-candidate"
                    if not overlap_terms
                    else "reranking-candidate",
                    "top_sources": ",".join(top_sources),
                }
            )
    return {"hit_at_5": hits / len(golden), "hits": hits, "total": len(golden), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="Use deterministic local embeddings")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/phase12-evaluation.json")
    args = parser.parse_args()
    golden = json.loads((Path(__file__).parent / "phase12-golden-set.json").read_text())
    sources = load_sources(golden)
    results = {}
    for name, size, overlap in PROFILES:
        results[f"{name}-vector"] = evaluate_profile(
            golden, sources, chunk_size=size, overlap=overlap, local=args.local, hybrid=False
        )
        results[f"{name}-hybrid"] = evaluate_profile(
            golden, sources, chunk_size=size, overlap=overlap, local=args.local, hybrid=True
        )
    payload = {"mode": "local" if args.local else "gemini", "profiles": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for name, result in results.items():
        print(f"{name}: Hit@5={result['hits']}/{result['total']} ({result['hit_at_5']:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
