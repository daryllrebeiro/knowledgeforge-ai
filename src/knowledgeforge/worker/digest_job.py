"""Periodic job entrypoint: generate and send recurring tenant activity digests (Phase 8 Item 3)."""

import argparse
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import Connection

from knowledgeforge.db import get_connection
from knowledgeforge.security.mailer import send_digest_email

logger = logging.getLogger("knowledgeforge.worker.digest_job")


def get_tenant_activity_stats(
    connection: Connection,
    tenant_id: UUID,
    since: datetime,
) -> dict[str, int]:
    """Calculate actual tenant activity counts since cutoff."""
    with connection.cursor() as cursor:
        # Ingested documents
        cursor.execute(
            """
            SELECT count(*) FROM documents
            WHERE tenant_id = %s AND status = 'ready' AND created_at >= %s
            """,
            (tenant_id, since),
        )
        ingested_count = int(cursor.fetchone()[0])

        # Extractions
        cursor.execute(
            """
            SELECT count(*) FROM document_extractions
            WHERE tenant_id = %s AND extracted_at >= %s
            """,
            (tenant_id, since),
        )
        extracted_count = int(cursor.fetchone()[0])

        # Failed ingestions
        cursor.execute(
            """
            SELECT count(*) FROM failed_ingestions
            WHERE tenant_id = %s AND attempted_at >= %s
            """,
            (tenant_id, since),
        )
        failed_count = int(cursor.fetchone()[0])

        # Total queries (from request_logs if available)
        cursor.execute(
            """
            SELECT count(*) FROM request_logs
            WHERE tenant_id = %s AND timestamp >= %s
            """,
            (tenant_id, since),
        )
        queries_count = int(cursor.fetchone()[0])

    return {
        "ingested_count": ingested_count,
        "extracted_count": extracted_count,
        "failed_count": failed_count,
        "queries_count": queries_count,
    }


def run_digest_job(
    connection: Connection,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Execute scheduled digest delivery for all opted-in tenants."""
    now = datetime.now(UTC)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.tenant_id, s.frequency, s.recipient_emails, s.last_sent_at, t.name
            FROM tenant_digest_settings s
            JOIN tenants t ON t.id = s.tenant_id
            WHERE s.enabled = true
            """
        )
        settings_rows = cursor.fetchall()

    sent_count = 0
    for tenant_id, frequency, recipients, last_sent_at, tenant_name in settings_rows:
        if not recipients:
            continue

        interval = timedelta(days=7) if frequency == "weekly" else timedelta(days=1)
        if not force and last_sent_at is not None:
            if now - last_sent_at < interval:
                continue

        since = (now - interval) if last_sent_at is None else last_sent_at
        stats = get_tenant_activity_stats(connection, tenant_id, since)

        if dry_run:
            logger.info(
                "digest_job.dry_run tenant=%s frequency=%s stats=%s recipients=%s",
                tenant_name,
                frequency,
                stats,
                recipients,
            )
            sent_count += 1
            continue

        # Send to all configured recipients
        for email in recipients:
            send_digest_email(
                email,
                tenant_name=tenant_name,
                period=frequency,
                ingested_count=stats["ingested_count"],
                extracted_count=stats["extracted_count"],
                failed_count=stats["failed_count"],
                queries_count=stats["queries_count"],
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tenant_digest_settings
                SET last_sent_at = %s, updated_at = now()
                WHERE tenant_id = %s
                """,
                (now, tenant_id),
            )
        connection.commit()
        sent_count += 1
        logger.info("digest_job.sent tenant=%s recipients=%d", tenant_name, len(recipients))

    return sent_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Run KnowledgeForge recurring activity digest job")
    parser.add_argument("--dry-run", action="store_true", help="Log eligible digests without sending")
    parser.add_argument("--force", action="store_true", help="Send digests regardless of last_sent_at")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    with get_connection() as connection:
        count = run_digest_job(connection, dry_run=args.dry_run, force=args.force)
        print(f"Digest job completed. Delivered digests: {count}")


if __name__ == "__main__":
    main()
