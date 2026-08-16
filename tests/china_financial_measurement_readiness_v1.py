#!/usr/bin/env python3
"""China Financial Draft-oriented measurement readiness audit.

Normative baseline for this audit is the current China Financial Draft used by
the project. The unmodified V0.9.1.1 manuscript is historical/reference input
and must not be treated as the current readiness specification.

This is intentionally a measurement-capability gate, not a declaration that the
repository is production-stable. It answers three separate questions:

1. Can the core Funding / RiskBearing / SlowBalanceSheet inputs be measured from
   currently persisted normalized data?
2. Do the collectors needed for policy, fiscal, primary-credit issuance and
   government-bond cash-clock context exist as executable/tested code paths?
3. Is the repository contract itself production-closed?

Those questions must not be collapsed into a single PASS label.
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
                # Individual malformed historical files should be caught by
                # their own regression jobs. This audit reports coverage from
                # readable normalized data and does not silently fabricate IDs.
                continue
    return result


def any_of(present: set[str], candidates: set[str]) -> bool:
    return bool(present.intersection(candidates))


def all_of(present: set[str], required: set[str]) -> bool:
    return required.issubset(present)


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
    risk = {"matched_tenor_pairs": risk_pairs, "status": "PASS" if any(risk_pairs.values()) else "FAIL"}

    slow_required = {"CC_RMB_LOAN_STOCK", "CC_CORP_LT_LOAN_STOCK"}
    slow = {
        "required_roots": sorted(slow_required),
        "required_roots_present": all_of(present, slow_required),
        "aggregate_context_present": any_of(present, {"CC_TSF_INCREMENT", "CC_TSF_RMB_LOANS"}),
    }
    slow["status"] = "PASS" if slow["required_roots_present"] and slow["aggregate_context_present"] else "FAIL"

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
        "market_family_v4": file_gate([
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
        "policy_event_incremental": file_gate([
            "collectors/china_financial/policy_event_family_v6_incremental.py",
            "collectors/china_financial/rrr_event_family_v5_incremental.py",
            ".github/workflows/china-financial-policy-incremental-smoke-v1.yml",
        ]),
        "nafmii_dfi_primary_issuance": file_gate([
            "collectors/china_financial/nafmii_dfi_issuance_family_v1.py",
            ".github/workflows/china-financial-nafmii-dfi-v1-data-test.yml",
        ]),
        "local_gov_bond_cash_clock": file_gate([
            "collectors/china_financial/local_gov_bond_cash_clock_v3.py",
            ".github/workflows/china-financial-local-gov-cash-clock-v3-data-test.yml",
        ]),
        "unified_candidate_gate": file_gate([
            ".github/workflows/china-financial-daily.yml",
        ]),
    }
    capabilities_pass = all(x["status"] == "PASS" for x in execution_capabilities.values())

    series_registry = load_json(ROOT / "registry/china_financial/series.json")
    sources_registry = load_json(ROOT / "registry/china_financial/sources.json")
    methods_registry = load_json(ROOT / "registry/china_financial/methods.json")
    contract = load_json(ROOT / "contracts/china_financial/current.json")

    core_measurement_pass = all(x["status"] == "PASS" for x in (funding, risk, slow, fiscal, policy_monthly))
    model_context_measurable = core_measurement_pass and capabilities_pass

    metadata_closure = {
        "series_registry_migration_complete": bool(series_registry.get("migration_complete")),
        "series_registry_implementation_complete": bool(series_registry.get("implementation_complete", False)),
        "sources_registry_migration_complete": bool(sources_registry.get("migration_complete", False)),
        "methods_registry_migration_complete": bool(methods_registry.get("migration_complete", False)),
        "contract_release_state": contract.get("release", {}).get("state") or contract.get("release_state"),
        "contract_release_gate": contract.get("release", {}).get("gate") or contract.get("release_gate"),
    }
    production_contract_ready = (
        metadata_closure["series_registry_migration_complete"]
        and metadata_closure["series_registry_implementation_complete"]
        and metadata_closure["sources_registry_migration_complete"]
        and metadata_closure["methods_registry_migration_complete"]
        and metadata_closure["contract_release_state"] == "PRODUCTION"
    )

    report = {
        "audit": "CHINA_FINANCIAL_DRAFT_MEASUREMENT_READINESS_V1",
        "normative_baseline": "CURRENT_CHINA_FINANCIAL_DRAFT",
        "historical_reference_not_normative": "UNMODIFIED_V0.9.1.1_MANUSCRIPT",
        "status": "MODEL_CONTEXT_MEASURABLE" if model_context_measurable else "MEASUREMENT_GAPS_REMAIN",
        "production_status": "PRODUCTION_CONTRACT_READY" if production_contract_ready else "PRODUCTION_CONTRACT_NOT_READY",
        "important_scope_boundary": {
            "government_bond_cash_clock": "LOCAL_GOVERNMENT_BONDS_ONLY",
            "central_government_bond_cash_clock": "NOT_YET_INCLUDED_IN_V3",
            "interpretation": "Model fiscal-liquidity context can consume the local-government event clock plus aggregate government-bond financing, but the repository must not describe this as a complete central+local sovereign event ledger.",
        },
        "core_measurement_gates": {
            "BroadFundingCondition": funding,
            "BroadRiskBearingCondition": risk,
            "SlowBalanceSheetCondition": slow,
            "FiscalSystemLiquidityCore": fiscal,
            "PolicyLiquidityMonthlyDriver": policy_monthly,
        },
        "execution_capabilities": execution_capabilities,
        "metadata_and_release_closure": metadata_closure,
        "persisted_series_count": len(present),
        "rules": {
            "unknown_is_not_zero": True,
            "component_pass_is_not_production_release": True,
            "collector_presence_is_not_same_as_live_source_success": True,
            "production_requires_contract_and_end_to_end_qc": True,
        },
    }

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if model_context_measurable else 2


if __name__ == "__main__":
    raise SystemExit(main())
