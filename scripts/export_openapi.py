"""Export or verify the KnowledgeForge OpenAPI schema against docs/openapi.json."""

import argparse
import json
import sys
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from knowledgeforge.main import app

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
OPENAPI_PATH = DOCS_DIR / "openapi.json"


def get_spec() -> dict:
    return get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or verify OpenAPI specification")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify docs/openapi.json matches the live app schema (exits 1 if different)",
    )
    args = parser.parse_args()

    current_spec = get_spec()
    serialized = json.dumps(current_spec, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OPENAPI_PATH.exists():
            print(
                f"ERROR: {OPENAPI_PATH} does not exist. Run without --check to generate it.",
                file=sys.stderr,
            )
            return 1
        committed_content = OPENAPI_PATH.read_text(encoding="utf-8")
        if committed_content != serialized:
            print(
                f"ERROR: OpenAPI schema drift detected between app and {OPENAPI_PATH}.\n"
                "Run 'python scripts/export_openapi.py' to update the committed spec.",
                file=sys.stderr,
            )
            return 1
        print("OpenAPI spec matches committed docs/openapi.json.")
        return 0

    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(serialized, encoding="utf-8")
    print(f"Exported OpenAPI spec to {OPENAPI_PATH} ({len(serialized)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
