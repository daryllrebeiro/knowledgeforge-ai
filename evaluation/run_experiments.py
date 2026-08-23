"""Run retrieval evaluation for each configured chunking profile.

The API process must be restarted with CHUNK_SIZE/CHUNK_OVERLAP for each profile so
re-ingestion uses the selected strategy. This runner records comparable Hit@5 results.
"""

import json
import sys
from pathlib import Path

import httpx

from evaluation.chunking_profiles import PROFILES


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    cases = json.loads((Path(__file__).parent / "golden-set.json").read_text())
    print("Profile comparison requires the API to be restarted and corpus re-ingested per profile.")
    for profile in PROFILES:
        hits = 0
        with httpx.Client(base_url=base_url, timeout=60) as client:
            for case in cases:
                response = client.post("/ask", json={"question": case["question"]})
                response.raise_for_status()
                citations = response.json()["citations"]
                hits += int(
                    case["expected_document_id"] in {item["document_id"] for item in citations}
                )
        print(f"{profile.name}: Hit@5={hits}/{len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
