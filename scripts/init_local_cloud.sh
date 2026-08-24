#!/usr/bin/env bash
set -euo pipefail

export CLOUDSDK_CORE_PROJECT="${CLOUDSDK_CORE_PROJECT:-local-project}"

until curl -fsS http://fake-gcs:4443/storage/v1/b >/dev/null; do sleep 1; done
curl -fsS -X POST 'http://fake-gcs:4443/storage/v1/b?project=local-project' \
  -H 'Content-Type: application/json' \
  -d '{"name":"knowledgeforge"}' || true

pubsub_base="http://${PUBSUB_EMULATOR_HOST}"
project_path="projects/${CLOUDSDK_CORE_PROJECT}"
until curl -fsS "${pubsub_base}/v1/${project_path}/topics" >/dev/null; do sleep 1; done
curl -fsS -X PUT "${pubsub_base}/v1/${project_path}/topics/knowledgeforge-ingestion" || true
curl -fsS -X PUT "${pubsub_base}/v1/${project_path}/topics/knowledgeforge-ingestion-dead-letter" || true
curl -fsS -X PUT "${pubsub_base}/v1/${project_path}/subscriptions/knowledgeforge-ingestion-worker" \
  -H 'Content-Type: application/json' \
  -d '{"topic":"projects/local-project/topics/knowledgeforge-ingestion","deadLetterPolicy":{"deadLetterTopic":"projects/local-project/topics/knowledgeforge-ingestion-dead-letter","maxDeliveryAttempts":5}}' || true
curl -fsS -X PUT "${pubsub_base}/v1/${project_path}/subscriptions/knowledgeforge-ingestion-dead-letter-worker" \
  -H 'Content-Type: application/json' \
  -d '{"topic":"projects/local-project/topics/knowledgeforge-ingestion-dead-letter"}' || true
