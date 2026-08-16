#!/usr/bin/env python3
"""PBOC policy-event collector V6: forward-only daily incremental mode.

High-frequency OMO amount/event collection remains target-date based through V1.
The low-frequency 7-day reverse-repo policy-rate state is checked only over a
short recent window. Daily mode never crawls 2025/2024 history merely to prove
that the policy rate is unchanged.

If a previous stored policy-rate value is supplied, a POL_OMO7D_RATE Root is
emitted only when the latest recent official quote differs from that stored
state. Without a previous value, the latest recent quote is reported in run
metadata as a state check but is not backfilled as a historical series.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collectors.china_financial import policy_event_family_v1 as v1
from collectors.china_financial import policy_event_family_v5 as v5

COLLECTOR_VERSION = "V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V6-INCREMENTAL"
DEFAULT_LOOKBACK_DAYS = 10
MAX_RECENT_PAGES = 4


def check_recent_omo7d(target: date, since: date, previous_rate: float | None = None):
    gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    quotes: dict[date, list[dict[str, Any]]] = {}
    articles_inspected = 0
    oldest_quote_date: date | None = None

    for page in range(1, MAX_RECENT_PAGES + 1):
        try:
            rows, ev, _ = v5._list_page(page)
            page_evidence.append(ev)
        except Exception as exc:
            diagnostics.append({
                "page": page,
                "reason": "RECENT_OMO_LIST_PAGE_FAILURE",
                "error": repr(exc),
            })
            continue

        page_crossed_window = False
        for item in rows:
            if item["title_year"] > target.year:
                continue
            try:
                parsed = v5._parse_article_once(item["url"])
                articles_inspected += 1
            except Exception as exc:
                diagnostics.append({
                    "source_url": item["url"],
                    "reason": "RECENT_OMO_ARTICLE_PARSE_FAILURE",
                    "error": repr(exc),
                })
                continue

            d = parsed.get("date")
            if not isinstance(d, date):
                continue
            oldest_quote_date = d if oldest_quote_date is None else min(oldest_quote_date, d)
            if d < since:
                page_crossed_window = True
                continue
            if d > target or parsed.get("rate") is None:
                continue
            quotes.setdefault(d, []).append({"title": item["title"], **parsed})

        if page_crossed_window or (oldest_quote_date is not None and oldest_quote_date < since):
            break

    valid_dates = sorted(quotes.keys(), reverse=True)
    if not valid_dates:
        # No recent 7D quote is normal for an incremental state checker. Existing
        # Store state carries forward; this is not a reason to crawl old years.
        return [], gaps, {
            "status": "NO_RECENT_7D_QUOTE",
            "since_date": since.isoformat(),
            "target_date": target.isoformat(),
            "pages_inspected": len(page_evidence),
            "articles_inspected": articles_inspected,
            "diagnostics": diagnostics,
            "page_evidence": page_evidence,
        }

    d = valid_dates[0]
    vals = sorted({round(float(x["rate"]), 10) for x in quotes[d]})
    if len(vals) != 1:
        gaps.append({
            "family": "OMO7D_RATE",
            "target_date": target.isoformat(),
            "reference_date": d.isoformat(),
            "reason": "RECENT_RATE_CONFLICT",
            "rates": vals,
        })
        return [], gaps, {
            "status": "INCOMPLETE",
            "since_date": since.isoformat(),
            "target_date": target.isoformat(),
        }

    current = quotes[d][0]
    current_rate = float(current["rate"])
    changed = previous_rate is not None and abs(current_rate - float(previous_rate)) > 1e-12
    observations: list[dict[str, Any]] = []

    if changed:
        observations.append({
            "series_id": "POL_OMO7D_RATE",
            "reference_date": d.isoformat(),
            "value": current_rate,
            "unit": "percent",
            "provider": "People's Bank of China",
            "source_url": current["source_url"],
            "available_at": current["available_at"],
            "collector_version": COLLECTOR_VERSION,
            "evidence_sha256": current["sha256"],
            "dimensions": {
                "source_semantic": "PBOC_7D_REVERSE_REPO_POLICY_RATE_INCREMENTAL_CHANGE",
                "incremental_mode": True,
                "incremental_since_date": since.isoformat(),
                "incremental_target_date": target.isoformat(),
                "previous_stored_rate": float(previous_rate),
                "change_bps_vs_previous_stored_state": round((current_rate - float(previous_rate)) * 100.0, 8),
                "selection_rule": current.get("selection"),
                "historical_backfill_performed": False,
                "point_in_time_safe": True,
            },
        })

    return observations, gaps, {
        "status": "PASS" if not gaps else "INCOMPLETE",
        "since_date": since.isoformat(),
        "target_date": target.isoformat(),
        "latest_recent_quote_date": d.isoformat(),
        "latest_recent_rate": current_rate,
        "previous_rate_supplied": previous_rate is not None,
        "state_change_detected": changed,
        "observation_emitted": changed,
        "pages_inspected": len(page_evidence),
        "articles_inspected": articles_inspected,
        "diagnostics": diagnostics,
        "page_evidence": page_evidence,
    }


def collect(target: date, since: date | None = None, previous_rate: float | None = None):
    if since is None:
        since = target - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    if since > target:
        raise ValueError("since_date must be <= target_date")

    # V1 handles target-date OMO / buyout / MLF amount-event facts. Those are
    # high-frequency/event facts and do not require historical replay.
    observations, gaps, run = v1.collect(target)
    rate_obs, rate_gaps, rate_run = check_recent_omo7d(target, since, previous_rate)
    observations.extend(rate_obs)
    gaps.extend(rate_gaps)

    out = dict(run)
    out.update({
        "collector_version": COLLECTOR_VERSION,
        "mode": "DAILY_INCREMENTAL",
        "incremental_since_date": since.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "omo7d_incremental_state_check": rate_run,
        "semantic_rules": {
            "daily_incremental_only": True,
            "default_recent_lookback_days": DEFAULT_LOOKBACK_DAYS,
            "no_2025_2024_backfill_in_daily_mode": True,
            "unchanged_policy_rate_emits_no_new_root_when_previous_state_supplied": True,
            "no_recent_quote_is_not_gap": True,
            "future_information_forbidden": True,
        },
    })
    return observations, gaps, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--since-date")
    ap.add_argument("--previous-rate", type=float)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    target = date.fromisoformat(a.date)
    since = date.fromisoformat(a.since_date) if a.since_date else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    obs, gaps, run = collect(target, since, a.previous_rate)
    for n, payload in (("observations.json", obs), ("gaps.json", gaps), ("run.json", run)):
        (out / n).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if gaps:
        print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
