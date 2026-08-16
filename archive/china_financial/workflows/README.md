# China Financial workflow archive

The active production-scheduled persistence workflow is:

- `.github/workflows/china-financial-daily-persist-v3.yml`

Older persistence workflow implementations are intentionally retained for rollback/audit in Git history and on the development branch `agent/china-financial-data-completeness-v1`:

- `china-financial-daily-persist-v1.yml`
- `china-financial-daily-persist-v2.yml`

They are intentionally absent from the active `.github/workflows` set on the release/main path so they cannot run a duplicate daily cron beside V3.

Collector, persistence and test source versions used by those workflows remain versioned in the repository unless separately superseded; this archive note does not authorize deletion of historical implementations.
