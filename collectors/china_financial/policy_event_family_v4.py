#!/usr/bin/env python3
"""PBOC policy-event collector V4: point-in-time OMO7D policy-rate state.

V4 preserves V1-V3 and changes the OMO7D rule from exact-target-day matching to
an as-of state rule:

* Find the latest official PBOC OMO notice at or before target_date that contains
  an explicit, parseable 7-day reverse-repo policy rate.
* Carry that official state across non-operation / non-quote days. No daily quote
  is required and absence of a target-day OMO notice is not a GAP.
* Never use an operation date after target_date (no look-ahead).
* reference_date is the official operation/quote date, not the daily run date.
* The immediately previous parseable 7-day quote is retained for change detection.
  A state-change flag is true only when those adjacent official quotes differ.

V1 amount/event collection remains unchanged and is composed with this state
collector. Older collector versions remain available for rollback/audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from collectors.china_financial import policy_event_family_v1 as v1
from collectors.china_financial import policy_event_family_v2 as v2

COLLECTOR_VERSION = "V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V4"
OMO_BASE = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/"
OMO_FIRST = urljoin(OMO_BASE, "index.html")
OMO_MODULE_ID = "17081"


def _omo_page_url(page: int) -> str:
    if page <= 1:
        return OMO_FIRST
    return urljoin(OMO_BASE, f"{OMO_MODULE_ID}-{page}.html")


def _extract_total_pages(html: str) -> int:
    m = re.search(r'name=["\']article_paging_list_hidden["\'][^>]*totalpage=["\'](\d+)', html, re.I)
    if not m:
        m = re.search(r"当前页:\s*.*?/\s*(\d+)", html, re.S)
    if not m:
        raise ValueError("PBC OMO total page count not found")
    return int(m.group(1))


def _page_links(url: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    final, raw, html = v1.fetch(url)
    parser = v1.LinkParser()
    parser.feed(html)
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, title in parser.links:
        if not href or not title:
            continue
        if "公开市场业务交易公告" not in title or re.search(r"\[20\d{2}\]", title) is None:
            continue
        full = urljoin(final, href)
        if full in seen:
            continue
        seen.add(full)
        ym = re.search(r"\[(20\d{2})\]", title)
        links.append({"title": title, "url": full, "title_year": int(ym.group(1)) if ym else None})
    return links, {
        "list_url": final,
        "list_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_count": len(links),
    }, _extract_total_pages(html)


def _operation_date(url: str) -> tuple[date | None, str, str, str | None]:
    final, raw, html = v1.fetch(url)
    text, _, available = v1.clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()
    patterns = [
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^。\n]{0,180}?(?:逆回购|公开市场)",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        r"(20\d{2})-(\d{1,2})-(\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), final, sha, available
        except ValueError:
            continue
    return None, final, sha, available


def collect_omo7d_state_asof(target: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    evidence_pages: list[dict[str, Any]] = []
    parsed_quotes: list[dict[str, Any]] = []
    inspected_articles = 0

    try:
        first_links, first_ev, total_pages = _page_links(OMO_FIRST)
        page_payloads: dict[int, tuple[list[dict[str, Any]], dict[str, Any]]] = {1: (first_links, first_ev)}
    except Exception as exc:
        gaps.append({
            "family": "OMO7D_RATE",
            "target_date": target.isoformat(),
            "source_url": OMO_FIRST,
            "reason": "SOURCE_LIST_FAILURE",
            "error": repr(exc),
        })
        return [], gaps, {
            "status": "INCOMPLETE",
            "target_date": target.isoformat(),
            "gap_count": len(gaps),
        }

    target_year_seen = False
    older_year_reached = False
    max_pages = total_pages

    for page in range(1, max_pages + 1):
        try:
            if page in page_payloads:
                links, page_ev = page_payloads[page]
            else:
                links, page_ev, _ = _page_links(_omo_page_url(page))
            evidence_pages.append({"page": page, **page_ev})
        except Exception as exc:
            gaps.append({
                "family": "OMO7D_RATE",
                "target_date": target.isoformat(),
                "source_url": _omo_page_url(page),
                "reason": "HISTORY_PAGE_FAILURE",
                "page": page,
                "error": repr(exc),
            })
            break

        page_years = [x["title_year"] for x in links if x.get("title_year") is not None]
        if page_years:
            if target.year in page_years:
                target_year_seen = True
            if max(page_years) < target.year:
                older_year_reached = True

        # Titles expose year and sequence, not exact operation date. Avoid fetching
        # future-year articles; exact date is established only from official body.
        candidates = [x for x in links if x.get("title_year") is None or x["title_year"] <= target.year]
        for item in candidates:
            try:
                op_date, final, sha, available = _operation_date(item["url"])
                inspected_articles += 1
                if op_date is None or op_date > target:
                    continue
                parsed = v2.parse_7d_rate_from_article(item["url"])
                if parsed is None:
                    continue
                parsed_quotes.append({
                    "date": op_date,
                    "title": item["title"],
                    "source_url": parsed["source_url"],
                    "available_at": parsed["available_at"],
                    "sha256": parsed["sha256"],
                    "selection": parsed["selection"],
                    "rate": float(parsed["rate"]),
                })
                parsed_quotes.sort(key=lambda x: x["date"], reverse=True)
                # Two adjacent official quotes at/before target are sufficient to
                # establish current state and whether the latest quote is a change.
                unique_dates = []
                for q in parsed_quotes:
                    if q["date"] not in unique_dates:
                        unique_dates.append(q["date"])
                if len(unique_dates) >= 2:
                    break
            except Exception as exc:
                # A single malformed historical article is diagnostic; fail closed
                # only if no valid as-of state can ultimately be established.
                gaps.append({
                    "family": "OMO7D_RATE_DIAGNOSTIC",
                    "target_date": target.isoformat(),
                    "source_url": item["url"],
                    "reason": "ARTICLE_PARSE_FAILURE",
                    "error": repr(exc),
                    "non_blocking_if_state_established": True,
                })

        unique_dates = []
        for q in sorted(parsed_quotes, key=lambda x: x["date"], reverse=True):
            if q["date"] not in unique_dates:
                unique_dates.append(q["date"])
        if len(unique_dates) >= 2:
            break
        if target_year_seen and older_year_reached and not parsed_quotes:
            # Continue into prior year because a policy rate can be unchanged for a
            # long time; do not stop merely because target-year pages had no quote.
            continue

    parsed_quotes.sort(key=lambda x: x["date"], reverse=True)
    # Deduplicate multiple links/quotes on the same operation date and validate rate.
    by_date: dict[date, list[dict[str, Any]]] = {}
    for q in parsed_quotes:
        by_date.setdefault(q["date"], []).append(q)

    valid_dates = sorted(by_date.keys(), reverse=True)
    if not valid_dates:
        blocking = {
            "family": "OMO7D_RATE",
            "target_date": target.isoformat(),
            "reason": "ASOF_STATE_NOT_ESTABLISHED",
            "message": "No official parseable 7-day reverse-repo policy-rate quote was established at or before target_date.",
        }
        gaps.append(blocking)
        return [], gaps, {
            "status": "INCOMPLETE",
            "target_date": target.isoformat(),
            "total_pages": total_pages,
            "pages_inspected": len(evidence_pages),
            "articles_inspected": inspected_articles,
            "gap_count": len(gaps),
        }

    current_date = valid_dates[0]
    current_rows = by_date[current_date]
    current_rates = sorted({round(float(x["rate"]), 10) for x in current_rows})
    if len(current_rates) != 1:
        gaps.append({
            "family": "OMO7D_RATE",
            "target_date": target.isoformat(),
            "reference_date": current_date.isoformat(),
            "reason": "ASOF_RATE_CONFLICT",
            "rates": current_rates,
        })
        return [], gaps, {"status": "INCOMPLETE", "target_date": target.isoformat()}

    current = current_rows[0]
    previous = None
    if len(valid_dates) >= 2:
        previous_rows = by_date[valid_dates[1]]
        prev_rates = sorted({round(float(x["rate"]), 10) for x in previous_rows})
        if len(prev_rates) == 1:
            previous = previous_rows[0]

    dims: dict[str, Any] = {
        "source_semantic": "PBOC_7D_REVERSE_REPO_POLICY_RATE_ASOF_STATE",
        "as_of_target_date": target.isoformat(),
        "state_quote_date": current_date.isoformat(),
        "selection_rule": current["selection"],
        "point_in_time_safe": True,
        "target_day_quote_required": False,
        "state_carried_forward": current_date < target,
    }
    if previous is not None:
        change_bps = round((float(current["rate"]) - float(previous["rate"])) * 100.0, 8)
        dims.update({
            "previous_quote_date": previous["date"].isoformat(),
            "previous_rate": float(previous["rate"]),
            "change_bps_vs_previous_quote": change_bps,
            "state_change_detected_on_latest_quote": abs(change_bps) > 1e-9,
        })

    observations.append({
        "series_id": "POL_OMO7D_RATE",
        "reference_date": current_date.isoformat(),
        "value": float(current["rate"]),
        "unit": "percent",
        "provider": "People's Bank of China",
        "source_url": current["source_url"],
        "available_at": current["available_at"],
        "collector_version": COLLECTOR_VERSION,
        "evidence_sha256": current["sha256"],
        "dimensions": dims,
    })

    # Historical article diagnostics do not make a valid state incomplete. Preserve
    # them in run diagnostics while removing them from blocking GAP output.
    diagnostics = [g for g in gaps if g.get("non_blocking_if_state_established")]
    gaps = [g for g in gaps if not g.get("non_blocking_if_state_established")]
    return observations, gaps, {
        "status": "PASS" if not gaps else "INCOMPLETE",
        "target_date": target.isoformat(),
        "state_reference_date": current_date.isoformat(),
        "current_rate": float(current["rate"]),
        "state_carried_forward": current_date < target,
        "previous_quote_date": previous["date"].isoformat() if previous else None,
        "previous_rate": float(previous["rate"]) if previous else None,
        "pages_inspected": len(evidence_pages),
        "articles_inspected": inspected_articles,
        "total_pages": total_pages,
        "diagnostics": diagnostics,
        "page_evidence": evidence_pages,
        "semantic_rules": {
            "as_of_latest_official_state": True,
            "target_day_quote_required": False,
            "future_operation_dates_forbidden": True,
            "reference_date_is_official_quote_date": True,
            "no_event_or_weekend_is_not_gap": True,
        },
    }


def collect(target: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    observations, gaps, run = v1.collect(target)
    rate_obs, rate_gaps, rate_run = collect_omo7d_state_asof(target)
    observations.extend(rate_obs)
    gaps.extend(rate_gaps)

    out = dict(run)
    out.update({
        "collector_version": COLLECTOR_VERSION,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "omo7d_rate_state": rate_run,
    })
    return observations, gaps, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="As-of date YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    target = date.fromisoformat(args.date)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    observations, gaps, run = collect(target)
    for name, payload in (("observations.json", observations), ("gaps.json", gaps), ("run.json", run)):
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if gaps:
        print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
