#!/usr/bin/env python3
"""PBOC broad RRR incremental collector V5.

Default daily mode is forward-only incremental collection. It checks only a
recent publication window and appends a new direct broad-RRR event when one is
published. It does NOT crawl 2025/2024 history merely to prove that the state is
unchanged.

Existing state is expected to be carried by the downstream Store. Historical
state-anchor lookup is deliberately outside the default daily path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collectors.china_financial import rrr_event_family_v3 as v3
from collectors.china_financial import rrr_event_family_v4 as v4

COLLECTOR_VERSION = "V1.9-CANDIDATE-RRR-EVENT-FAMILY-V5-INCREMENTAL"
DEFAULT_LOOKBACK_DAYS = 10
MAX_RECENT_PAGES = 8


def collect(target: date, since: date | None = None):
    if since is None:
        since = target - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    if since > target:
        raise ValueError("since_date must be <= target_date")

    obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    pages_scanned = 0
    oldest_seen: date | None = None

    # The current/recent PBC news pages are newest-first. Daily incremental mode
    # starts at page 1 and stops as soon as it has crossed the requested recent
    # window. It never invokes the historical page locator.
    for page_no in range(1, MAX_RECENT_PAGES + 1):
        try:
            p = v3._page(page_no)
            pages_scanned += 1
            page_evidence.append({
                k: (value.isoformat() if isinstance(value, date) else value)
                for k, value in p.items() if k != "rows"
            })
        except Exception as exc:
            diagnostics.append({
                "page": page_no,
                "reason": "RECENT_PAGE_FETCH_FAILURE",
                "error": repr(exc),
            })
            continue

        row_dates = [r.get("list_date") for r in p["rows"] if isinstance(r.get("list_date"), date)]
        if row_dates:
            page_oldest = min(row_dates)
            oldest_seen = page_oldest if oldest_seen is None else min(oldest_seen, page_oldest)

        for row in p["rows"]:
            d = row.get("list_date")
            if d is None or d < since or d > target:
                continue
            if not v4._direct_rrr_title(row["title"]):
                continue
            if row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            try:
                parsed = v4._parse_direct(row["url"], d)
                if parsed is not None:
                    parsed["collector_version"] = COLLECTOR_VERSION
                    parsed["dimensions"].update({
                        "incremental_mode": True,
                        "incremental_since_date": since.isoformat(),
                        "incremental_target_date": target.isoformat(),
                        "historical_backfill_performed": False,
                        "existing_state_should_carry_forward_when_no_new_event": True,
                    })
                    obs.append(parsed)
            except Exception as exc:
                # A direct RRR announcement inside the active window is expected
                # data. Failure to parse it is a real current-scope GAP.
                gaps.append({
                    "family": "RRR_EVENT",
                    "target_date": target.isoformat(),
                    "since_date": since.isoformat(),
                    "source_url": row["url"],
                    "title": row["title"],
                    "reason": "NEW_DIRECT_RRR_ARTICLE_PARSE_FAILURE",
                    "error": repr(exc),
                })

        if oldest_seen is not None and oldest_seen < since:
            break

    # No RRR announcement in the recent window is a normal no-event result, not
    # a GAP and not a reason to crawl older years.
    obs.sort(key=lambda x: (x["reference_date"], x["source_url"]))
    run = {
        "module": "china_financial_rrr_event_family",
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "target_date": target.isoformat(),
        "since_date": since.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(obs),
        "new_event_count": len(obs),
        "gap_count": len(gaps),
        "pages_scanned": pages_scanned,
        "oldest_seen_date": oldest_seen.isoformat() if oldest_seen else None,
        "diagnostics": diagnostics,
        "page_evidence": page_evidence,
        "semantic_rules": {
            "daily_incremental_only": True,
            "default_recent_lookback_days": DEFAULT_LOOKBACK_DAYS,
            "no_2025_2024_backfill_in_daily_mode": True,
            "no_new_release_is_not_gap": True,
            "existing_state_carries_forward_outside_collector": True,
            "direct_policy_announcement_only_for_root": True,
            "historical_review_is_qc_only": True,
            "future_information_forbidden": True,
        },
    }
    return obs, gaps, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--since-date")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    target = date.fromisoformat(a.date)
    since = date.fromisoformat(a.since_date) if a.since_date else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    obs, gaps, run = collect(target, since)
    for n, payload in (("observations.json", obs), ("gaps.json", gaps), ("run.json", run)):
        (out / n).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if gaps:
        print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
