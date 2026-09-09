"""Periodic job entrypoint: purge unverified user accounts and expired documents past retention window.

Invoked as a scheduled Cloud Run Job (via Cloud Scheduler) or periodic CLI cron.
Enforces GDPR storage limitation by deleting stale unverified registrations and
tenant-configured document retention lifecycles.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from knowledgeforge.config import get_settings
from knowledgeforge.db import get_connection
from knowledgeforge.security.auth import purge_unverified_accounts

logger = logging.getLogger("knowledgeforge.security.purge_job")


def run_purge(*, max_age_days: int = 30, dry_run: bool = False) -> int:
    """Execute unverified user purge. Returns count of purged users."""
    if max_age_days <= 0:
        raise ValueError("max_age_days must be positive")

    with get_connection() as connection:
        if dry_run:
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT count(*) FROM users
                    WHERE email_verified = false
                      AND created_at < %s
                    """,
                    (cutoff,),
                )
                row = cursor.fetchone()
                count = int(row[0]) if row else 0
            logger.info(
                "purge_job.dry_run eligible_unverified_users=%d cutoff=%s",
                count,
                cutoff.isoformat(),
            )
            return count

        purged_count = purge_unverified_accounts(connection, max_age_days=max_age_days)
        logger.info(
            "purge_job.executed purged_users=%d max_age_days=%d", purged_count, max_age_days
        )
        return purged_count


def purge_expired_documents(connection, *, dry_run: bool = False) -> int:
    """Purge tenant-owned documents that exceed each tenant's retention_days policy.

    Tenants with retention_days = 0 (or NULL) have indefinite retention and are skipped.
    Returns count of purged (or eligible if dry_run=True) documents.
    """
    if dry_run:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM documents d
                JOIN tenants t ON d.tenant_id = t.id
                WHERE t.retention_days > 0
                  AND d.created_at < NOW() - (t.retention_days * INTERVAL '1 day')
                """
            )
            row = cursor.fetchone()
            count = int(row[0]) if row else 0
        logger.info("purge_job.documents.dry_run eligible_documents=%d", count)
        return count

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM documents d
            USING tenants t
            WHERE d.tenant_id = t.id
              AND t.retention_days > 0
              AND d.created_at < NOW() - (t.retention_days * INTERVAL '1 day')
            RETURNING d.id;
            """
        )
        rows = cursor.fetchall()
        connection.commit()
        count = len(rows)
    logger.info("purge_job.documents.executed purged_documents=%d", count)
    return count


def run_document_purge(*, dry_run: bool = False) -> int:
    """Execute document retention purge. Returns count of purged documents."""
    with get_connection() as connection:
        return purge_expired_documents(connection, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge stale unverified accounts and expired documents (GDPR storage limitation)"
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Purge accounts unverified past N days (default: 30)",
    )
    parser.add_argument(
        "--purge-documents",
        action="store_true",
        help="Also purge documents exceeding tenant retention policies",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Count eligible records without deleting"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    try:
        count = run_purge(max_age_days=args.max_age_days, dry_run=args.dry_run)
        action = "would purge" if args.dry_run else "purged"
        print(f"Unverified account purge completed: {action} {count} user(s).")

        if args.purge_documents:
            doc_count = run_document_purge(dry_run=args.dry_run)
            print(f"Document retention purge completed: {action} {doc_count} document(s).")

        return 0
    except Exception as e:
        logger.exception("purge_job.failed error=%s", e)
        print(f"ERROR: unverified account purge failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
