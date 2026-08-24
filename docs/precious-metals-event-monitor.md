# Precious-metals event monitor (one-time, 2026-08-28)

The workflow `.github/workflows/precious-metals-event-monitor.yml` is scheduled for **2026-08-24 22:58 Asia/Tokyo** (13:58 UTC) and is also manually runnable. GitHub Actions schedules are not real-time and may queue.

During an approximately five-minute run:

- the official Federal Reserve speeches feed is the primary source and is checked about every 2 seconds;\n- market quotes are auxiliary and requested every 5 seconds, subject to source limits;
- other Federal Reserve, U.S. Treasury, and White House feeds are auxiliary and checked every 20 seconds;
- the official FRED DFII10 10-year real-yield observation is refreshed once per minute (daily frequency);
- every snapshot and the targeted speech event (emitted as `TARGET_EVENT`) is printed immediately as compact JSON;
- `snapshots.jsonl`, `events.jsonl`, and `summary.json` are uploaded as a seven-day workflow artifact.

## Fast interpretation

Use `summary.json.signal` for triage, then read any matching event and the last snapshots before acting.

- **偏多贵金属**: gold/silver rise while DXY and nominal yields fall, with at least three confirming votes.
- **偏空贵金属**: gold/silver fall while DXY and nominal yields rise, with at least three confirming votes.
- **暂不交易**: mixed, weak, stale, or incomplete cross-asset confirmation.

The classifier intentionally does not infer policy meaning from headlines. Headline interpretation should be checked against the cross-asset response. Quotes can be delayed; gold/silver fall back to futures proxies if spot symbols are unavailable. This is monitoring infrastructure, not financial advice or an order-execution system.
