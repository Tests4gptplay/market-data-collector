#!/usr/bin/env python3
"""Local-government bond cash-clock V3: simplified rolling-window context.

This version deliberately does less than V1/V2. It reuses the already-tested
official CELMA parsers from V1 and turns recent publication-day facts into the
small set of fiscal-liquidity context needed by the China Financial model.

Scope:
- local-government bond issuance/auction events;
- explicit issuance-payment events;
- principal maturity events;
- coupon cash estimates / coupon schedule facts when the official notice allows
  them to be classified safely.

Non-goals:
- no attempt to build a perfect security master;
- no silent central-government-bond substitution;
- no conversion of a no-document day into zero cash flow;
- no reconciliation of planned face value with later actual issuance in this
  wrapper. V1 observations keep event_stage / amount_semantic distinctions.

The rolling publication window solves the main operational problem of V1 daily
mode: a weekend or a quiet publication date may legitimately have no new
notice, while the model still needs recently announced cash-clock context.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import local_gov_bond_cash_clock_v1 as v1

COLLECTOR_VERSION = "V1.9-CANDIDATE-LOCAL-GOV-BOND-CASH-CLOCK-V3"
EVENT_SERIES = {
    "FISC_GOV_BOND_AUCTION_EVENT",
    "FISC_GOV_BOND_PAYMENT_EVENT",
    "FISC_GOV_BOND_MATURITY_EVENT",
    "FISC_GOV_BOND_COUPON_SCHEDULE_EVENT",
    "FISC_GOV_BOND_COUPON_CASH_ESTIMATE",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dedupe_key(obs: dict[str, Any]) -> tuple[Any, ...]:
    dims = obs.get("dimensions") or {}
    return (
        obs.get("series_id"),
        obs.get("reference_date"),
        obs.get("value"),
        obs.get("source_url"),
        dims.get("bond_code") or dims.get("bond_id"),
        dims.get("event_stage"),
        dims.get("amount_semantic"),
    )


def _normalize_observation(obs: dict[str, Any], collected_at: str) -> dict[str, Any]:
    """Add interface-compatible aliases without altering V1 evidence semantics."""
    out = dict(obs)
    dims = dict(out.get("dimensions") or {})
    source_semantic = out.get("source_semantic") or dims.get("source_semantic")
    if source_semantic is None:
        source_semantic = dims.get("amount_semantic") or "CELMA_LOCAL_GOV_BOND_EVENT"
    out["source_semantic"] = source_semantic
    out["collected_at"] = out.get("collected_at") or collected_at
    out["collector_version"] = COLLECTOR_VERSION
    out["dimensions"] = dims
    return out


def collect(as_of: date, lookback_days: int = 7) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if lookback_days < 0 or lookback_days > 31:
        raise ValueError("lookback_days must be between 0 and 31")

    collected_at = _utc_now()
    raw_obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    daily_runs: list[dict[str, Any]] = []
    successful_days = 0
    failed_days = 0

    start = as_of - timedelta(days=lookback_days)
    d = start
    while d <= as_of:
        try:
            obs, day_gaps, day_run = v1.collect(d)
            successful_days += 1
            raw_obs.extend(obs)
            gaps.extend(day_gaps)
            daily_runs.append({
                "publication_date": d.isoformat(),
                "status": "PASS" if not day_gaps else "INCOMPLETE",
                "observation_count": len(obs),
                "gap_count": len(day_gaps),
                "source_run": day_run,
            })
        except Exception as exc:
            failed_days += 1
            gaps.append({
                "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
                "reference_date": d.isoformat(),
                "gap_class": "SOURCE_OR_PARSER_FAILURE",
                "stage": "ROLLING_PUBLICATION_WINDOW",
                "message": repr(exc),
                "collector_version": COLLECTOR_VERSION,
            })
            daily_runs.append({
                "publication_date": d.isoformat(),
                "status": "SOURCE_FAILURE",
                "observation_count": 0,
                "gap_count": 1,
            })
        d += timedelta(days=1)

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for obs in raw_obs:
        if obs.get("series_id") not in EVENT_SERIES:
            continue
        unique[_dedupe_key(obs)] = _normalize_observation(obs, collected_at)
    observations = sorted(
        unique.values(),
        key=lambda x: (str(x.get("reference_date")), str(x.get("series_id")), str(x.get("source_url"))),
    )

    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, int] = defaultdict(int)
    event_dates: dict[str, list[str]] = defaultdict(list)
    for obs in observations:
        sid = str(obs.get("series_id"))
        counts[sid] += 1
        if obs.get("reference_date"):
            event_dates[sid].append(str(obs["reference_date"]))
        value = obs.get("value")
        if isinstance(value, (int, float)):
            dims = obs.get("dimensions") or {}
            stage = str(dims.get("event_stage") or "UNSPECIFIED")
            totals[sid][stage] += float(value)

    full_window_proven = failed_days == 0
    if observations:
        context_status = "PASS_EVENTS_FOUND" if full_window_proven else "PARTIAL_EVENTS_FOUND_WITH_SOURCE_FAILURE"
    else:
        context_status = "PASS_NO_EVENT_IN_PROVEN_WINDOW" if full_window_proven else "UNKNOWN_SOURCE_FAILURE_IN_WINDOW"

    context = {
        "module": "china_financial_local_gov_bond_cash_clock_context",
        "collector_version": COLLECTOR_VERSION,
        "as_of": as_of.isoformat(),
        "publication_window_start": start.isoformat(),
        "publication_window_end": as_of.isoformat(),
        "lookback_days": lookback_days,
        "status": context_status,
        "scope": "LOCAL_GOVERNMENT_BONDS_ONLY",
        "central_government_bonds_included": False,
        "window_proof": {
            "all_publication_days_checked": full_window_proven,
            "successful_days": successful_days,
            "failed_days": failed_days,
            "unknown_is_never_zero": True,
            "no_event_claim_requires_complete_window": True,
        },
        "event_count_by_series": dict(sorted(counts.items())),
        "known_amount_cny_100m_by_series_and_stage": {
            sid: {stage: round(value, 8) for stage, value in sorted(stages.items())}
            for sid, stages in sorted(totals.items())
        },
        "event_dates_by_series": {sid: sorted(set(ds)) for sid, ds in sorted(event_dates.items())},
        "semantic_rules": {
            "planned_and_actual_not_reconciled_here": True,
            "event_stage_preserved": True,
            "amount_semantic_preserved": True,
            "no_security_master_required": True,
            "no_cross_government_level_substitution": True,
        },
    }

    run = {
        "module": "china_financial_local_gov_bond_cash_clock_v3",
        "collector_version": COLLECTOR_VERSION,
        "as_of": as_of.isoformat(),
        "completed_at": collected_at,
        "status": "PASS" if full_window_proven else "INCOMPLETE",
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "context_status": context_status,
        "scope": "LOCAL_GOVERNMENT_BONDS_ONLY",
        "daily_runs": daily_runs,
    }
    return observations, gaps, context, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True, help="As-of date YYYY-MM-DD")
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    observations, gaps, context, run = collect(date.fromisoformat(args.as_of), args.lookback_days)
    for name, payload in (
        ("observations.json", observations),
        ("gaps.json", gaps),
        ("context.json", context),
        ("run.json", run),
    ):
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"run": run, "context": context}, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
