#!/usr/bin/env python3
"""China Financial Draft Data-Layer Readiness V3.

Normative baseline: current internal China Financial Draft.
Historical/reference only: untouched V0.9.1.1 manuscript.

V3 adds session-aware incremental Fast Market readiness to V2. A previously
validated market session may be carried forward across a weekend/non-trading
period without re-fetching the identical session. New-session collection remains
fail-closed.

READY remains scoped only to DATA_COLLECTION_AND_MEASUREMENT_LAYER and does not
declare downstream Permission logic, trading model, runtime Store/SLA, or the
repository as a whole to be Production.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "china_financial"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_series_ids(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        sid = obj.get("series_id")
        if isinstance(sid, str):
            out.add(sid)
        for value in obj.values():
            collect_series_ids(value, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_series_ids(value, out)


def persisted_series_ids() -> set[str]:
    result: set[str] = set()
    for layer in ("observations", "derived"):
        base = DATA / layer
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            try:
                collect_series_ids(load_json(path), result)
            except Exception:
                continue
    return result


def all_of(present: set[str], required: set[str]) -> bool:
    return required.issubset(present)


def any_of(present: set[str], candidates: set[str]) -> bool:
    return bool(present.intersection(candidates))


def file_gate(paths: list[str]) -> dict[str, Any]:
    missing = [p for p in paths if not (ROOT / p).exists()]
    return {"status": "PASS" if not missing else "FAIL", "required_paths": paths, "missing_paths": missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    present = persisted_series_ids()

    funding = {
        "dr007_present": "FUND_DR007" in present,
        "independent_breadth_present": any_of(present, {"FUND_R007", "FUND_GC007", "FUND_NCD_AAA_3M", "FUND_NCD_AAA_1Y"}),
    }
    funding["status"] = "PASS" if funding["dr007_present"] and funding["independent_breadth_present"] else "FAIL"

    risk_pairs = {
        "AAA_1Y": all_of(present, {"CRD_MTN_AAA_1Y", "SOV_CGB_1Y", "CRD_SPREAD_AAA_1Y"}),
        "AAA_3Y": all_of(present, {"CRD_MTN_AAA_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AAA_3Y"}),
    }
    risk_breadth = {
        "AA+_3Y": all_of(present, {"CRD_MTN_AAP_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AAP_3Y"}),
        "AA_3Y": all_of(present, {"CRD_MTN_AA_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AA_3Y"}),
    }
    risk = {"matched_tenor_high_grade_pairs": risk_pairs, "rating_breadth": risk_breadth, "status": "PASS" if any(risk_pairs.values()) else "FAIL"}

    slow_required = {"CC_RMB_LOAN_STOCK", "CC_CORP_LT_LOAN_STOCK"}
    slow_support = {"CC_CORP_ST_LOAN_STOCK", "CC_HH_LT_LOAN_STOCK", "CC_HH_ST_LOAN_STOCK", "CC_BILL_FINANCING_STOCK", "CC_TSF_ENTRUSTED_LOAN", "CC_TSF_TRUST_LOAN", "CC_TSF_UNDISCOUNTED_BA"}
    slow = {
        "required_roots": sorted(slow_required),
        "required_roots_present": all_of(present, slow_required),
        "aggregate_crosscheck_present": any_of(present, {"CC_TSF_INCREMENT", "CC_TSF_RMB_LOANS"}),
        "supporting_structure_present": sorted(slow_support.intersection(present)),
    }
    slow["status"] = "PASS" if slow["required_roots_present"] and slow["aggregate_crosscheck_present"] else "FAIL"

    fiscal_required = {"FISC_GENERAL_REVENUE", "FISC_GENERAL_EXPENDITURE", "FISC_GOV_FUND_REVENUE", "FISC_GOV_FUND_EXPENDITURE", "FISC_DEPOSIT_STOCK", "FISC_GOV_BOND_NET_FINANCING_TSF"}
    fiscal = {
        "required_roots": sorted(fiscal_required),
        "status": "PASS" if all_of(present, fiscal_required) else "FAIL",
        "missing": sorted(fiscal_required - present),
        "realized_net_anchor": "FISC_DEPOSIT_CHANGE_FROM_FISC_DEPOSIT_STOCK",
    }

    policy_monthly_required = {"POL_MLF_NET_MONTHLY", "POL_PBOC_CGB_NET_MONTHLY", "POL_PSL_NET_MONTHLY", "POL_SLF_NET_MONTHLY", "POL_STRUCTURAL_TOOLS_NET_MONTHLY", "POL_TREASURY_CASH_MGMT_NET"}
    policy_monthly = {"required_roots": sorted(policy_monthly_required), "status": "PASS" if all_of(present, policy_monthly_required) else "FAIL", "missing": sorted(policy_monthly_required - present)}

    capabilities = {
        "fast_market_incremental": file_gate([
            "collectors/china_financial/fast_market_ready_incremental_v1.py",
            ".github/workflows/china-financial-fast-market-incremental-v1-ready-test.yml",
        ]),
        "fast_market_underlying": file_gate(["collectors/china_financial/market_family_v4.py"]),
        "monthly_credit": file_gate(["collectors/china_financial/pbc_monthly_credit_family_v1.py"]),
        "mof_fiscal": file_gate(["collectors/china_financial/mof_fiscal_ytd_family_v2.py"]),
        "monthly_policy_tools": file_gate(["collectors/china_financial/pbc_monthly_policy_tools_v2.py"]),
        "policy_incremental": file_gate(["collectors/china_financial/policy_event_family_v6_incremental.py", "collectors/china_financial/rrr_event_family_v5_incremental.py"]),
        "nafmii_primary_market_activity": file_gate(["collectors/china_financial/nafmii_dfi_issuance_family_v1.py"]),
        "local_gov_cash_clock_context": file_gate(["collectors/china_financial/local_gov_bond_cash_clock_v3.py"]),
        "central_gov_cash_clock_ready_context": file_gate(["collectors/china_financial/central_gov_bond_cash_clock_v5_context.py", ".github/workflows/china-financial-central-gov-cash-clock-v5-ready-test.yml"]),
        "unified_ready_gate": file_gate([".github/workflows/china-financial-daily-ready-v3.yml"]),
    }
    capabilities_pass = all(x["status"] == "PASS" for x in capabilities.values())

    contract = load_json(ROOT / "contracts/china_financial/current.json")
    series_registry = load_json(ROOT / "registry/china_financial/series.json")
    sources_registry = load_json(ROOT / "registry/china_financial/sources.json")
    methods_registry = load_json(ROOT / "registry/china_financial/methods.json")

    core_measurement_pass = all(x["status"] == "PASS" for x in (funding, risk, slow, fiscal, policy_monthly))
    contract_ready = contract.get("status") == "READY" and contract.get("readiness_scope") == "DATA_COLLECTION_AND_MEASUREMENT_LAYER"
    data_layer_ready = core_measurement_pass and capabilities_pass and contract_ready

    report = {
        "audit": "CHINA_FINANCIAL_DRAFT_DATA_LAYER_READINESS_V3",
        "normative_baseline": "CURRENT_CHINA_FINANCIAL_DRAFT",
        "historical_reference_not_normative": "UNMODIFIED_V0.9.1.1_MANUSCRIPT",
        "status": "DATA_LAYER_READY" if data_layer_ready else "DATA_LAYER_NOT_READY",
        "ready_scope": "DATA_COLLECTION_AND_MEASUREMENT_LAYER",
        "production_runtime_status": "NOT_DECLARED",
        "core_measurement_gates": {
            "BroadFundingCondition": funding,
            "BroadRiskBearingCondition": risk,
            "SlowBalanceSheetCondition": slow,
            "FiscalSystemLiquidityCore": fiscal,
            "PolicyLiquidityMonthlyDriver": policy_monthly,
        },
        "execution_capabilities": capabilities,
        "fast_market_incremental_semantics": {
            "validated_session_may_be_reused": True,
            "weekend_nontrading_carry_forward_is_not_stale": True,
            "same_session_redundant_refetch_not_required": True,
            "new_session_collection_remains_fail_closed": True,
            "incomplete_persisted_session_never_reused": True,
        },
        "government_bond_context": {
            "scope": "SIMPLIFIED_CENTRAL_PLUS_LOCAL_CONTEXT",
            "security_master_required": False,
            "cash_clock_role": "FISCAL_CONTEXT_MODIFIER",
            "central_source_outage_behavior": "UNKNOWN_PLUS_DATA_CONFIDENCE_DOWNWEIGHT_AND_RETRY",
            "source_outage_never_becomes_zero": True,
            "source_health_warning_does_not_relax_core_financial_condition_gates": True,
        },
        "metadata_and_runtime_closure": {
            "series_registry_migration_complete": bool(series_registry.get("migration_complete")),
            "series_registry_implementation_complete": bool(series_registry.get("implementation_complete", False)),
            "sources_registry_migration_complete": bool(sources_registry.get("migration_complete", False)),
            "methods_registry_migration_complete": bool(methods_registry.get("migration_complete", False)),
            "metadata_migration_is_not_a_data_layer_ready_blocker": True,
            "runtime_store_writer_declared": bool(contract.get("runtime", {}).get("scheduled_store_writer", False)),
            "runtime_production_release": contract.get("production_runtime_release", "NOT_DECLARED"),
        },
        "persisted_series_count": len(present),
        "operating_policy": {
            "default": "DAILY_INCREMENTAL",
            "high_frequency": "LATEST_VALIDATED_ELIGIBLE_SESSION_THEN_NEW_SESSION_ONLY",
            "low_frequency": "CHECK_NEW_RELEASE_AND_CARRY_FORWARD_EXISTING_STATE",
            "historical_backfill_default": False,
            "uncollected_2025_2024_or_older_not_ready_requirement": True,
        },
        "rules": {
            "unknown_is_not_zero": True,
            "low_frequency_no_new_release_is_not_gap": True,
            "component_pass_is_not_repository_wide_production": True,
            "ready_is_data_layer_scope_only": True,
        },
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        p = Path(args.out); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if data_layer_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
