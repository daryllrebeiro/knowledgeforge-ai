#!/usr/bin/env bash
# =============================================================================
# KnowledgeForge AI: R6 local execution evidence runner (Bash)
#
# Runs every locally-runnable R6 checklist item and records command output
# under docs/evidence/<timestamp>/ for docs/validation-status.md:
#   quality-gates      ruff check + format, mypy src
#   unit-tests         pytest (no integration/live) + coverage ratchet
#   integration-tests  real-PostgreSQL suite (only when DATABASE_URL is set)
#   emulator-smoke     full local stack: register -> upload -> worker -> ask
#   chaos-drills       Redis loss + Pub/Sub redelivery storm (F6.3)
#   backup-restore     pg_dump/pg_restore integrity (only when
#                      RESTORE_DATABASE_URL is set)
#   load-run           Locust /ask load profile (only when R6_LOAD_RUN=1;
#                      needs the stack started by emulator-smoke — this script
#                      keeps it up when R6_LOAD_RUN=1)
#
# Usage:
#   ./scripts/r6_evidence.sh
#   DATABASE_URL=... RESTORE_DATABASE_URL=... ./scripts/r6_evidence.sh
#   R6_LOAD_RUN=1 ./scripts/r6_evidence.sh
#
# Requirements: uv, Docker (for the emulator/chaos/load steps), pg_dump +
# pg_restore (for backup-restore).
# =============================================================================
set -uo pipefail

EVIDENCE_DIR="docs/evidence/$(date -u +%Y%m%d-%H%M%SZ)"
SUMMARY="$EVIDENCE_DIR/summary.txt"
mkdir -p "$EVIDENCE_DIR"
RESULTS=()
FULL="docker-compose.full.yml"
CHAOS="docker-compose.chaos.yml"

run_step() {
    # run_step <name> <function>: tee the function's output to a step log.
    local name="$1" fn="$2"
    local log="$EVIDENCE_DIR/$name.log"
    echo ""
    echo "=== $name ==="
    if "$fn" 2>&1 | tee "$log"; then
        RESULTS+=("PASS  $name")
    else
        RESULTS+=("FAIL  $name (see $log)")
    fi
}

stack_logs() {
    # stack_logs <name> <compose args...>: save container logs as evidence.
    local name="$1"
    shift
    docker compose "$@" logs --no-color >"$EVIDENCE_DIR/$name-stack.log" 2>&1 || true
}

quality_gates() {
    uv run ruff check . || return 1
    uv run ruff format --check . || return 1
    uv run mypy src
}

unit_tests() {
    uv run pytest -m "not integration and not live" \
        --cov=knowledgeforge --cov-report=term --cov-report=json:coverage.json || return 1
    uv run python scripts/coverage_ratchet.py coverage.json
}

integration_tests() {
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "SKIPPED: set DATABASE_URL to a local PostgreSQL to run the real-DB suite."
        return 0
    fi
    uv run python scripts/apply_migrations.py || return 1
    uv run pytest -m integration
}

emulator_smoke() {
    docker compose -f "$FULL" up -d --build --scale smoke=0 || return 1
    if ! docker compose -f "$FULL" run --rm smoke; then
        stack_logs emulator-smoke -f "$FULL"
        docker compose -f "$FULL" down --volumes --remove-orphans || true
        return 1
    fi
    stack_logs emulator-smoke -f "$FULL"
    if [ "${R6_LOAD_RUN:-0}" != "1" ]; then
        docker compose -f "$FULL" down --volumes --remove-orphans
    else
        echo "Stack left running for the load run (R6_LOAD_RUN=1)."
    fi
}

chaos_drills() {
    docker compose -f "$FULL" -f "$CHAOS" up -d --build --scale smoke=0 || return 1
    if ! docker compose -f "$FULL" -f "$CHAOS" run --rm chaos; then
        stack_logs chaos-drills -f "$FULL" -f "$CHAOS"
        docker compose -f "$FULL" -f "$CHAOS" down --volumes --remove-orphans || true
        return 1
    fi
    stack_logs chaos-drills -f "$FULL" -f "$CHAOS"
    docker compose -f "$FULL" -f "$CHAOS" down --volumes --remove-orphans
}

backup_restore() {
    if [ -z "${RESTORE_DATABASE_URL:-}" ]; then
        echo "SKIPPED: set RESTORE_DATABASE_URL (and DATABASE_URL) to run the"
        echo "backup/restore integrity check; pg_dump and pg_restore must be on PATH."
        return 0
    fi
    uv run python scripts/backup_restore_check.py
}

load_run() {
    if [ "${R6_LOAD_RUN:-0}" != "1" ]; then
        echo "SKIPPED: set R6_LOAD_RUN=1 to include a headless Locust run"
        echo "(10 users, 2/second spawn, 1 minute) against the local stack."
        return 0
    fi
    uv run --with locust locust -f scripts/locustfile.py \
        --host http://localhost:8000 --headless -u 10 -r 2 -t 1m \
        --csv "$EVIDENCE_DIR/load"
    local status=$?
    docker compose -f "$FULL" down --volumes --remove-orphans
    return "$status"
}

{
    echo "KnowledgeForge R6 evidence run"
    echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$SUMMARY"

run_step quality-gates quality_gates
run_step unit-tests unit_tests
run_step integration-tests integration_tests
# emulator-smoke must precede load-run (which reuses its still-running stack)
# and chaos-drills (which tears the stack down for its own override).
run_step emulator-smoke emulator_smoke
run_step load-run load_run
run_step chaos-drills chaos_drills
run_step backup-restore backup_restore

{
    echo ""
    echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' "${RESULTS[@]}"
} | tee -a "$SUMMARY"

echo ""
echo "Evidence written to $EVIDENCE_DIR (paste the relevant numbers into"
echo "docs/validation-status.md)."

FAILED=0
for result in "${RESULTS[@]}"; do
    case "$result" in FAIL*) FAILED=1 ;; esac
done
exit "$FAILED"
