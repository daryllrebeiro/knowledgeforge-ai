"""Minimal manual check for Gemini credentials and SDK configuration."""

from google import genai

from knowledgeforge.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_api_key == "replace-me":
        raise SystemExit("Set GEMINI_API_KEY in .env before running this smoke test.")

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents="Reply with exactly: KnowledgeForge Gemini smoke test passed.",
    )
    print(response.text)


if __name__ == "__main__":
    main()
