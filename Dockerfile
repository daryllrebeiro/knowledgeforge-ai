FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Versions come from uv.lock so CI and production run identical dependency sets.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts

RUN uv sync --frozen --no-dev --compile-bytecode

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Never run as root; the API writes nothing to the container filesystem.
RUN useradd --system --uid 1001 appuser
USER appuser

EXPOSE 8000
# Cloud Run uses its own probes, but local Docker runs (emulator stack) use this.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "knowledgeforge.main:app", "--host", "0.0.0.0", "--port", "8000"]
