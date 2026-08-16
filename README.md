# market-data-collector

A versioned, reproducible market-data collection pipeline focused on public-source financial data, semantic correctness, evidence lineage, and fail-closed data quality.

中文：这是一个面向公开金融数据源的版本化采集项目，目标不是简单“抓到一个数字”，而是保存该数字的来源、日期、字段语义、派生关系和质量状态，使数据可以被研究、模型和自动化任务稳定复用。

## Project goals

- Collect public financial data from authoritative sources.
- Keep **source semantics explicit** instead of silently substituting similar-looking series.
- Preserve raw-source lineage, request metadata, timestamps, parser versions, and hashes when redistribution permits.
- Normalize observations into stable machine-readable contracts.
- Keep deterministic derived series separate from root observations.
- Record missing/failed collection as explicit gaps. **Unknown is not zero and not neutral.**
- Make collector implementation upgrades independent from downstream model interfaces whenever the output contract is unchanged.
- Store normalized historical datasets directly in Git so every revision is auditable and reversible.

## Architecture

```text
public sources
    ↓
collectors / adapters
    ↓
semantic validation
    ↓
RAW evidence metadata
    ↓
OBSERVATIONS ──→ DERIVED
    ↓              ↓
GAPS / RUNS / QC / provenance
    ↓
versioned Git datasets
    ↓
contracts/current.json
    ↓
downstream consumers
```

`contracts/current.json` is the stable repository-level interface anchor. Individual collection modules have their own contracts so implementation changes can be released without forcing downstream consumers to change.

## Initial module: China Financial

The first production-oriented module is `china_financial`.

Current source families being migrated and validated include:

- People's Bank of China (PBOC)
- China Foreign Exchange Trade System / ChinaMoney
- ChinaBond
- Shanghai Stock Exchange (SSE)

The module intentionally distinguishes several frequently confused data products. Examples:

- actual DR repo transaction rates **must not** be replaced by FR/FDR fixing rates;
- SSE GC actual weighted-average repo rates **must not** be replaced by fixing curves;
- pure NCD AAA curves **must not** be replaced by ordinary commercial-bank bond curves;
- credit spreads must preserve provider-consistent parent series unless an explicitly versioned method says otherwise.

## Repository layout

```text
contracts/                    Stable machine-readable interfaces
collectors/                   Collection and parsing code
registry/                     Series/source/method metadata
methods/                      Deterministic derivation logic
schemas/                      JSON schemas and validation definitions
data/                         Versioned normalized datasets
evidence/                     Redistributable evidence metadata / checksums
tests/                        Semantic, parser and regression tests
.github/workflows/            Scheduled and manual GitHub Actions
```

## Data layers

- `raw`: source evidence metadata and, only when appropriate for redistribution, raw payloads.
- `observations`: normalized root observations.
- `derived`: deterministic features derived from explicit parent observations.
- `gaps`: failed, missing, stale, or semantically unsafe collection attempts.
- `runs`: per-run execution manifests and completeness/QC status.

## Versioning

Collector code and data-interface versions are deliberately separated.

Implementation-only fixes such as endpoint changes, parser repairs, retry handling, or equivalent fallback routing may update collector code without changing the public data interface.

A new interface version is required when series identifiers, field meanings, units, timing semantics, derivation rules, or other consumer-visible behavior changes.

Historical versions are preserved. New versions do not silently overwrite old contracts.

## Public-data policy

This project is designed around public-source factual market data. Availability of a public webpage or endpoint does not automatically grant unrestricted redistribution rights for full raw payloads. The project therefore prioritizes normalized factual observations plus provenance, request metadata, and hashes; raw payload retention is source-specific.

## Status

The repository is currently being initialized from previously validated source-adapter smoke tests. The China Financial module is **not yet declared production-stable** until the unified collector, dynamic-date handling, historical regression tests, and end-to-end output checks pass.
