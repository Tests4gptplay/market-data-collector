#!/usr/bin/env python3
"""Validation for China Financial Git-backed Store persistence V2."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-root", default="data/china_financial")
    args = ap.parse_args()
    root = Path(args.store_root)
    latest_path = root / "manifests" / "latest.json"
    assert latest_path.exists(), latest_path
    latest = load(latest_path)

    assert latest["schema_version"] == "CF_STORE_MANIFEST_V2", latest
    assert latest["pipeline_version"] == "CF_STORE_PERSISTENCE_V2", latest
    assert latest["fast_market_coverage"] == "27/27", latest
    assert latest.get("latest_valid_market_session"), latest

    contract = latest.get("consumer_contract") or {}
    assert contract.get("entrypoint") == "data/china_financial/manifests/latest.json", contract
    assert contract.get("standard_runtime_must_not_invoke_collectors") is True, contract
    assert contract.get("standard_runtime_must_not_scrape_sources") is True, contract
    assert contract.get("consumer_should_not_glob_all_historical_store_files") is True, contract
    assert contract.get("consumer_reads_only_manifest_selected_active_paths") is True, contract
    assert contract.get("collector_schedule_is_independent_of_model_invocation") is True, contract

    required = {
        "fast_market",
        "pbc_monthly_credit_v1",
        "mof_fiscal_ytd_v2",
        "pbc_monthly_policy_tools_v2",
        "nafmii_dfi_issuance_v1",
        "rrr_event_v5_incremental",
        "policy_event_v6_incremental",
        "local_gov_cash_clock_v3",
        "central_gov_cash_clock_v5",
    }
    statuses = latest.get("family_status") or {}
    paths = latest.get("active_data_paths") or {}
    assert required <= set(statuses), required - set(statuses)
    assert required <= set(paths), required - set(paths)
    for name in required:
        assert statuses[name]["status"] == "PASS", (name, statuses[name])
        entry = paths[name]
        assert isinstance(entry, dict) and entry, (name, entry)
        for kind, p in entry.items():
            if kind == "registry":
                continue
            path = Path(p)
            assert path.exists(), (name, kind, p)

    registry = paths.get("registry") or {}
    for p in registry.values():
        assert Path(p).exists(), p

    central = (latest.get("fiscal_context") or {}).get("central_government_bond") or {}
    assert central.get("ready_adapter_semantics", {}).get("source_outage_becomes_unknown_not_zero") is True, central
    local = (latest.get("fiscal_context") or {}).get("local_government_bond") or {}
    assert local.get("window_proof", {}).get("all_publication_days_checked") is True, local
    assert local.get("window_proof", {}).get("unknown_is_never_zero") is True, local

    layers = latest.get("store_layers") or {}
    for layer in ("observations", "derived", "gaps", "contexts", "diagnostics", "runs", "manifests"):
        assert layer in layers, (layer, layers)

    print(json.dumps({
        "status": "PASS",
        "schema_version": latest["schema_version"],
        "pipeline_version": latest["pipeline_version"],
        "store_manifest": str(latest_path),
        "as_of": latest.get("as_of"),
        "latest_valid_market_session": latest.get("latest_valid_market_session"),
        "family_count": len(required),
        "consumer_entrypoint": contract.get("entrypoint"),
        "model_reads_store_not_collectors": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
