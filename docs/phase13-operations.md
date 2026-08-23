# Phase 13 local performance and recovery procedure

## Performance run

Start the full local stack, configure a valid `KNOWLEDGEFORGE_TOKEN`, and run:

```powershell
$env:KNOWLEDGEFORGE_URL = "http://localhost:8000"
$env:KNOWLEDGEFORGE_TOKEN = "<local-token>"
locust -f loadtest/locustfile.py --headless -u 10 -r 2 -t 2m --csv=artifacts/locust
```

Record request count, error rate, P50, P95, P99, and the first concurrency level where
the local SLOs degrade. This is a local proxy, not a GCP capacity claim.

## Backup and restore

Create a second local PostgreSQL database and run:

```powershell
$env:DATABASE_URL = "postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge"
$env:RESTORE_DATABASE_URL = "postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge_restore"
python scripts/backup_restore_check.py
```

The check verifies document count, chunk count, and that at least one restored
embedding is non-null.

## Incident drills

- Gemini outage: configure a failing generator and verify the circuit breaker opens
  after the threshold, then permits a recovery attempt after the cooldown.
- Worker crash: raise from the ingestion callback and verify the delivery is not
  acknowledged, allowing Pub/Sub retry/dead-letter handling.
- Migration recovery: use the forward-only policy in `docs/migrations.md`; restore a
  backup and apply reviewed forward repair SQL rather than assuming destructive rollback.
