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

## One-command deployment (free tier)

Deploy all four services (API, ingestion worker, extraction worker, outbox dispatcher) to Cloud Run with the same pull/test/deploy flow used in [the-visualizer](https://github.com/daryllrebeiro/the-visualizer). Everything provisioned sits in a Google Cloud no-cost tier; provide your own Postgres+pgvector via `DATABASE_URL` (free options: Neon, Supabase).

```powershell
gcloud auth login
$env:DATABASE_URL = "postgresql://..."   # free pgvector host
$env:GEMINI_API_KEY = "..."
./scripts/pull-and-deploy.ps1            # stash -> pull -> lint/mypy/tests -> deploy
```

Or deploy directly (Bash equivalent: `./scripts/deploy-cloudrun.sh`). Both are idempotent and handle Artifact Registry (commit-SHA images), Secret Manager (values injected out-of-band; rotation is opt-in via `ROTATE_SECRETS=true`), GCS, Pub/Sub (ingestion + extraction topics, push with OIDC, dead-letter policies), the extraction service, the outbox Cloud Run Job + Cloud Scheduler trigger, dedicated least-privilege service accounts, migrations (001-014), and a live health + functional smoke test including the structured-extraction loop. See `docs/roadmap.md` → R7 and Phase 2.5 for details.

## Product and support documents

- [Privacy policy](docs/privacy-policy.md)
- [Terms of service](docs/terms-of-service.md)
- [Support path](docs/support.md)
