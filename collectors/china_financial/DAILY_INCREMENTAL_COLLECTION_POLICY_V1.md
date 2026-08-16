# China Financial Daily Incremental Collection Policy V1

Status: ACTIVE DATA-ACQUISITION RULE
Scope: China Financial collectors only
Purpose: define forward-rolling daily collection behavior after the initial build/bootstrap phase.

## 1. Core rule

China Financial is now a rolling incremental daily-record system.

The initial/bootstrap collection that has already been executed is accepted and preserved as historical data. Historical data that was not collected during that bootstrap phase MUST NOT be newly backfilled merely to make older years look complete.

In particular:

- Do not newly populate missing 2025, 2024, or older observations just for completeness.
- Do not run routine daily jobs by replaying old years.
- Do not regenerate old daily/monthly observations on every execution.
- Do not treat absence of never-collected pre-incremental history as a current-day GAP.
- Existing historical observations that were already collected remain valid historical records and must not be deleted.

## 2. Daily incremental mode

Default production behavior is `DAILY_INCREMENTAL`.

Each execution should concentrate on information newly available since the latest successful collection state.

### High-frequency families

Examples include:

- DR / R / GC repo rates
- NCD curves
- CGB curves
- credit yields and spreads
- daily PBOC market-operation facts
- government-bond event/cash-clock notices when newly published

Rules:

1. Collect the latest required observation(s) and only a short recent rolling window when needed for continuity, retries, holiday handling, or source-lag detection.
2. Do not scan 2025/2024 history during normal daily execution.
3. A holiday/non-trading day must not generate a false data GAP simply because no new market observation exists.
4. Retry recent source failures within a bounded window; do not solve a current transient failure by performing a multi-year historical crawl.

## 3. Low-frequency families

Examples include:

- broad RRR state
- 7-day reverse-repo policy-rate state
- monthly credit / TSF structure
- monthly policy-tool flows
- fiscal YTD releases
- NAFMII DFI monthly issuance

Rules:

1. Check the official current/recent publication channel on every applicable daily execution.
2. If no new official release or state change exists, carry forward the previously established state; no new historical observation is required.
3. When a new official release appears, append the new observation/state change from that point forward.
4. Do not backfill missing older months/years that were never collected during bootstrap.

## 4. Historical state anchor exception

A low-frequency current state may depend on an official event whose announcement date is older than the incremental system start date.

Example: the currently effective broad RRR setting may have last changed in 2025 even though the rolling collector is operating in 2026.

In that case the collector MAY read the minimum older official source needed to establish the current state anchor, subject to all of the following:

- the older source is used only to establish the current as-of state;
- it does not trigger reconstruction of the intervening historical time series;
- it does not cause routine collection of unrelated 2025/2024 data;
- the original official announcement/effective date is preserved in provenance;
- future information must never leak into a historical as-of query.

This exception is `STATE_ANCHOR_LOOKUP`, not `HISTORICAL_BACKFILL`.

## 5. Backfill policy

Historical backfill is OFF by default.

A backfill may occur only when explicitly requested for one of these reasons:

- a dedicated research/backtest request;
- repair of a known data corruption affecting an already-collected period;
- migration/reconstruction specifically authorized for a named historical range.

A normal daily run must never automatically escalate into historical backfill.

## 6. GAP semantics

A GAP means data that should have been available for the current incremental collection scope could not be established safely.

The following are NOT GAPs:

- an older year/month that was never collected before the incremental regime began;
- a low-frequency series with no new release today;
- a policy state that is unchanged and correctly carried forward;
- a market holiday or legitimate no-event day;
- missing historical observations outside the active rolling window.

## 7. Version/history rule

This policy does not delete or rewrite existing collector versions or already-collected historical data.

Future changes to this policy must create a new version and preserve this V1 for rollback/audit.
