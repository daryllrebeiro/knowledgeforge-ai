"""Run a small real-Gemini embedding retrieval evaluation against local documents."""

import json
import math
from pathlib import Path

from google import genai

from knowledgeforge.config import get_settings
from knowledgeforge.ingestion.embed import embed_texts


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def main() -> int:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
        raise SystemExit("Set GEMINI_API_KEY for the live evaluation.")
    client = genai.Client(api_key=settings.gemini_api_key)
    corpus_dir = Path(__file__).parent / "local-corpus"
    documents = sorted(corpus_dir.glob("*.md"))
    texts = [document.read_text(encoding="utf-8") for document in documents]
    cases = json.loads((Path(__file__).parent / "live-golden-set.json").read_text())
    document_embeddings = embed_texts(client, texts, model=settings.gemini_embedding_model)
    question_embeddings = embed_texts(
        client, [case["question"] for case in cases], model=settings.gemini_embedding_model
    )
    hits = 0
    for case, question_embedding in zip(cases, question_embeddings, strict=True):
        ranked = sorted(
            zip(documents, document_embeddings, strict=True),
            key=lambda item: cosine(question_embedding, item[1]),
            reverse=True,
        )
        hit = ranked[0][0].name == case["document"]
        hits += int(hit)
        print(f"{'PASS' if hit else 'FAIL'}: {case['question']} -> {ranked[0][0].name}")
    score = hits / len(cases)
    print(f"Live local-corpus Hit@1: {hits}/{len(cases)} ({score:.1%})")
    return 0 if hits == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
