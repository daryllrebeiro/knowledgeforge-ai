# knowledgeforge-ai
Production-grade RAG platform with grounded, cited answers over your documents. Gemini, pgvector built from first principles, evaluated on a golden dataset, not wrapped in LangChain.

## Local development

```powershell
Copy-Item .env.example .env
uv sync --dev
docker compose up -d
uv run uvicorn knowledgeforge.main:app --reload
```

The liveness endpoint is available at `http://localhost:8000/health`.

Run the checks with:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

The Gemini smoke test requires a real `GEMINI_API_KEY` in `.env`:

```powershell
uv run python scripts/gemini_smoke_test.py
```
