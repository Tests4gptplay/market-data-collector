# market-data-collector

A versioned market-data collection and validation project for public-source financial data, with an emphasis on source semantics, evidence lineage, and fail-closed data quality.

中文：这是一个面向公开金融数据源的版本化采集与验证项目。目标不是简单“抓到一个数字”，而是尽可能明确保存该数字的来源、日期、字段语义、派生关系和质量状态，使数据能够被研究、模型与自动化流程复用。

> **Important status note / 重要状态说明**
>
> This repository contains a mixture of working collectors, regression-tested candidate collectors, probes, historical normalized snapshots, and still-incomplete release metadata. A successful component test does **not** mean the whole repository or a module is production-stable.
>
> 本仓库同时包含已能工作的采集器、通过数据回归的候选采集器、数据源探针、历史标准化快照，以及仍在补齐的发布元数据。**单个组件测试通过，不等于整个项目或模块已经达到 Production 稳定状态。**

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

The `data/` tree may contain normalized snapshots, regression fixtures, and auditable historical outputs used to test collector behavior. Its presence in Git does **not** by itself mean this repository is the authoritative runtime Store for every downstream deployment. Runtime Store/data-retention policy is deployment-specific and should remain separate from tool-code version governance.

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

`contracts/current.json` is the repository-level interface anchor. Individual modules may have their own contracts so implementation changes can evolve without unnecessarily forcing downstream consumers to change.

## Status terminology

The repository uses several different maturity labels. They are intentionally not interchangeable:

- **PROBE** — source discovery, endpoint research, or semantic investigation. A probe is not a production collector.
- **DATA TEST / REGRESSION TEST** — a collector has been tested against known official-source examples or a bounded live window. This validates a specific behavior, not the entire module.
- **CANDIDATE** — an implementation path exists and may already collect usable data, but release metadata and/or end-to-end gates are not yet frozen as production.
- **PRODUCTION** — should be claimed only when the current contract explicitly declares it and the required end-to-end collection/QC release gate has passed.

A green family-level workflow therefore means only that the tested family passed its own gate.

## Initial module: China Financial

The first production-oriented module is `china_financial`, but its current contract is still **CANDIDATE**, not PRODUCTION.

Current candidate capabilities include public-source collection or validation paths for, among other things:

- People's Bank of China (PBOC) policy operations and monthly monetary/credit releases;
- China Foreign Exchange Trade System / ChinaMoney funding and credit-market series;
- ChinaBond sovereign-yield fallback/validation where explicitly allowed;
- Shanghai Stock Exchange repo data;
- Ministry of Finance fiscal releases;
- NAFMII monthly debt-financing-instrument gross issuance;
- local-government bond issue/payment/maturity/coupon cash-clock facts from the official local-government disclosure platform.

These capabilities do **not** imply that every public-source family has identical maturity or coverage.

The module intentionally distinguishes several frequently confused products. Examples:

- actual DR repo transaction rates **must not** be replaced by FR/FDR fixing rates;
- SSE GC actual weighted-average repo rates **must not** be replaced by fixing curves;
- pure NCD AAA curves **must not** be replaced by ordinary commercial-bank bond curves;
- credit spreads must preserve matched-tenor and provider-consistent parent semantics unless an explicitly versioned method says otherwise;
- policy-liquidity operations are policy-driver inputs and must not be silently presented as realized financial conditions;
- a no-event result is valid only when the relevant source window was actually checked; otherwise the state remains unknown.

### China Financial integration gate

`.github/workflows/china-financial-daily.yml` is currently a **candidate integration/readiness gate**. It exists to exercise the main collector families together and distinguish measurement readiness from release readiness.

At the current candidate stage, a successful run can support a statement such as **“the tested model inputs are measurable in the tested window”**. It must not be rewritten as **“China Financial is production-stable”** unless the contract release state is separately promoted after its end-to-end release requirements are met.

### Government-bond cash-clock scope

The simplified rolling cash-clock candidate currently covers **local-government bonds** through the official local-government disclosure platform. It explicitly reports:

`LOCAL_GOVERNMENT_BONDS_ONLY`

It must **not** be described as a complete central-government + local-government sovereign event ledger. Aggregate government-bond financing and fiscal data may be available elsewhere in the module, but central-government security-level auction/payment/maturity/coupon event coverage is a separate scope that should be claimed only after it has its own validated production path.

## Repository layout

```text
contracts/                    Machine-readable interfaces and release state
collectors/                   Collection and parsing code
registry/                     Series/source/method metadata
methods/                      Deterministic derivation logic where applicable
schemas/                      JSON schemas and validation definitions where applicable
data/                         Normalized snapshots / regression history where retained
evidence/                     Redistributable evidence metadata / checksums where retained
tests/                        Semantic, parser and regression tests
.github/workflows/            Candidate, regression, probe and release-gate workflows
```

Not every directory is required to have the same maturity level at every point in development.

## Data layers

Where a module uses the full normalized data model, the intended layers are:

- `raw`: source evidence metadata and, only when appropriate for redistribution, raw payload references or payloads;
- `observations`: normalized root observations;
- `derived`: deterministic features derived from explicit parent observations;
- `gaps`: failed, missing, stale, or semantically unsafe collection attempts;
- `runs`: execution manifests and completeness/QC states.

## Versioning

Collector-code versions and data-interface versions are deliberately separated.

Implementation-only fixes such as endpoint changes, parser repairs, retry handling, or equivalent fallback routing may update collector code without changing the public data interface when output semantics remain compatible.

A new interface version is required when series identifiers, field meanings, units, timing semantics, derivation rules, or other consumer-visible behavior changes.

Historical versions are preserved. New versions do not silently overwrite old contracts or erase prior collector versions used for regression and rollback.

## Public-data policy

This project is designed around public-source factual market data. Availability of a public webpage or endpoint does not automatically grant unrestricted redistribution rights for full raw payloads. The project therefore prioritizes normalized factual observations plus provenance, request metadata, and hashes; raw-payload retention is source-specific.

## Current release status

As of the current candidate branch, China Financial has multiple working and regression-tested collector families and a unified candidate integration gate. However, the module remains **CANDIDATE** under its current contract and should be described as **production-oriented / under production-readiness validation**, not as production-stable.

Remaining release work can include registry/method/source metadata closure, full end-to-end QC, dynamic scheduling/runtime-store integration, and any explicitly required coverage that is still outside a validated production collector. The contract and machine-readable readiness outputs, rather than README wording or a single green Action, are the source of truth for release status.
