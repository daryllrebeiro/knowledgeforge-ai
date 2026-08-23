# Migration policy

Migrations `001` through `006` are forward-only at this stage. They are applied in
numeric order by `scripts/apply_migrations.py` against a fresh database in CI.

The current migration set does not include destructive down-migrations because several
steps transform existing data or indexes. Recovery is therefore handled by restoring a
database backup and applying forward repair SQL under review. Before production use,
each migration that needs rollback support must either receive a tested down-migration
or have a specific forward-repair procedure recorded here.

Phase 10 CI proves fresh-database application. Rollback/recovery drills are scheduled
for Phase 13.
