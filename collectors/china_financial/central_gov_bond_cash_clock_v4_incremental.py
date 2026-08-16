#!/usr/bin/env python3
"""Central-government Treasury cash-clock V4 incremental context collector.

V1/V2/V3 are preserved. V4 aligns readiness blocking with the current China
Financial Draft's fiscal architecture:

* FISC_DEPOSIT_CHANGE is the realized net fiscal-liquidity anchor elsewhere in
  the module.
* Government-bond cash clock is a supporting Context / Modifier, not a complete
  security ledger and not a standalone hard state axis.
* The official MOF publication-list window MUST be proven.
* If core pre-issue documents exist in the recent window, at least one must be
  successfully readable so the cash-clock context is usable.
* An individual detail-page transient failure is an explicit non-blocking
  diagnostic when other core schedule evidence is available; it is retried on
  later DAILY_INCREMENTAL runs.
* If every discovered core pre-issue detail fails, the context is incomplete.
* Result-detail failures are incremental-confirmation diagnostics and never
  silently become payment facts.

This prevents a single flaky MOF detail page from overstating a system-wide data
failure while still refusing to call an entirely unreadable schedule window
READY.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import central_gov_bond_cash_clock_v3_incremental as v3

COLLECTOR_VERSION = "V1.9-READY-CENTRAL-GOV-BOND-CASH-CLOCK-V4-INCREMENTAL"


def collect(as_of: date, lookback_days: int = 7):
    observations, v3_blocking, diagnostics, context, run = v3.collect(as_of, lookback_days)

    window = context.get("window_proof", {})
    list_proven = bool(window.get("publication_list_window_checked"))
    core_discovered = int(window.get("core_preissue_documents_discovered") or 0)
    core_success = int(window.get("core_preissue_documents_successfully_read") or 0)

    blocking: list[dict[str, Any]] = []
    diagnostics = list(diagnostics)

    for gap in v3_blocking:
        if gap.get("stage") == "MOF_BUSINESS_ANNOUNCEMENT_DETAIL":
            diagnostics.append({
                **gap,
                "readiness_blocking": False,
                "v4_reason": "INDIVIDUAL_DETAIL_FAILURE_WITH_LIST_WINDOW_PROVEN_RETRY_NEXT_DAILY_RUN",
            })
        else:
            blocking.append(gap)

    # A proven list with discovered core notices is usable only if at least one
    # core schedule detail is readable. We do not require every individual
    # supporting detail page to be available in the same run.
    if list_proven and core_discovered > 0 and core_success == 0:
        blocking.append({
            "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
            "reference_date": as_of.isoformat(),
            "gap_class": "CORE_CONTEXT_UNAVAILABLE",
            "stage": "MOF_CORE_PREISSUE_SCHEDULE",
            "message": "Core pre-issue notices were discovered but none of their detail pages was readable in this run.",
            "collector_version": COLLECTOR_VERSION,
        })

    usable = list_proven and not blocking and (core_discovered == 0 or core_success > 0)

    for row in observations:
        row["collector_version"] = COLLECTOR_VERSION

    context = dict(context)
    context["collector_version"] = COLLECTOR_VERSION
    context["blocking_gap_count"] = len(blocking)
    context["nonblocking_diagnostic_count"] = len(diagnostics)
    context["readiness_semantics"] = {
        "fiscal_deposit_change_is_realized_net_anchor": True,
        "government_bond_cash_clock_is_supporting_context": True,
        "publication_list_window_must_be_proven": True,
        "at_least_one_core_detail_required_when_core_docs_exist": True,
        "every_individual_core_detail_required": False,
        "individual_detail_failure_is_retried_incrementally": True,
        "all_core_details_unreadable_is_blocking": True,
        "unknown_is_not_zero": True,
    }
    if not list_proven:
        context["status"] = "UNKNOWN_WINDOW_NOT_PROVEN"
    elif blocking:
        context["status"] = "INCOMPLETE_CORE_CONTEXT"
    elif diagnostics and observations:
        context["status"] = "PASS_EVENTS_FOUND_WITH_NONBLOCKING_DIAGNOSTICS"
    elif diagnostics:
        context["status"] = "PASS_WINDOW_WITH_NONBLOCKING_DIAGNOSTICS"
    elif observations:
        context["status"] = "PASS_EVENTS_FOUND"
    else:
        context["status"] = "PASS_NO_EVENT_IN_PROVEN_WINDOW"

    run = {
        "module": "china_financial_central_gov_bond_cash_clock_v4_incremental",
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "as_of": as_of.isoformat(),
        "status": "PASS" if usable else "INCOMPLETE",
        "context_status": context["status"],
        "observation_count": len(observations),
        "blocking_gap_count": len(blocking),
        "nonblocking_diagnostic_count": len(diagnostics),
        "core_preissue_documents_discovered": core_discovered,
        "core_preissue_documents_successfully_read": core_success,
    }
    return observations, blocking, diagnostics, context, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    obs, gaps, diag, context, run = collect(date.fromisoformat(args.as_of), args.lookback_days)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in {
        "observations.json": obs,
        "gaps.json": gaps,
        "diagnostics.json": diag,
        "context.json": context,
        "run.json": run,
    }.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": run, "context": context}, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
