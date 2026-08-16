#!/usr/bin/env python3
"""PBOC broad RRR collector V3: point-in-time latest official broad change.

Preserves V1/V2. V3 implements the approved low-frequency state/event rule:
- Do not require an RRR announcement on target_date.
- Find the latest official broad financial-institution RRR change whose official
  list publication date is <= target_date.
- Use the true announcement/publication date as reference_date.
- Keep effective_date separately when stated by PBOC.
- Targeted/special-institution adjustments never replace the broad RRR root.
- Never use a list row or article published after target_date (no look-ahead).
- Historical lookup uses bounded page-date binary localization, then scans only
  toward older pages until a valid broad RRR event is established.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from collectors.china_financial.policy_event_family_v1 import fetch, clean_article

COLLECTOR_VERSION = "V1.9-CANDIDATE-RRR-EVENT-FAMILY-V3"
BASE = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/"
FIRST = urljoin(BASE, "index.html")
MODULE_ID = "11040"
MAX_OLDER_SCAN_PAGES = 45


class LP(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str | None, str]] = []
        self.href: str | None = None
        self.buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.buf = []

    def handle_data(self, data):
        if self.href is not None:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, " ".join("".join(self.buf).split())))
            self.href = None
            self.buf = []


def _page_url(page: int) -> str:
    return FIRST if page <= 1 else urljoin(BASE, f"{MODULE_ID}-{page}.html")


def _cn_date(text: str) -> date | None:
    for pat in (r"(20\d{2})年(\d{1,2})月(\d{1,2})日", r"(20\d{2})-(\d{1,2})-(\d{1,2})"):
        m = re.search(pat, text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


def _page(page: int) -> dict[str, Any]:
    final, raw, html = fetch(_page_url(page))
    m = re.search(r'name=["\']article_paging_list_hidden["\'][^>]*totalpage=["\'](\d+)', html, re.I)
    if not m:
        raise ValueError("PBC news totalpage not found")
    total = int(m.group(1))

    # PBC list page visibly prints YYYY-MM-DD immediately around each article row.
    plain = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    dates: list[date] = []
    for y, mo, d in re.findall(r"(20\d{2})-(\d{1,2})-(\d{1,2})", plain):
        try:
            dates.append(date(int(y), int(mo), int(d)))
        except ValueError:
            pass

    parser = LP(); parser.feed(html)
    rows: list[dict[str, Any]] = []
    # Pair each relevant anchor with nearest visible list date in surrounding source.
    for href, title in parser.links:
        if not href or not title:
            continue
        if not any(k in title for k in ("存款准备金率", "降准", "货币政策大事记")):
            continue
        pos = html.find(href)
        ctx = html[max(0, pos - 450): pos + 1500] if pos >= 0 else ""
        ctx_plain = " ".join(re.sub(r"<[^>]+>", " ", ctx).split())
        visible = None
        dm = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", ctx_plain)
        if dm:
            try:
                visible = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
            except ValueError:
                pass
        rows.append({"title": title, "url": urljoin(final, href), "list_date": visible})

    return {
        "page": page,
        "url": final,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "total_pages": total,
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
        "rows": rows,
    }


def _locate_page_for_date(target: date) -> tuple[int, int, list[dict[str, Any]]]:
    first = _page(1)
    total = int(first["total_pages"])
    evidence = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in first.items() if k != "rows"}]
    if first["min_date"] is not None and target >= first["min_date"]:
        return 1, total, evidence

    lo, hi = 1, total
    best = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        p = first if mid == 1 else _page(mid)
        evidence.append({k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items() if k != "rows"})
        mn, mx = p["min_date"], p["max_date"]
        if mn is None or mx is None:
            # Fail-safe: shrink toward newer half rather than pretending location.
            hi = mid - 1
            continue
        if target > mx:
            hi = mid - 1
            best = max(1, mid - 1)
        elif target < mn:
            lo = mid + 1
            best = mid
        else:
            return mid, total, evidence
    return min(max(best, 1), total), total, evidence


def _parse_broad_rrr(url: str, announcement_date: date) -> dict[str, Any] | None:
    final, raw, html = fetch(url)
    text, _, available = clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()

    patterns = [
        (r"(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,160}?下调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点", -1),
        (r"(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,160}?上调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点", 1),
        (r"(?:金融机构|存款类金融机构)[^。]{0,120}?存款准备金率[^。]{0,80}?下调\s*(\d+(?:\.\d+)?)\s*个百分点", -1),
    ]
    hits: list[dict[str, Any]] = []
    for pat, sign in patterns:
        for m in re.finditer(pat, text):
            groups = m.groups()
            if len(groups) >= 2:
                eff = _cn_date(groups[0] or "")
                amount = float(groups[1])
            else:
                eff = None
                amount = float(groups[0])
            hits.append({"bps": round(sign * amount * 100.0, 8), "effective_date": eff, "matched": m.group(0)})

    # A chronology article may phrase the event as "降低...0.5个百分点".
    if not hits:
        for m in re.finditer(r"(?:降低|下调)[^。]{0,80}?(?:金融机构|存款类金融机构)[^。]{0,100}?存款准备金率[^。]{0,50}?(\d+(?:\.\d+)?)\s*个百分点", text):
            hits.append({"bps": -float(m.group(1)) * 100.0, "effective_date": None, "matched": m.group(0)})

    values = sorted({round(float(h["bps"]), 8) for h in hits})
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"ambiguous broad RRR changes: {values}")
    hit = next(h for h in hits if round(float(h["bps"]), 8) == values[0])

    targeted = []
    for m in re.finditer(r"([^。]{0,100}(?:汽车金融公司|金融租赁公司|农村信用社|农村商业银行|村镇银行)[^。]{0,140}?存款准备金率[^。]{0,100})", text):
        targeted.append(m.group(1))

    return {
        "series_id": "POL_RRR_CHANGE_BPS",
        "reference_date": announcement_date.isoformat(),
        "value": values[0],
        "unit": "bp",
        "provider": "People's Bank of China",
        "source_url": final,
        "available_at": available,
        "collector_version": COLLECTOR_VERSION,
        "evidence_sha256": sha,
        "dimensions": {
            "source_semantic": "PBOC_BROAD_REQUIRED_RESERVE_RATIO_CHANGE_ASOF_EVENT",
            "announcement_date": announcement_date.isoformat(),
            "effective_date": hit["effective_date"].isoformat() if hit["effective_date"] else None,
            "matched_text": hit["matched"],
            "targeted_adjustment_context": targeted[:10],
            "broad_rrr_only": True,
        },
    }


def collect(target: date):
    obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []

    try:
        start, total, locate_ev = _locate_page_for_date(target)
        page_evidence.extend(locate_ev)
    except Exception as exc:
        gaps.append({"family": "RRR_EVENT", "target_date": target.isoformat(), "source_url": FIRST, "reason": "HISTORY_LOCALIZATION_FAILURE", "error": repr(exc)})
        return obs, gaps, {"status": "INCOMPLETE", "target_date": target.isoformat(), "gap_count": len(gaps)}

    found = None
    pages_scanned = 0
    for page_no in range(start, min(total, start + MAX_OLDER_SCAN_PAGES - 1) + 1):
        try:
            p = _page(page_no)
            pages_scanned += 1
            page_evidence.append({k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items() if k != "rows"})
        except Exception as exc:
            diagnostics.append({"page": page_no, "reason": "PAGE_FETCH_FAILURE", "error": repr(exc)})
            continue

        # Newest-to-oldest within page as rendered by PBC.
        for row in p["rows"]:
            d = row.get("list_date")
            if d is None:
                diagnostics.append({"source_url": row["url"], "reason": "LIST_DATE_MISSING", "title": row["title"]})
                continue
            if d > target:
                continue
            try:
                parsed = _parse_broad_rrr(row["url"], d)
                if parsed is not None:
                    found = parsed
                    break
            except Exception as exc:
                diagnostics.append({"source_url": row["url"], "reason": "ARTICLE_PARSE_FAILURE", "title": row["title"], "error": repr(exc)})
        if found is not None:
            break

    if found is None:
        gaps.append({
            "family": "RRR_EVENT",
            "target_date": target.isoformat(),
            "reason": "ASOF_BROAD_RRR_EVENT_NOT_ESTABLISHED",
            "pages_scanned": pages_scanned,
            "max_older_scan_pages": MAX_OLDER_SCAN_PAGES,
        })
    else:
        found["dimensions"].update({
            "as_of_target_date": target.isoformat(),
            "state_carried_forward": found["reference_date"] < target.isoformat(),
            "point_in_time_safe": True,
            "target_day_announcement_required": False,
        })
        obs.append(found)

    run = {
        "module": "china_financial_rrr_event_family",
        "collector_version": COLLECTOR_VERSION,
        "target_date": target.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(obs),
        "gap_count": len(gaps),
        "start_page": start,
        "total_pages": total,
        "pages_scanned_after_localization": pages_scanned,
        "diagnostics": diagnostics,
        "page_evidence": page_evidence,
        "semantic_rules": {
            "as_of_latest_official_broad_rrr_event": True,
            "target_day_announcement_required": False,
            "future_list_rows_forbidden": True,
            "announcement_date_is_reference_date": True,
            "effective_date_separate": True,
            "targeted_adjustments_do_not_replace_broad_rrr": True,
            "bounded_history_scan": MAX_OLDER_SCAN_PAGES,
        },
    }
    return obs, gaps, run


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", required=True); ap.add_argument("--out", required=True); a = ap.parse_args()
    target = date.fromisoformat(a.date); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    obs, gaps, run = collect(target)
    for n, payload in (("observations.json", obs), ("gaps.json", gaps), ("run.json", run)):
        (out / n).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    if gaps: print(json.dumps(gaps, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
