#!/usr/bin/env python3
"""Central-government Treasury cash-clock V5 READY context adapter.

V1-V4 are preserved. V5 is the active READY-facing adapter used by the unified
China Financial data-layer gate.

Why this layer exists
---------------------
The current China Financial Draft treats government-bond cash timing as a
FiscalSystemLiquidityContext / Modifier. Realized fiscal-liquidity anchoring is
provided by fiscal-deposit changes and the broader fiscal roots elsewhere in
the module. A temporary MOF web outage must therefore be represented as
UNKNOWN context with lower Data Confidence, not fabricated zero and not a
false declaration that the entire China Financial measurement layer is broken.

Semantics
---------
* V4 remains the strict collector/diagnostic implementation.
* If V4 has usable context, V5 passes it through.
* If the official MOF list/detail source is temporarily unavailable or the
  supporting cash-clock context cannot be established in this run, V5 exposes
  ``UNKNOWN_SOURCE_HEALTH_WARNING`` and preserves every gap as a diagnostic.
* UNKNOWN is never zero/neutral.
* The next DAILY_INCREMENTAL run retries the source.
* This graceful degradation applies only to this supporting government-bond
  context. It does not weaken the hard gates for Funding, RiskBearing, Slow
  Balance Sheet or realized fiscal anchors.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import central_gov_bond_cash_clock_v4_incremental as v4

COLLECTOR_VERSION = "V1.9-READY-CENTRAL-GOV-BOND-CASH-CLOCK-V5-CONTEXT"


def collect(as_of: date, lookback_days: int = 7):
    observations, blocking, diagnostics, context, run = v4.collect(as_of, lookback_days)

    for row in observations:
        row["collector_version"] = COLLECTOR_VERSION

    diagnostics = list(diagnostics)
    for gap in blocking:
        diagnostics.append({
            **gap,
            "readiness_blocking": False,
            "v5_context_handling": "SUPPORTING_CONTEXT_UNKNOWN_RETRY_NEXT_INCREMENTAL_RUN",
        })

    source_context_available = run.get("status") == "PASS"
    context = dict(context)
    context["collector_version"] = COLLECTOR_VERSION
    context["blocking_gap_count"] = 0
    context["nonblocking_diagnostic_count"] = len(diagnostics)
    context["source_context_available"] = source_context_available
    context["data_confidence_effect"] = "NONE" if source_context_available else "DOWNWEIGHT_FISCAL_CASH_CLOCK_CONTEXT"
    context["ready_adapter_semantics"] = {
        "government_bond_cash_clock_is_supporting_context": True,
        "realized_fiscal_anchor_is_external_to_this_collector": "FISC_DEPOSIT_CHANGE",
        "source_outage_becomes_unknown_not_zero": True,
        "source_outage_retried_next_daily_increment": True,
        "source_outage_does_not_relax_other_core_measurement_gates": True,
    }

    if source_context_available:
        ready_status = "PASS_CONTEXT_AVAILABLE"
    else:
        ready_status = "PASS_CONTEXT_UNKNOWN_SOURCE_HEALTH_WARNING"
        context["status"] = "UNKNOWN_SOURCE_HEALTH_WARNING"

    ready_run = {
        "module": "china_financial_central_gov_bond_cash_clock_v5_context",
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "as_of": as_of.isoformat(),
        "status": "PASS",
        "ready_status": ready_status,
        "source_context_available": source_context_available,
        "observation_count": len(observations),
        "blocking_gap_count": 0,
        "nonblocking_diagnostic_count": len(diagnostics),
        "upstream_v4_status": run.get("status"),
        "upstream_v4_context_status": run.get("context_status"),
    }
    return observations, [], diagnostics, context, ready_run


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
