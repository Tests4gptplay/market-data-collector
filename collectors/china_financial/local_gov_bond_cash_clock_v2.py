#!/usr/bin/env python3
"""Local-government bond cash-clock collector V2.

Preserves V1. V2 fixes issuance-result row parsing after regression showed that
V1 could interpret the calendar year embedded in the bond name (e.g. 2026年)
as the tenor and then treat the next year as issuance amount.

V2 anchors all issuance-term parsing strictly AFTER the bond code and requires:
  bond_code -> short/type text -> tenor -> amount -> rate -> issue date -> accrual date.
All other V1 point-in-time, payment-date and debt-service safeguards are kept.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from collectors.china_financial import local_gov_bond_cash_clock_v1 as v1

COLLECTOR_VERSION = "V1.9-CANDIDATE-LOCAL-GOV-BOND-CASH-CLOCK-V2"


def parse_result_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    codes = list(re.finditer(r"\b(\d{6,7})\b", text))
    for i, cm in enumerate(codes):
        # The data row may begin with a long bond name before the code, but every
        # numeric field we trust is required after the code.
        end = codes[i + 1].start() if i + 1 < len(codes) else min(len(text), cm.end() + 1200)
        post = text[cm.end():end]
        dates = re.findall(r"(20\d{2}-\d{2}-\d{2})", post)
        if not dates:
            continue

        # First '<n>年' after code is tenor. This explicitly excludes '2026年'
        # in the long bond name before code that broke V1.
        tm = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*年", post)
        if not tm:
            continue
        tenor = float(tm.group(1))
        if not (0 < tenor <= 100):
            continue

        first_date_pos = post.find(dates[0])
        numeric_zone = post[tm.end():first_date_pos]
        # Normal CELMA issuance-result layout after tenor contains issuance amount,
        # optional bid/price fields, and coupon/yield before issue date. Amount is
        # the first positive number after tenor; coupon is the final plausible
        # percentage-like number (0.1..10) before the first date.
        nums = [float(x.replace(",", "")) for x in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", numeric_zone)]
        if not nums:
            continue
        amount = nums[0]
        plausible_rates = [x for x in nums[1:] if 0.1 <= x <= 10]
        if not plausible_rates:
            # Some single-field layouts contain only amount then coupon; if amount
            # itself is small we still cannot reinterpret it as rate.
            continue
        coupon = plausible_rates[-1]
        if amount <= 0:
            continue

        records.append({
            "bond_code": cm.group(1),
            "tenor_years": tenor,
            "amount_100m": amount,
            "coupon_pct": coupon,
            "issue_date": dates[0],
            "accrual_date": dates[1] if len(dates) > 1 else None,
            "raw_segment": post[:700],
        })

    return list({r["bond_code"]: r for r in records}.values())


def parse_result(url: str, publication_date: date):
    # Keep V1 retrieval/evidence logic, replace only the row parser.
    original = v1.parse_result_records
    try:
        v1.parse_result_records = parse_result_records
        obs, gaps, run = v1.parse_result(url, publication_date)
    finally:
        v1.parse_result_records = original
    for x in obs:
        x["collector_version"] = COLLECTOR_VERSION
    return obs, gaps, run


def collect(target: date, detail_url: str | None = None, detail_type: str | None = None):
    if detail_url and detail_type == "RESULT":
        obs, gaps, fr = parse_result(detail_url, target)
        run = {
            "module": "china_financial_local_gov_bond_cash_clock",
            "collector_version": COLLECTOR_VERSION,
            "target_date": target.isoformat(),
            "status": "PASS" if not gaps else "INCOMPLETE",
            "observation_count": len(obs),
            "gap_count": len(gaps),
            "families": [{"family": "RESULT", "diagnostic_override": True, **fr}],
            "semantic_rules": {**v1.collect(target, detail_url, "RESULT")[2].get("semantic_rules", {}), "issuance_numeric_fields_must_follow_bond_code": True},
        }
        return obs, gaps, run

    # For PREISSUE/DEBT_SERVICE diagnostic overrides use V1 parsers but relabel
    # observations; in production daily mode reproduce V1 discovery while routing
    # RESULT documents through the corrected parser.
    if detail_url:
        obs, gaps, run = v1.collect(target, detail_url, detail_type)
        for x in obs:
            x["collector_version"] = COLLECTOR_VERSION
        run["collector_version"] = COLLECTOR_VERSION
        return obs, gaps, run

    obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    families = []
    for fam, ch in v1.CHANNELS.items():
        try:
            docs, ev = v1.documents_on_date(ch, target)
        except Exception as exc:
            gaps.append({"family": fam, "target_date": target.isoformat(), "source_url": v1.list_url(ch, 1), "reason": "LIST_DISCOVERY_FAILURE", "error": repr(exc)})
            continue
        fr = {"family": fam, "document_count": len(docs), "list_evidence": ev, "documents": []}
        fn = {"PREISSUE": v1.parse_preissue, "RESULT": parse_result, "DEBT_SERVICE": v1.parse_debt_service}[fam]
        for d in docs:
            o, g, r = fn(d["url"], d["publication_date"])
            for x in o:
                x["collector_version"] = COLLECTOR_VERSION
            obs += o; gaps += g
            fr["documents"].append({"title": d["title"], "url": d["url"], "publication_date": d["publication_date"].isoformat(), **r})
        families.append(fr)
    unique = {json.dumps([x["series_id"], x["reference_date"], x.get("value"), x.get("source_url"), x.get("dimensions", {}).get("bond_code"), x.get("dimensions", {}).get("event_stage")], ensure_ascii=False, sort_keys=True): x for x in obs}
    obs = list(unique.values())
    run = {
        "module": "china_financial_local_gov_bond_cash_clock",
        "collector_version": COLLECTOR_VERSION,
        "target_date": target.isoformat(),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(obs), "gap_count": len(gaps), "families": families,
        "semantic_rules": {
            "no_document_day_is_no_event_not_gap": True,
            "publication_date_must_not_exceed_target": True,
            "payment_date_must_be_explicit": True,
            "accrual_date_never_substituted_for_payment": True,
            "planned_and_actual_amounts_kept_distinct": True,
            "unclassified_debt_service_split_is_gap": True,
            "issuance_numeric_fields_must_follow_bond_code": True,
        },
    }
    return obs, gaps, run


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", required=True); ap.add_argument("--out", required=True); ap.add_argument("--detail-url"); ap.add_argument("--detail-type"); a = ap.parse_args()
    target = date.fromisoformat(a.date); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    obs, gaps, run = collect(target, a.detail_url, a.detail_type)
    for n, payload in (("observations.json", obs), ("gaps.json", gaps), ("run.json", run)):
        (out / n).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if gaps: print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
