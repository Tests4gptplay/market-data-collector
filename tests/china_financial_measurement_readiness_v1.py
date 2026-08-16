#!/usr/bin/env python3
"""China Financial Draft data-layer readiness audit.

Normative baseline is the current internal China Financial Draft. The untouched
V0.9.1.1 manuscript is historical/reference material only and is not used as
the current readiness specification.

READY in this audit has a deliberately narrow meaning:

    DATA_COLLECTION_AND_MEASUREMENT_LAYER_READY

It means the Draft's current production-core measurement inputs have executable
collection paths and the required normalized roots/context can be measured. It
does NOT declare a downstream trading model, scheduled runtime Store, consumer
Permission engine, or repository-wide production SLA to be production-ready.

The audit therefore keeps data readiness separate from broader metadata/runtime
release closure.
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
                # Historical malformed fixtures are handled by their own tests.
                # This audit never fabricates IDs from unreadable files.
                continue
    return result


def any_of(present: set[str], candidates: set[str]) -> bool:
    return bool(present.intersection(candidates))


def all_of(present: set[str], required: set[str]) -> bool:
    return required.issubset(present)


def file_gate(paths: list[str]) -> dict[str, Any]:
    missing = [p for p in paths if not (ROOT / p).exists()]
    return {
        "status": "PASS" if not missing else "FAIL",
        "required_paths": paths,
        "missing_paths": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()

    present = persisted_series_ids()

    # Draft Minimum Coverage: Funding
    funding = {
        "dr007_present": "FUND_DR007" in present,
        "independent_breadth_present": any_of(
            present,
            {"FUND_R007", "FUND_GC007", "FUND_NCD_AAA_3M", "FUND_NCD_AAA_1Y"},
        ),
    }
    funding["status"] = "PASS" if funding["dr007_present"] and funding["independent_breadth_present"] else "FAIL"

    # Draft Minimum Coverage: Risk Bearing
    risk_pairs = {
        "AAA_1Y": all_of(present, {"CRD_MTN_AAA_1Y", "SOV_CGB_1Y", "CRD_SPREAD_AAA_1Y"}),
        "AAA_3Y": all_of(present, {"CRD_MTN_AAA_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AAA_3Y"}),
    }
    risk_breadth = {
        "AA+_3Y": all_of(present, {"CRD_MTN_AAP_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AAP_3Y"}),
        "AA_3Y": all_of(present, {"CRD_MTN_AA_3Y", "SOV_CGB_3Y", "CRD_SPREAD_AA_3Y"}),
    }
    risk = {
        "matched_tenor_high_grade_pairs": risk_pairs,
        "rating_breadth": risk_breadth,
        "status": "PASS" if any(risk_pairs.values()) else "FAIL",
    }

    # Draft Minimum Coverage: Slow Balance Sheet
    slow_required = {"CC_RMB_LOAN_STOCK", "CC_CORP_LT_LOAN_STOCK"}
    slow_support = {
        "CC_CORP_ST_LOAN_STOCK",
        "CC_HH_LT_LOAN_STOCK",
        "CC_HH_ST_LOAN_STOCK",
        "CC_BILL_FINANCING_STOCK",
        "CC_TSF_ENTRUSTED_LOAN",
        "CC_TSF_TRUST_LOAN",
        "CC_TSF_UNDISCOUNTED_BA",
    }
    slow = {
        "required_roots": sorted(slow_required),
        "required_roots_present": all_of(present, slow_required),
        "aggregate_crosscheck_present": any_of(present, {"CC_TSF_INCREMENT", "CC_TSF_RMB_LOANS"}),
        "supporting_structure_present": sorted(slow_support.intersection(present)),
    }
    slow["status"] = "PASS" if slow["required_roots_present"] and slow["aggregate_crosscheck_present"] else "FAIL"

    # Draft Fiscal core: realized anchor + budget/fund roots + financing context.
    # Detailed security-level lifecycle accounting is not required for READY.
    fiscal_required = {
        "FISC_GENERAL_REVENUE",
        "FISC_GENERAL_EXPENDITURE",
        "FISC_GOV_FUND_REVENUE",
        "FISC_GOV_FUND_EXPENDITURE",
        "FISC_DEPOSIT_STOCK",
        "FISC_GOV_BOND_NET_FINANCING_TSF",
    }
    fiscal = {
        "required_roots": sorted(fiscal_required),
        "status": "PASS" if all_of(present, fiscal_required) else "FAIL",
        "missing": sorted(fiscal_required - present),
    }

    policy_monthly_required = {
        "POL_MLF_NET_MONTHLY",
        "POL_PBOC_CGB_NET_MONTHLY",
        "POL_PSL_NET_MONTHLY",
        "POL_SLF_NET_MONTHLY",
        "POL_STRUCTURAL_TOOLS_NET_MONTHLY",
        "POL_TREASURY_CASH_MGMT_NET",
    }
    policy_monthly = {
        "required_roots": sorted(policy_monthly_required),
        "status": "PASS" if all_of(present, policy_monthly_required) else "FAIL",
        "missing": sorted(policy_monthly_required - present),
    }

    execution_capabilities = {
        "fast_market": file_gate([
            "collectors/china_financial/market_family_v4.py",
            ".github/workflows/china-financial-market-family-v4-candidate.yml",
        ]),
        "pbc_monthly_credit": file_gate([
            "collectors/china_financial/pbc_monthly_credit_family_v1.py",
            ".github/workflows/china-financial-pbc-monthly-credit-v1-candidate.yml",
        ]),
        "mof_fiscal": file_gate([
            "collectors/china_financial/mof_fiscal_ytd_family_v2.py",
            ".github/workflows/china-financial-mof-fiscal-ytd-v2-candidate.yml",
        ]),
        "monthly_policy_tools": file_gate([
            "collectors/china_financial/pbc_monthly_policy_tools_v2.py",
            ".github/workflows/china-financial-pbc-monthly-policy-tools-v2-candidate.yml",
        ]),
        "policy_incremental": file_gate([
            "collectors/china_financial/policy_event_family_v6_incremental.py",
            "collectors/china_financial/rrr_event_family_v5_incremental.py",
            ".github/workflows/china-financial-policy-incremental-smoke-v1.yml",
        ]),
        "nafmii_primary_market_activity": file_gate([
            "collectors/china_financial/nafmii_dfi_issuance_family_v1.py",
            ".github/workflows/china-financial-nafmii-dfi-v1-data-test.yml",
        ]),
        "local_government_cash_clock_context": file_gate([
            "collectors/china_financial/local_gov_bond_cash_clock_v3.py",
            ".github/workflows/china-financial-local-gov-cash-clock-v3-data-test.yml",
        ]),
        "central_government_cash_clock_context": file_gate([
            "collectors/china_financial/central_gov_bond_cash_clock_v3_incremental.py",
            ".github/workflows/china-financial-central-gov-cash-clock-v3-ready-test.yml",
        ]),
        "unified_ready_gate": file_gate([
            ".github/workflows/china-financial-daily.yml",
        ]),
    }
    capabilities_pass = all(x["status"] == "PASS" for x in execution_capabilities.values())

    series_registry = load_json(ROOT / "registry/china_financial/series.json")
    sources_registry = load_json(ROOT / "registry/china_financial/sources.json")
    methods_registry = load_json(ROOT / "registry/china_financial/methods.json")
    contract = load_json(ROOT / "contracts/china_financial/current.json")

    core_measurement_pass = all(
        x["status"] == "PASS"
        for x in (funding, risk, slow, fiscal, policy_monthly)
    )

    contract_ready_scope = (
        contract.get("status") == "READY"
        and contract.get("readiness_scope") == "DATA_COLLECTION_AND_MEASUREMENT_LAYER"
    )
    data_layer_ready = core_measurement_pass and capabilities_pass and contract_ready_scope

    metadata_closure = {
        "series_registry_migration_complete": bool(series_registry.get("migration_complete")),
        "series_registry_implementation_complete": bool(series_registry.get("implementation_complete", False)),
        "sources_registry_migration_complete": bool(sources_registry.get("migration_complete", False)),
        "methods_registry_migration_complete": bool(methods_registry.get("migration_complete", False)),
        "contract_status": contract.get("status"),
        "contract_readiness_scope": contract.get("readiness_scope"),
        "runtime_store_writer_declared": bool(contract.get("runtime", {}).get("scheduled_store_writer", False)),
    }

    report = {
        "audit": "CHINA_FINANCIAL_DRAFT_DATA_LAYER_READINESS_V1",
        "normative_baseline": "CURRENT_CHINA_FINANCIAL_DRAFT",
        "historical_reference_not_normative": "UNMODIFIED_V0.9.1.1_MANUSCRIPT",
        "status": "DATA_LAYER_READY" if data_layer_ready else "DATA_LAYER_NOT_READY",
        "ready_scope": "DATA_COLLECTION_AND_MEASUREMENT_LAYER",
        "production_runtime_status": "NOT_DECLARED",
        "important_scope_boundary": {
            "government_bond_cash_clock": "SIMPLIFIED_CENTRAL_PLUS_LOCAL_CONTEXT",
            "security_master": "NOT_REQUIRED",
            "central_actual_result_updates": "INCREMENTAL_CONFIRMATION_NOT_CORE_SCHEDULE_BLOCKER",
            "interpretation": "Government-bond data is a fiscal-liquidity context clock, not a complete security-level sovereign ledger.",
        },
        "core_measurement_gates": {
            "BroadFundingCondition": funding,
            "BroadRiskBearingCondition": risk,
            "SlowBalanceSheetCondition": slow,
            "FiscalSystemLiquidityCore": fiscal,
            "PolicyLiquidityMonthlyDriver": policy_monthly,
        },
        "execution_capabilities": execution_capabilities,
        "metadata_and_runtime_closure": metadata_closure,
        "persisted_series_count": len(present),
        "rules": {
            "daily_incremental_default": True,
            "no_2025_2024_backfill_by_default": True,
            "low_frequency_no_new_release_is_not_gap": True,
            "unknown_is_not_zero": True,
            "component_pass_is_not_repository_wide_production": True,
            "ready_is_data_layer_scope_only": True,
            "runtime_store_and_downstream_permission_engine_are_separate": True,
        },
    }

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if data_layer_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
