# market-data-collector

A versioned market-data collection and validation project for public-source financial data, with an emphasis on source semantics, evidence lineage, and fail-closed data quality.

中文：这是一个面向公开金融数据源的版本化采集与验证项目。目标不是简单“抓到一个数字”，而是尽可能明确保存该数字的来源、日期、字段语义、派生关系和质量状态，使数据能够被研究、模型与自动化流程复用。

> **Important status note / 重要状态说明**
>
> The China Financial module's **data collection and measurement layer is READY against the current internal China Financial Draft measurement scope**. READY is deliberately narrower than a repository-wide PRODUCTION declaration.
>
> China Financial 的**数据采集与测量层已经按当前内部 China Financial Draft 的测量范围进入 READY**。这里的 READY 有严格边界：它不等于下游 Permission Engine、交易模型、调度/运行时 Store SLA 或整个仓库已经进入 Production。
>
> The repository still contains historical collector versions, probes, regression tests and metadata-migration work. Those artifacts are retained intentionally for audit and rollback and must not be confused with the active READY data path.
>
> 仓库仍会保留旧 Collector、Probe、回归测试及尚未完全清理的元数据迁移工作。这些内容是为审计、诊断和版本回滚而保留，不能与当前 READY 数据路径混为一谈。

## Project goals

- Collect public financial data from authoritative or explicitly identified public sources.
- Keep **source semantics explicit** instead of silently substituting similar-looking series.
- Preserve source lineage, request metadata, timestamps, parser versions, and hashes when redistribution permits.
- Normalize observations into machine-readable interfaces.
- Keep deterministic derived series separate from root observations.
- Record missing, failed, stale, or semantically unsafe collection as explicit gaps. **Unknown is not zero and not neutral.**
- Keep collector implementation upgrades independent from downstream interfaces whenever consumer-visible semantics are unchanged.
- Preserve historical code and interface versions rather than silently replacing old versions.

## What Git is authoritative for

Git is the canonical version history for the collector code, workflows, contracts, registries, tests, and other Git-managed tooling in this project.

The `data/` tree may contain normalized snapshots, regression fixtures, and auditable historical outputs used to test collector behavior. Its presence in Git does **not** by itself mean this repository is the authoritative runtime Store for every downstream deployment. Runtime Store/data-retention policy is deployment-specific and remains separate from tool-code version governance.

中文：Git 是本项目中采集代码、工作流、Contract、Registry、测试及其他 Git 化工具的权威版本历史。`data/` 目录可以保存用于审计与回归的标准化快照/历史输出，但**不能因为数据出现在 Git 中，就自动把本仓库描述为所有部署场景的唯一权威运行时 Store**。

## Architecture

```text
public sources
    ↓
collectors / adapters
    ↓
semantic validation
    ↓
source evidence metadata
    ↓
OBSERVATIONS ──→ DERIVED
    ↓              ↓
GAPS / RUNS / QC / provenance
    ↓
normalized snapshots / runtime Store (deployment-specific)
    ↓
contracts/current.json
    ↓
downstream consumers
```

`contracts/current.json` is the repository-level interface/readiness anchor for the module. Individual collector implementations can evolve without forcing unnecessary consumer-visible interface changes.

## Status terminology

The repository uses several maturity labels. They are intentionally not interchangeable:

- **PROBE** — source discovery, endpoint research, or semantic investigation. A probe is not an active collector.
- **DATA TEST / REGRESSION TEST** — a collector has been tested against known official-source examples or a bounded live window. This validates a specific behavior, not the entire module.
- **CANDIDATE** — an implementation path exists but is not the currently declared READY path.
- **READY** — the explicitly named scope in the current contract has passed its readiness definition. For China Financial today that scope is **DATA_COLLECTION_AND_MEASUREMENT_LAYER**.
- **PRODUCTION** — reserved for a broader runtime/release declaration with the required scheduler, Store, downstream contract and operational SLA closure. China Financial READY must not be silently rewritten as repository-wide PRODUCTION.

A green historical family workflow therefore remains only evidence for that family/version. The current contract and current READY gate define the active readiness claim.

## China Financial module

### Normative baseline

The normative measurement specification for the active data layer is the **current internal China Financial Draft**. The untouched V0.9.1.1 manuscript is historical/reference material and is not used as the current Git readiness specification.

### Current READY scope

`contracts/china_financial/current.json` declares:

`READY / DATA_COLLECTION_AND_MEASUREMENT_LAYER`

The active data layer provides collection/measurement paths for the Draft's current production-core inputs, including:

- PBOC policy operations and low-frequency policy-state checks;
- PBOC monthly monetary-policy-tool releases;
- PBOC monthly RMB credit / TSF balance-sheet data;
- CFETS / ChinaMoney funding, sovereign and credit-market data;
- SSE exchange-repo funding data;
- pure AAA NCD wholesale-funding curves;
- matched-tenor credit-spread proxies with provider-consistency rules;
- Ministry of Finance fiscal-flow releases;
- NAFMII monthly debt-financing-instrument gross issuance as a **PrimaryMarketActivityProxy**, not proof of financing access;
- simplified central- and local-government bond cash-clock context for fiscal drain/release and duration-supply interpretation.

These capabilities do **not** mean that every public-source series, every deferred research idea, or every historical date has been backfilled.

### Daily incremental operating policy

The default operating mode is **DAILY_INCREMENTAL**.

