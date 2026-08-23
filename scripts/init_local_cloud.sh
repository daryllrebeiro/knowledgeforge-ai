#!/usr/bin/env bash
set -euo pipefail

until curl -fsS http://fake-gcs:4443/storage/v1/b >/dev/null; do sleep 1; done
curl -fsS -X POST 'http://fake-gcs:4443/storage/v1/b?project=local-project' \
  -H 'Content-Type: application/json' \
  -d '{"name":"knowledgeforge"}' || true

until gcloud pubsub topics list >/dev/null 2>&1; do sleep 1; done
gcloud pubsub topics create knowledgeforge-ingestion || true
gcloud pubsub topics create knowledgeforge-ingestion-dead-letter || true
gcloud pubsub subscriptions create knowledgeforge-ingestion-worker \
  --topic=knowledgeforge-ingestion \
  --dead-letter-topic=knowledgeforge-ingestion-dead-letter \
  --max-delivery-attempts=5 || true
gcloud pubsub subscriptions create knowledgeforge-ingestion-dead-letter-worker \
  --topic=knowledgeforge-ingestion-dead-letter || true
