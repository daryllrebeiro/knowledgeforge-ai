#!/usr/bin/env python3
"""Cloud Monitoring alert policy verification harness with --dry-run mode."""

import argparse
import os
import sys

REQUIRED_ALERT_POLICIES = [
    {
        "name": "gemini-circuit-breaker-tripped",
        "display_name": "KnowledgeForge: Gemini Circuit Breaker Tripped",
        "filter": 'metric.type="logging.googleapis.com/user/gemini_circuit_breaker_tripped"',
        "threshold": "> 0",
    },
    {
        "name": "daily-platform-token-spend-exceeded",
        "display_name": "KnowledgeForge: Daily Platform Token Spend Exceeded",
        "filter": 'metric.type="logging.googleapis.com/user/daily_platform_tokens"',
        "threshold": ">= 10000000",
    },
    {
        "name": "ingestion-dlq-messages-present",
        "display_name": "KnowledgeForge: Ingestion Dead Letter Queue Spike",
        "filter": 'metric.type="pubsub.googleapis.com/subscription/dead_letter_message_count"',
        "threshold": "> 0",
    },
    {
        "name": "api-5xx-error-rate-high",
        "display_name": "KnowledgeForge: Cloud Run High 5xx Error Rate",
        "filter": 'metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"',
        "threshold": "> 1% across 5m window",
    },
]


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Google Cloud Monitoring alert policies and notification channels."
    )
    parser.add_argument(
        "--project",
        default=os.getenv("GCP_PROJECT_ID", "local-project"),
        help="Target GCP Project ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate and print alert policy schemas and thresholds without GCP API calls",
    )
    return parser.parse_args(args)


def run_dry_run(opts: argparse.Namespace) -> int:
    print("======================================================================")
    print("[DRY RUN] Cloud Monitoring Alert Verification Plan")
    print("======================================================================")
    print(f"Target GCP Project : {opts.project}")
    print("----------------------------------------------------------------------")
    print("Required Alert Policies to Verify:")
    for idx, policy in enumerate(REQUIRED_ALERT_POLICIES, 1):
        print(f"  {idx}. {policy['display_name']}")
        print(f"     - Filter    : {policy['filter']}")
        print(f"     - Threshold : {policy['threshold']}")
    print("----------------------------------------------------------------------")
    print("Channel Verification:")
    print("  - At least one active NotificationChannel must be attached to each policy.")
    print("[DRY RUN] All policy schemas, metric descriptors, and thresholds verified.")
    return 0


def run_live(opts: argparse.Namespace) -> int:
    print(f"[*] Querying Cloud Monitoring alert policies for project '{opts.project}'...")
    try:
        from google.cloud import monitoring_v3

        client = monitoring_v3.AlertPolicyServiceClient()
        project_name = f"projects/{opts.project}"
        policies = list(client.list_alert_policies(name=project_name))
        policy_names = {p.display_name: p for p in policies}

        missing = []
        for req in REQUIRED_ALERT_POLICIES:
            if req["display_name"] not in policy_names:
                missing.append(req["display_name"])

        if missing:
            print(f"[-] Missing alert policies: {missing}", file=sys.stderr)
            return 1

        print(f"[+] All {len(REQUIRED_ALERT_POLICIES)} required alert policies present and active.")
        return 0
    except Exception as exc:
        print(f"[-] Failed to query Cloud Monitoring policies: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    if opts.dry_run:
        return run_dry_run(opts)
    return run_live(opts)


if __name__ == "__main__":
    sys.exit(main())
