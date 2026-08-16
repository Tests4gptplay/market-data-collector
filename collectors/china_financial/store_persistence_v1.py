#!/usr/bin/env python3
"""China Financial Git-backed Store persistence V1.

This module is the persistence boundary between staged collector output and the
Git-backed normalized China Financial Store. Collectors remain producers;
downstream models consume persisted Store data and do not synchronously run
collectors.

Safety rules
------------
* persist only PASS/accepted READY outputs;
* identical low-frequency observations are deduplicated without refreshing old
  retrieved_at timestamps;
* a conflicting value for an already-persisted natural key fails closed rather
  than silently rewriting history;
* new-session Fast Market data are persisted once; validated-session reuse does
  not rewrite the same market session;
* no-event low-frequency checks do not fabricate zero observations;
* supporting government-bond source-health warnings are kept in the latest
  manifest and never converted to zero.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PIPELINE_VERSION = "CF_STORE_PERSISTENCE_V1"
SCHEMA_VERSION = "CF_STORE_MANIFEST_V1"


class StoreConflict(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ref_key(row: dict[str, Any]) -> str:
    return str(row.get("reference_date") or row.get("reference_period") or "")


def source_semantic(row: dict[str, Any]) -> str:
    dims = row.get("dimensions")
    if not isinstance(dims, dict):
        dims = {}
    return str(row.get("source_semantic") or dims.get("source_semantic") or "")


def series_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("series_id") or ""), ref_key(row)


def event_key(row: dict[str, Any]) -> tuple[Any, ...]:
    dims = row.get("dimensions")
    if not isinstance(dims, dict):
        dims = {}
    return (
        str(row.get("series_id") or ""),
        ref_key(row),
        source_semantic(row),
        str(row.get("source_url") or ""),
        str(dims.get("bond_code") or dims.get("bond_id") or ""),
        str(dims.get("event_stage") or ""),
        str(dims.get("amount_semantic") or ""),
        str(dims.get("tenor") or dims.get("tenor_days") or ""),
    )


def same_value(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if str(a.get("unit") or "") != str(b.get("unit") or ""):
        return False
    av, bv = a.get("value"), b.get("value")
    if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
        return math.isclose(float(av), float(bv), rel_tol=0.0, abs_tol=1e-10)
    return av == bv


def sort_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda x: (
            str(x.get("series_id") or ""),
            ref_key(x),
            source_semantic(x),
            str(x.get("source_url") or ""),
        ),
    )


def merge_rows(
    path: Path,
    staged_rows: list[dict[str, Any]],
    *,
    event_mode: bool = False,
) -> dict[str, int]:
    existing = load_json(path, []) if path.exists() else []
    if not isinstance(existing, list) or not isinstance(staged_rows, list):
        raise StoreConflict(f"{path}: expected JSON arrays")
    key_fn = event_key if event_mode else series_key
    index: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in existing:
        k = key_fn(row)
        if k in index and not same_value(index[k], row):
            raise StoreConflict(f"{path}: existing duplicate conflict for key={k!r}")
        index[k] = row

    added = 0
    identical = 0
    for row in staged_rows:
        if not row.get("series_id") or not ref_key(row):
            raise StoreConflict(f"{path}: staged observation lacks series_id/reference: {row!r}")
        k = key_fn(row)
        old = index.get(k)
        if old is None:
            index[k] = row
            added += 1
            continue
        if same_value(old, row):
            identical += 1
            continue
        raise StoreConflict(
            f"{path}: conflicting revision for key={k!r}; "
            f"stored={old.get('value')!r} staged={row.get('value')!r}. "
            "V1 persistence is fail-closed; review/version the revision explicitly."
        )

    merged = sort_rows(index.values())
    if added > 0 or not path.exists():
        write_json(path, merged)
    return {"existing": len(existing), "staged": len(staged_rows), "added": added, "identical": identical, "final": len(merged)}


def require_pass(run: dict[str, Any], family: str) -> None:
    if run.get("status") != "PASS":
        raise StoreConflict(f"{family}: upstream status is not PASS: {run.get('status')!r}")


def require_empty_gaps(path: Path, family: str) -> list[Any]:
    gaps = load_json(path, [])
    if gaps:
        raise StoreConflict(f"{family}: staged blocking gaps are non-empty ({len(gaps)})")
    return gaps


def persist_family_snapshot(
    store: Path,
    year: str,
    family_name: str,
    stage: Path,
    *,
    has_derived: bool,
) -> dict[str, Any]:
    run = load_json(stage / "run.json")
    require_pass(run, family_name)
    require_empty_gaps(stage / "gaps.json", family_name)
    result: dict[str, Any] = {"status": "PASS", "collector_version": run.get("collector_version")}
    obs_path = store / "observations" / year / f"{year}-{family_name}.json"
    result["observations"] = merge_rows(obs_path, load_json(stage / "observations.json"), event_mode=False)
    result["observations_path"] = str(obs_path)

    if has_derived:
        derived_path = store / "derived" / year / f"{year}-{family_name}.json"
        result["derived"] = merge_rows(derived_path, load_json(stage / "derived.json"), event_mode=False)
        result["derived_path"] = str(derived_path)

    gap_path = store / "gaps" / year / f"{year}-{family_name}.json"
    if not gap_path.exists():
        write_json(gap_path, [])
    result["gaps_path"] = str(gap_path)

    changed = result["observations"]["added"]
    if has_derived:
        changed += result["derived"]["added"]
    run_path = store / "runs" / year / f"{year}-{family_name}.json"
    if changed > 0 or not run_path.exists():
        persisted_run = dict(run)
        persisted_run["persistence_pipeline_version"] = PIPELINE_VERSION
        persisted_run["persistence_mode"] = "MERGE_DEDUP_NO_SILENT_REVISION"
        persisted_run["github_run_id"] = os.environ.get("GITHUB_RUN_ID")
        persisted_run["github_source_commit"] = os.environ.get("GITHUB_SHA")
        write_json(run_path, persisted_run)
    result["run_path"] = str(run_path)
    result["new_store_rows"] = changed
    return result


def persist_fast(store: Path, stage: Path) -> dict[str, Any]:
    adapter_run = load_json(stage / "run.json")
    require_pass(adapter_run, "fast_market")
    market_date = str(adapter_run.get("market_date") or "")
    if not market_date:
        raise StoreConflict("fast_market: missing market_date")
    year = market_date[:4]
    result: dict[str, Any] = {
        "status": "PASS",
        "mode": adapter_run.get("mode"),
        "market_date": market_date,
        "coverage_count": adapter_run.get("coverage_count"),
        "expected_count": adapter_run.get("expected_count"),
        "missing_series": adapter_run.get("missing_series"),
        "new_store_rows": 0,
    }
    if not (
        adapter_run.get("coverage_count") == adapter_run.get("expected_count") == 27
        and adapter_run.get("missing_series") == []
    ):
        raise StoreConflict(f"fast_market: invalid coverage contract: {adapter_run}")

    if adapter_run.get("mode") == "PERSISTED_VALIDATED_SESSION_REUSE":
        result["persistence_action"] = "NO_REWRITE_REUSE_EXISTING_VALIDATED_SESSION"
        return result
    if adapter_run.get("mode") != "LIVE_NEW_SESSION_COLLECTION":
        raise StoreConflict(f"fast_market: unsupported adapter mode {adapter_run.get('mode')!r}")

    src = stage / "live" / market_date
    upstream = load_json(src / "run.json")
    require_pass(upstream, "market_family_v4")
    require_empty_gaps(src / "gaps.json", "market_family_v4")

    name = f"{market_date}-market-family-v4.json"
    obs_path = store / "observations" / year / name
    derived_path = store / "derived" / year / name
    result["observations"] = merge_rows(obs_path, load_json(src / "observations.json"), event_mode=False)
    result["derived"] = merge_rows(derived_path, load_json(src / "derived.json"), event_mode=False)
    gap_path = store / "gaps" / year / name
    if not gap_path.exists():
        write_json(gap_path, [])
    run_path = store / "runs" / year / name
    if result["observations"]["added"] or result["derived"]["added"] or not run_path.exists():
        persisted_run = dict(upstream)
        persisted_run["persistence_pipeline_version"] = PIPELINE_VERSION
        persisted_run["github_run_id"] = os.environ.get("GITHUB_RUN_ID")
        persisted_run["github_source_commit"] = os.environ.get("GITHUB_SHA")
        write_json(run_path, persisted_run)
    result["new_store_rows"] = result["observations"]["added"] + result["derived"]["added"]
    result["persistence_action"] = "PERSIST_NEW_SESSION_IF_NEEDED"
    result["paths"] = {
        "observations": str(obs_path),
        "derived": str(derived_path),
        "gaps": str(gap_path),
        "run": str(run_path),
    }
    return result


def persist_event_family(store: Path, year: str, family_name: str, stage: Path) -> dict[str, Any]:
    run = load_json(stage / "run.json")
    require_pass(run, family_name)
    require_empty_gaps(stage / "gaps.json", family_name)
    obs = load_json(stage / "observations.json")
    path = store / "observations" / year / f"{year}-{family_name}.json"
    merged = merge_rows(path, obs, event_mode=True)
    run_path = store / "runs" / year / f"{year}-{family_name}.json"
    if merged["added"] > 0 or not run_path.exists():
        persisted_run = dict(run)
        persisted_run["persistence_pipeline_version"] = PIPELINE_VERSION
        persisted_run["github_run_id"] = os.environ.get("GITHUB_RUN_ID")
        persisted_run["github_source_commit"] = os.environ.get("GITHUB_SHA")
        write_json(run_path, persisted_run)
    gap_path = store / "gaps" / year / f"{year}-{family_name}.json"
    if not gap_path.exists():
        write_json(gap_path, [])
    return {
        "status": "PASS",
        "collector_version": run.get("collector_version"),
        "observation_count_this_run": len(obs),
        "new_store_rows": merged["added"],
        "merge": merged,
        "observations_path": str(path),
        "run_path": str(run_path),
    }


def persist_government_context(
    store: Path,
    year: str,
    family_name: str,
    stage: Path,
    *,
    central: bool,
) -> dict[str, Any]:
    run = load_json(stage / "run.json")
    require_pass(run, family_name)
    if not central:
        require_empty_gaps(stage / "gaps.json", family_name)
    obs = load_json(stage / "observations.json")
    path = store / "observations" / year / f"{year}-{family_name}.json"
    merged = merge_rows(path, obs, event_mode=True)
    context = load_json(stage / "context.json")
    return {
        "status": "PASS",
        "collector_version": run.get("collector_version"),
        "new_store_rows": merged["added"],
        "merge": merged,
        "observations_path": str(path),
        "context": context,
        "run": run,
    }


def latest_period(path: str | None) -> str | None:
    if not path:
        return None
    rows = load_json(Path(path), [])
    refs = [ref_key(x) for x in rows if ref_key(x)]
    return max(refs) if refs else None


def previous_manifest(store: Path) -> dict[str, Any]:
    path = store / "manifests" / "latest.json"
    if not path.exists():
        return {}
    try:
        payload = load_json(path)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def resolve_omo7d_state(prev: dict[str, Any], omo_stage: Path) -> dict[str, Any] | None:
    run = load_json(omo_stage / "run.json")
    obs = load_json(omo_stage / "observations.json")
    changed = [x for x in obs if x.get("series_id") == "POL_OMO7D_RATE"]
    if changed:
        row = sorted(changed, key=ref_key)[-1]
        return {
            "value": row.get("value"),
            "unit": row.get("unit"),
            "reference_date": ref_key(row),
            "basis": "PERSISTED_INCREMENTAL_CHANGE_ROOT",
            "collector_version": row.get("collector_version"),
        }

    old = ((prev.get("policy_state") or {}).get("omo7d_rate") if isinstance(prev, dict) else None)
    if isinstance(old, dict) and old.get("value") is not None:
        carried = dict(old)
        carried["carry_forward"] = True
        return carried

    check = run.get("omo7d_incremental_state_check")
    if isinstance(check, dict) and check.get("latest_recent_rate") is not None:
        return {
            "value": check.get("latest_recent_rate"),
            "unit": "percent",
            "reference_date": check.get("latest_recent_quote_date"),
            "basis": "INITIAL_RECENT_OFFICIAL_QUOTE_STATE_ANCHOR",
            "collector_version": run.get("collector_version"),
            "state_anchor_only_not_backfill": True,
        }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--store-root", default="data/china_financial")
    ap.add_argument("--fast-dir", required=True)
    ap.add_argument("--credit-dir", required=True)
    ap.add_argument("--fiscal-dir", required=True)
    ap.add_argument("--policy-tools-dir", required=True)
    ap.add_argument("--nafmii-dir", required=True)
    ap.add_argument("--rrr-dir", required=True)
    ap.add_argument("--omo-dir", required=True)
    ap.add_argument("--local-dir", required=True)
    ap.add_argument("--central-dir", required=True)
    ap.add_argument("--summary-out")
    args = ap.parse_args()

    store = Path(args.store_root)
    year = str(args.year)
    prev = previous_manifest(store)

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "as_of": args.as_of,
        "generated_at": now_iso(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_repository": os.environ.get("GITHUB_REPOSITORY"),
        "github_source_commit": os.environ.get("GITHUB_SHA"),
        "families": {},
    }

    summary["families"]["fast_market"] = persist_fast(store, Path(args.fast_dir))
    summary["families"]["pbc_monthly_credit_v1"] = persist_family_snapshot(
        store, year, "pbc-monthly-credit-v1", Path(args.credit_dir), has_derived=True
    )
    summary["families"]["mof_fiscal_ytd_v2"] = persist_family_snapshot(
        store, year, "mof-fiscal-ytd-v2", Path(args.fiscal_dir), has_derived=True
    )
    summary["families"]["pbc_monthly_policy_tools_v2"] = persist_family_snapshot(
        store, year, "pbc-monthly-policy-tools-v2", Path(args.policy_tools_dir), has_derived=False
    )
    summary["families"]["nafmii_dfi_issuance_v1"] = persist_family_snapshot(
        store, year, "nafmii-dfi-issuance-v1", Path(args.nafmii_dir), has_derived=False
    )
    summary["families"]["rrr_event_v5_incremental"] = persist_event_family(
        store, year, "rrr-event-v5-incremental", Path(args.rrr_dir)
    )
    summary["families"]["policy_event_v6_incremental"] = persist_event_family(
        store, year, "policy-event-v6-incremental", Path(args.omo_dir)
    )
    summary["families"]["local_gov_cash_clock_v3"] = persist_government_context(
        store, year, "local-gov-cash-clock-v3", Path(args.local_dir), central=False
    )
    summary["families"]["central_gov_cash_clock_v5"] = persist_government_context(
        store, year, "central-gov-cash-clock-v5", Path(args.central_dir), central=True
    )

    fast = summary["families"]["fast_market"]
    policy_state: dict[str, Any] = {}
    omo_state = resolve_omo7d_state(prev, Path(args.omo_dir))
    if omo_state is not None:
        policy_state["omo7d_rate"] = omo_state

    latest = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "as_of": args.as_of,
        "generated_at": summary["generated_at"],
        "latest_valid_market_session": fast["market_date"],
        "fast_market_coverage": f"{fast.get('coverage_count')}/{fast.get('expected_count')}",
        "policy_state": policy_state,
        "family_status": {
            name: {
                "status": payload.get("status"),
                "new_store_rows": payload.get("new_store_rows", 0),
                "latest_reference": latest_period(payload.get("observations_path")),
            }
            for name, payload in summary["families"].items()
        },
        "fiscal_context": {
            "local_government_bond": summary["families"]["local_gov_cash_clock_v3"]["context"],
            "central_government_bond": summary["families"]["central_gov_cash_clock_v5"]["context"],
        },
        "runtime_rules": {
            "model_reads_store_not_collectors": True,
            "collector_and_model_runtime_decoupled": True,
            "unknown_is_never_zero": True,
            "same_key_conflicting_revision_fails_closed": True,
            "identical_low_frequency_rows_do_not_refresh_old_vintage": True,
        },
    }

    daily_manifest = store / "manifests" / year / f"{args.as_of}-daily-persist-v1.json"
    latest_manifest = store / "manifests" / "latest.json"
    daily_run = store / "runs" / year / f"{args.as_of}-daily-persist-v1.json"
    write_json(daily_manifest, latest)
    write_json(latest_manifest, latest)
    write_json(daily_run, summary)

    total_new = sum(int(x.get("new_store_rows", 0) or 0) for x in summary["families"].values())
    result = {
        "status": "PASS",
        "pipeline_version": PIPELINE_VERSION,
        "as_of": args.as_of,
        "latest_valid_market_session": fast["market_date"],
        "new_store_rows": total_new,
        "latest_manifest": str(latest_manifest),
        "daily_manifest": str(daily_manifest),
        "daily_run": str(daily_run),
    }
    if args.summary_out:
        write_json(Path(args.summary_out), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