- High-frequency market series use the latest eligible trading session / recent rolling window.
- Low-frequency policy and macro series are checked for new official releases; if no new release exists, the previously known state carries forward outside the event collector.
- Previously collected historical data is preserved.
- Uncollected 2025/2024-or-older history is **not** backfilled merely to make the repository look historically complete.
- Historical backfill is an explicit maintenance/research operation, not the default daily task.
- A no-event result is valid only when the relevant recent publication window was successfully checked.
- Unknown is never converted to zero or neutral.

### Semantic protections

The module intentionally distinguishes several frequently confused products and definitions:

- actual DR repo transaction rates **must not** be replaced by FR/FDR fixing rates;
- SSE GC actual weighted-average repo rates **must not** be replaced by fixing curves;
- pure NCD AAA curves **must not** be replaced by ordinary commercial-bank bond curves;
- credit spreads preserve matched-tenor and provider-consistent parent semantics unless an explicitly versioned method says otherwise;
- policy-liquidity operations are policy-driver inputs and are not silently presented as realized financial conditions;
- RRR and policy-rate series are low-frequency state changes, not daily fabricated observations;
- historical policy-review articles are QC/context only and cannot create a new policy Root event;
- no-event, no-new-release and source-failure states remain distinct.

### Government-bond cash-clock scope

The active Draft does **not** require a complete security-level sovereign-bond ledger. The READY implementation therefore uses a deliberately simplified cash-clock context.

**Central government:**

- MOF pre-issue notices provide the core auction/issuance schedule, explicit issue-payment deadline where stated, and maturity schedule.
- Post-auction result notices provide actual issuance amount/coupon corrections when available.
- A transient failure of an individual old/result detail page is recorded and retried later; it does not erase an otherwise proven recent core schedule window.
- Distribution end is never silently substituted for an issue-payment date.

**Local government:**

- the official local-government bond disclosure platform is checked over a recent rolling publication window;
- auction/payment/maturity/coupon facts are consumed only to the level needed for fiscal-liquidity context;
- no security master or full lifecycle reconciliation is required for READY.

The combined scope is therefore:

`SIMPLIFIED_CENTRAL_PLUS_LOCAL_CONTEXT`

It supports fiscal **Drain / Release / Duration Supply Context**. It must not be marketed as a complete central+local sovereign security master or exhaustive bond-event ledger.

## China Financial READY gate

`.github/workflows/china-financial-daily.yml` is the active **China Financial Daily Data-Layer READY Gate**.

The gate exercises the principal Draft measurement paths together:

1. core fast-market funding / sovereign / credit series;
2. PBC monthly credit and TSF structure;
3. MOF fiscal flows;
4. monthly policy tools;
5. NAFMII primary-market-activity proxy;
6. incremental OMO / RRR policy checks;
7. simplified local-government cash-clock context;
8. simplified central-government cash-clock context;
9. final Draft data-layer readiness semantics.

A successful run supports the statement:

> **China Financial data collection and measurement layer is READY for the current internal Draft measurement scope.**

It does **not** support the broader statement that the downstream trading/Permission system or every deployment runtime is production-stable.

## Repository layout

```text
contracts/                    Machine-readable interfaces and readiness/release state
collectors/                   Collection and parsing code
registry/                     Series/source/method metadata
methods/                      Deterministic derivation logic where applicable
schemas/                      JSON schemas and validation definitions where applicable
data/                         Normalized snapshots / regression history where retained
evidence/                     Redistributable evidence metadata / checksums where retained
tests/                        Semantic, parser, readiness and regression tests
.github/workflows/            READY gates, candidate tests, regressions and probes
```

Historical versions remain in place for rollback and diagnostics; presence of an older V1/V2/V3 file does not mean it is the active READY implementation.

## Data layers

Where a module uses the full normalized data model, the intended layers are:

- `raw`: source evidence metadata and, only when appropriate for redistribution, raw payload references or payloads;
- `observations`: normalized root observations;
- `derived`: deterministic features derived from explicit parent observations;
- `gaps`: failed, missing, stale, or semantically unsafe collection attempts;
- `runs`: execution manifests and completeness/QC states.

## Versioning

Collector-code versions and data-interface versions are deliberately separated.

Implementation-only fixes such as endpoint changes, parser repairs, retry handling, incremental-state handling, or equivalent fallback routing may update collector code without changing the public data interface when output semantics remain compatible.

A new interface version is required when series identifiers, field meanings, units, timing semantics, derivation rules, or other consumer-visible behavior changes.

Historical versions are preserved. New versions do not silently overwrite old contracts or erase prior collector versions used for regression and rollback.

## Public-data policy

This project is designed around public-source factual market data. Availability of a public webpage or endpoint does not automatically grant unrestricted redistribution rights for full raw payloads. The project therefore prioritizes normalized factual observations plus provenance, request metadata, and hashes; raw-payload retention is source-specific.

## Current release/readiness status

**China Financial: READY — DATA_COLLECTION_AND_MEASUREMENT_LAYER.**

This READY claim is bounded by the current internal China Financial Draft and the DAILY_INCREMENTAL operating policy. Metadata cleanup, historical probes, legacy regressions, runtime Store implementation, scheduling/SLA, downstream aggregation and Permission-engine work are separate concerns and do not change the meaning of the data-layer READY declaration.

The current contract and machine-readable readiness output, rather than a README marketing sentence or a single family-level green Action, remain the source of truth for the exact scope of READY.
