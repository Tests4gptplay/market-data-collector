#!/usr/bin/env python3
"""Central-government Treasury cash-clock V3 incremental context collector.

V1/V2 are preserved for rollback and detailed regression work. V3 is the
rolling DAILY_INCREMENTAL implementation used by the China Financial Draft
measurement layer.

Draft-scope objective
---------------------
The China Financial fiscal context needs a practical government-bond cash
clock, not a complete security master. For central-government book-entry
Treasuries the minimum useful facts are:

* planned auction / issuance date and amount;
* explicit issue-payment deadline (cash drain) and amount;
* principal maturity date and amount;
* coupon schedule/cash estimate when directly supported by the announcement;
* post-auction actual amount/coupon as an incremental correction when available.

Readiness semantics
-------------------
* The MOF business-announcement list is the publication-window proof.
* A pre-issue notice (title contains ``发行工作有关事宜``) is CORE cash-clock
  evidence. If such a document is discovered in the checked window but its
  detail cannot be read/parsed, the core window is incomplete.
* A post-auction ``国债业务公告`` detail is an ACTUAL-CORRECTION layer. A
  transient detail-page failure is recorded but does not invalidate an already
  proven core schedule window. It is retried on later daily runs.
* Distribution end is never promoted to a payment fact.
* No historical backfill is performed by default; the caller supplies a short
  recent lookback window.
* Unknown is never zero.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import central_gov_bond_cash_clock_v2 as v2

v1 = v2.v1
COLLECTOR_VERSION = "V1.9-READY-CENTRAL-GOV-BOND-CASH-CLOCK-V3-INCREMENTAL"


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {v1.dedupe_key(x): x for x in rows}
    return sorted(unique.values(), key=lambda x: (x["reference_date"], x["series_id"], x["source_url"]))


def _doc_class(title: str) -> str:
    if "发行工作有关事宜" in title:
        return "CORE_PREISSUE_SCHEDULE"
    if "国债业务公告" in title:
        return "ACTUAL_RESULT_CORRECTION"
    return "OTHER_DISCOVERED_DOCUMENT"


def collect(as_of: date, lookback_days: int = 7):
    if lookback_days < 0 or lookback_days > 31:
        raise ValueError("lookback_days must be between 0 and 31")

    start = as_of - timedelta(days=lookback_days)
    observations: list[dict[str, Any]] = []
    blocking_gaps: list[dict[str, Any]] = []
    nonblocking_diagnostics: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    list_evidence: list[dict[str, Any]] = []

    try:
        docs, list_evidence = v1.documents_in_window(start, as_of)
        list_window_proven = not bool(list_evidence and list_evidence[-1].get("window_lower_bound_not_crossed"))
    except Exception as exc:
        docs = []
        list_window_proven = False
        blocking_gaps.append({
            "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
            "reference_date": as_of.isoformat(),
            "gap_class": "SOURCE_FAILURE",
            "stage": "MOF_BUSINESS_ANNOUNCEMENT_LIST",
            "message": repr(exc),
            "collector_version": COLLECTOR_VERSION,
        })

    core_docs_discovered = 0
    core_docs_success = 0
    correction_docs_discovered = 0
    correction_docs_success = 0

    for doc in docs:
        title = doc.get("title", "")
        cls = _doc_class(title)
        if cls == "CORE_PREISSUE_SCHEDULE":
            core_docs_discovered += 1
        elif cls == "ACTUAL_RESULT_CORRECTION":
            correction_docs_discovered += 1

        try:
            rows, gaps, run = v2.hardened_parse_document(doc["url"], doc["publication_date"])
            for row in rows:
                row["collector_version"] = COLLECTOR_VERSION
            observations.extend(rows)
            run = dict(run)
            run["document_class"] = cls
            run["collector_version"] = COLLECTOR_VERSION
            documents.append(run)

            if cls == "CORE_PREISSUE_SCHEDULE":
                if gaps:
                    blocking_gaps.extend(gaps)
                else:
                    core_docs_success += 1
            elif cls == "ACTUAL_RESULT_CORRECTION":
                if gaps:
                    nonblocking_diagnostics.extend({**g, "readiness_blocking": False} for g in gaps)
                else:
                    correction_docs_success += 1
            else:
                # Out-of-scope/other discovered documents are diagnostics only.
                nonblocking_diagnostics.extend({**g, "readiness_blocking": False} for g in gaps)
        except Exception as exc:
            item = {
                "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
                "reference_date": doc["publication_date"].isoformat(),
                "gap_class": "SOURCE_OR_PARSER_FAILURE",
                "stage": "MOF_BUSINESS_ANNOUNCEMENT_DETAIL",
                "message": repr(exc),
                "collector_version": COLLECTOR_VERSION,
                "source_url": doc["url"],
                "document_class": cls,
            }
            documents.append({
                "url": doc["url"],
                "publication_date": doc["publication_date"].isoformat(),
                "title": title,
                "document_class": cls,
                "status": "FAIL",
                "error": repr(exc),
            })
            if cls == "CORE_PREISSUE_SCHEDULE":
                blocking_gaps.append(item)
            else:
                nonblocking_diagnostics.append({**item, "readiness_blocking": False})

    observations = _dedupe(observations)
    core_window_ready = list_window_proven and core_docs_success == core_docs_discovered and not blocking_gaps

    if not list_window_proven:
        context_status = "UNKNOWN_WINDOW_NOT_PROVEN"
    elif not core_window_ready:
        context_status = "INCOMPLETE_CORE_SCHEDULE_WINDOW"
    elif observations:
        context_status = "PASS_EVENTS_FOUND"
    else:
        context_status = "PASS_NO_EVENT_IN_PROVEN_WINDOW"

    context = {
        "module": "china_financial_central_gov_bond_cash_clock_context",
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "as_of": as_of.isoformat(),
        "publication_window_start": start.isoformat(),
        "publication_window_end": as_of.isoformat(),
        "status": context_status,
        "scope": "CENTRAL_GOVERNMENT_BOOK_ENTRY_TREASURY_CASH_CLOCK_CONTEXT",
        "window_proof": {
            "publication_list_window_checked": list_window_proven,
            "core_preissue_documents_discovered": core_docs_discovered,
            "core_preissue_documents_successfully_read": core_docs_success,
            "actual_correction_documents_discovered": correction_docs_discovered,
            "actual_correction_documents_successfully_read": correction_docs_success,
            "unknown_is_never_zero": True,
            "no_event_claim_requires_complete_list_window": True,
        },
        "observation_count": len(observations),
        "blocking_gap_count": len(blocking_gaps),
        "nonblocking_diagnostic_count": len(nonblocking_diagnostics),
        "documents": documents,
        "list_evidence": list_evidence,
        "semantic_rules": {
            "preissue_schedule_is_core_cash_clock": True,
            "actual_result_is_incremental_correction": True,
            "actual_result_detail_transient_failure_does_not_block_core_ready": True,
            "distribution_end_is_not_payment_fact": True,
            "no_security_master_required": True,
            "daily_incremental_only": True,
            "historical_backfill_not_default": True,
        },
    }

    run = {
        "module": "china_financial_central_gov_bond_cash_clock_v3_incremental",
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "as_of": as_of.isoformat(),
        "status": "PASS" if core_window_ready else "INCOMPLETE",
        "context_status": context_status,
        "observation_count": len(observations),
        "blocking_gap_count": len(blocking_gaps),
        "nonblocking_diagnostic_count": len(nonblocking_diagnostics),
    }
    return observations, blocking_gaps, nonblocking_diagnostics, context, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    as_of = date.fromisoformat(args.as_of)
    obs, gaps, diag, context, run = collect(as_of, args.lookback_days)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payloads = {
        "observations.json": obs,
        "gaps.json": gaps,
        "diagnostics.json": diag,
        "context.json": context,
        "run.json": run,
    }
    for name, payload in payloads.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": run, "context": context}, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
