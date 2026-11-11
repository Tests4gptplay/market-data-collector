# China Financial workflow archive

The current persistence workflow (manual triggering available; its automatic schedule was paused on 2026-10-28) is:

- `.github/workflows/china-financial-daily-persist-v3.yml`

Older persistence workflow implementations are intentionally retained for rollback/audit in Git history and on the development branch `agent/china-financial-data-completeness-v1`:

- `china-financial-daily-persist-v1.yml`
- `china-financial-daily-persist-v2.yml`

They are intentionally absent from the current `.github/workflows` directory. The V3 automatic daily cron remains paused.

Collector, persistence and test source versions used by those workflows remain versioned in the repository unless separately superseded; this archive note does not authorize deletion of historical implementations.
