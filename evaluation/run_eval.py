"""Run the golden set against a running KnowledgeForge API."""

import json
import sys
from pathlib import Path

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    cases = json.loads((Path(__file__).parent / "golden-set.json").read_text())
    hits = 0
    with httpx.Client(base_url=base_url, timeout=60) as client:
        for case in cases:
            response = client.post("/ask", json={"question": case["question"]})
            response.raise_for_status()
            result = response.json()
            cited_documents = {citation["document_id"] for citation in result["citations"]}
            hit = case["expected_document_id"] in cited_documents
            hits += int(hit)
            print(f"{'PASS' if hit else 'FAIL'}: {case['question']}")
    print(f"Hit@5: {hits}/{len(cases)} ({hits / len(cases):.1%})")
    return 0 if hits == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
