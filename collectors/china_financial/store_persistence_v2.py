#!/usr/bin/env python3
"""China Financial Git-backed Store persistence V2.

V1 is preserved. V2 keeps V1's fail-closed merge/dedup rules and tightens the
consumer boundary so a model can use the Store without scanning historical
seed files or invoking collectors.

V2 changes
----------
1. The model-facing manifest contains explicit active data paths for every
   current family. Consumers must start from manifests/latest.json rather than
   globbing the whole data tree.
2. Simplified local-government cash-clock acceptance follows the V3 wrapper's
   final window-proof/context contract. Lower-level V1 diagnostics are retained
   as diagnostics when the V3 publication window is proven; they are not
   promoted back into blocking gaps after V3 has classified the context PASS.
3. Local/central government-bond context and diagnostics are persisted in
   versioned daily context/diagnostic snapshots for audit.
4. The latest manifest declares a no-synchronous-collection consumer contract.

No existing V1 Store file or collector version is deleted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import store_persistence_v1 as v1

PIPELINE_VERSION = "CF_STORE_PERSISTENCE_V2"
SCHEMA_VERSION = "CF_STORE_MANIFEST_V2"


def _stage_diagnostics(stage: Path, central: bool) -> list[Any]:
    if central:
        p = stage / "diagnostics.json"
        return v1.load_json(p, []) if p.exists() else []
    p = stage / "gaps.json"
    return v1.load_json(p, []) if p.exists() else []


def persist_government_context_v2(
    store: Path,
    year: str,
    family_name: str,
    stage: Path,
    *,
    central: bool,
) -> dict[str, Any]:
    run = v1.load_json(stage / "run.json")
    v1.require_pass(run, family_name)
    context = v1.load_json(stage / "context.json")

    if central:
        semantics = context.get("ready_adapter_semantics") or {}
        if semantics.get("source_outage_becomes_unknown_not_zero") is not True:
            raise v1.StoreConflict(f"{family_name}: missing central UNKNOWN-not-zero READY semantic")
    else:
        proof = context.get("window_proof") or {}
        if proof.get("all_publication_days_checked") is not True:
            raise v1.StoreConflict(f"{family_name}: local publication window is not fully proven")
        if proof.get("unknown_is_never_zero") is not True:
            raise v1.StoreConflict(f"{family_name}: local UNKNOWN-not-zero semantic missing")

    obs = v1.load_json(stage / "observations.json")
    obs_path = store / "observations" / year / f"{year}-{family_name}.json"
    merged = v1.merge_rows(obs_path, obs, event_mode=True)

    as_of = str(run.get("as_of") or context.get("as_of") or "")
    if not as_of:
        raise v1.StoreConflict(f"{family_name}: as_of missing")

    context_path = store / "contexts" / year / f"{as_of}-{family_name}.json"
    v1.write_json(context_path, context)

    diagnostics = _stage_diagnostics(stage, central)
    diagnostics_path = store / "diagnostics" / year / f"{as_of}-{family_name}.json"
    v1.write_json(diagnostics_path, diagnostics)

    run_path = store / "runs" / year / f"{as_of}-{family_name}.json"
    persisted_run = dict(run)
    persisted_run["persistence_pipeline_version"] = PIPELINE_VERSION
    persisted_run["diagnostic_count_persisted"] = len(diagnostics)
    persisted_run["diagnostics_are_blocking"] = False if central else False
    persisted_run["context_path"] = str(context_path)
    persisted_run["diagnostics_path"] = str(diagnostics_path)
    v1.write_json(run_path, persisted_run)

    gap_path = store / "gaps" / year / f"{year}-{family_name}.json"
    # Final wrapper contract is the blocking-gap contract. For these simplified
    # context adapters, accepted PASS means no blocking gap is persisted.
    if not gap_path.exists():
        v1.write_json(gap_path, [])

    return {
        "status": "PASS",
        "collector_version": run.get("collector_version"),
        "new_store_rows": merged["added"],
        "merge": merged,
        "observations_path": str(obs_path),
        "context_path": str(context_path),
        "diagnostics_path": str(diagnostics_path),
        "run_path": str(run_path),
        "diagnostic_count": len(diagnostics),
        "context": context,
        "run": run,
    }


def _arg_value(flag: str, default: str | None = None) -> str | None:
    try:
        i = sys.argv.index(flag)
    except ValueError:
        return default
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else default


def _active_paths(store: Path, latest: dict[str, Any]) -> dict[str, Any]:
    family_status = latest.get("family_status") or {}
    as_of = str(latest.get("as_of") or "")
    market_date = str(latest.get("latest_valid_market_session") or "")
    year = as_of[:4]
    market_year = market_date[:4]

    result: dict[str, Any] = {
        "registry": {
            "series": "registry/china_financial/series.json",
            "sources": "registry/china_financial/sources.json",
            "methods": "registry/china_financial/methods.json",
        },
        "fast_market": {
            "observations": f"data/china_financial/observations/{market_year}/{market_date}-market-family-v4.json",
            "derived": f"data/china_financial/derived/{market_year}/{market_date}-market-family-v4.json",
            "run": f"data/china_financial/runs/{market_year}/{market_date}-market-family-v4.json",
        },
    }

    snapshot_names = {
        "pbc_monthly_credit_v1": ("pbc-monthly-credit-v1", True),
        "mof_fiscal_ytd_v2": ("mof-fiscal-ytd-v2", True),
        "pbc_monthly_policy_tools_v2": ("pbc-monthly-policy-tools-v2", False),
        "nafmii_dfi_issuance_v1": ("nafmii-dfi-issuance-v1", False),
        "rrr_event_v5_incremental": ("rrr-event-v5-incremental", False),
        "policy_event_v6_incremental": ("policy-event-v6-incremental", False),
    }
    for key, (stem, has_derived) in snapshot_names.items():
        entry = {
            "observations": f"data/china_financial/observations/{year}/{year}-{stem}.json",
            "run": f"data/china_financial/runs/{year}/{year}-{stem}.json",
        }
        if has_derived:
            entry["derived"] = f"data/china_financial/derived/{year}/{year}-{stem}.json"
        result[key] = entry

    for key, stem in {
        "local_gov_cash_clock_v3": "local-gov-cash-clock-v3",
        "central_gov_cash_clock_v5": "central-gov-cash-clock-v5",
    }.items():
        result[key] = {
            "observations": f"data/china_financial/observations/{year}/{year}-{stem}.json",
            "context": f"data/china_financial/contexts/{year}/{as_of}-{stem}.json",
            "diagnostics": f"data/china_financial/diagnostics/{year}/{as_of}-{stem}.json",
            "run": f"data/china_financial/runs/{year}/{as_of}-{stem}.json",
        }

    missing = sorted(k for k in family_status if k not in result and k != "fast_market")
    if missing:
        raise v1.StoreConflict(f"manifest active path mapping missing families: {missing}")
    return result


def _enrich_manifest(store: Path, as_of: str, year: str) -> None:
    latest_path = store / "manifests" / "latest.json"
    latest = v1.load_json(latest_path)
    latest["schema_version"] = SCHEMA_VERSION
    latest["pipeline_version"] = PIPELINE_VERSION
    latest["active_data_paths"] = _active_paths(store, latest)
    latest["consumer_contract"] = {
        "entrypoint": "data/china_financial/manifests/latest.json",
        "standard_runtime_must_not_invoke_collectors": True,
        "standard_runtime_must_not_scrape_sources": True,
        "consumer_should_not_glob_all_historical_store_files": True,
        "consumer_reads_only_manifest_selected_active_paths": True,
        "collector_schedule_is_independent_of_model_invocation": True,
        "store_insufficient_or_stale_must_fail_closed_or_downweight_per_draft": True,
    }
    latest["store_layers"] = {
        "observations": "normalized root/event observations",
        "derived": "deterministic derived observations",
        "gaps": "blocking data gaps only",
        "contexts": "accepted supporting context snapshots",
        "diagnostics": "non-blocking/source-health diagnostics",
        "runs": "collector/persistence execution manifests",
        "manifests": "model-facing Store pointers and daily Store state",
    }
    v1.write_json(latest_path, latest)
    daily = store / "manifests" / year / f"{as_of}-daily-persist-v1.json"
    if daily.exists():
        v1.write_json(daily, latest)


def main() -> int:
    store_root = _arg_value("--store-root", "data/china_financial")
    as_of = _arg_value("--as-of")
    year = _arg_value("--year")
    if not as_of or not year or not store_root:
        raise SystemExit("V2 requires --as-of, --year and --store-root")

    # Patch only the government-context persistence boundary; all other V1
    # merge/dedup/conflict rules are reused unchanged.
    v1.PIPELINE_VERSION = PIPELINE_VERSION
    v1.SCHEMA_VERSION = SCHEMA_VERSION
    v1.persist_government_context = persist_government_context_v2
    rc = v1.main()
    if rc != 0:
        return rc
    _enrich_manifest(Path(store_root), as_of, year)

    latest = v1.load_json(Path(store_root) / "manifests" / "latest.json")
    result = {
        "status": "PASS",
        "pipeline_version": PIPELINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "latest_valid_market_session": latest.get("latest_valid_market_session"),
        "consumer_entrypoint": latest["consumer_contract"]["entrypoint"],
        "active_family_path_count": len(latest.get("active_data_paths") or {}),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
