#!/usr/bin/env python3
"""PBOC broad RRR collector V4.

V4 preserves V1-V3 and fixes a critical vintage bug found by regression:
monetary-policy chronology/review articles can republish an old RRR event months
later. They are useful QC evidence but MUST NOT create a new Root event.

Production root candidates are therefore restricted to direct PBOC decision /
announcement titles about changing financial-institution reserve requirements.
The list publication date is the announcement reference date. Historical review
articles are excluded from Root generation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from collectors.china_financial import rrr_event_family_v3 as v3
from collectors.china_financial.policy_event_family_v1 import fetch, clean_article

COLLECTOR_VERSION = "V1.9-CANDIDATE-RRR-EVENT-FAMILY-V4"
MAX_OLDER_SCAN_PAGES = 45


def _direct_rrr_title(title: str) -> bool:
    if "货币政策大事记" in title or "执行报告" in title or "回顾" in title:
        return False
    return (
        "存款准备金率" in title
        and any(k in title for k in ("决定下调", "决定降低", "决定上调", "下调金融机构", "上调金融机构"))
    )


def _effective_date(text: str, announcement: date) -> date | None:
    for pat in (
        r"(?:自|从)\s*(20\d{2})年(\d{1,2})月(\d{1,2})日(?:起)?",
        r"(?:自|从)\s*(\d{1,2})月(\d{1,2})日(?:起)?",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^。]{0,80}?(?:下调|上调|降低|提高).*?存款准备金率",
    ):
        m = re.search(pat, text)
        if not m:
            continue
        try:
            if len(m.groups()) == 3:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return date(announcement.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def _parse_direct(url: str, announcement: date) -> dict[str, Any] | None:
    final, raw, html = fetch(url)
    text, _, available = clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()

    broad: list[tuple[float, str]] = []
    for pat, sign in (
        (r"(?:下调|降低)(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点", -1),
        (r"(?:上调|提高)(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点", 1),
        (r"(?:金融机构|存款类金融机构)[^。]{0,120}?存款准备金率[^。]{0,80}?(?:下调|降低)\s*(\d+(?:\.\d+)?)\s*个百分点", -1),
    ):
        for m in re.finditer(pat, text):
            broad.append((round(sign * float(m.group(1)) * 100.0, 8), m.group(0)))

    vals = sorted({x[0] for x in broad})
    if not vals:
        return None
    if len(vals) != 1:
        raise ValueError(f"ambiguous broad RRR changes in direct announcement: {vals}")
    matched = next(x[1] for x in broad if x[0] == vals[0])

    targeted = []
    for m in re.finditer(r"([^。]{0,100}(?:汽车金融公司|金融租赁公司|农村信用社|农村商业银行|村镇银行)[^。]{0,160}?存款准备金率[^。]{0,100})", text):
        targeted.append(m.group(1))

    eff = _effective_date(text, announcement)
    return {
        "series_id": "POL_RRR_CHANGE_BPS",
        "reference_date": announcement.isoformat(),
        "value": vals[0],
        "unit": "bp",
        "provider": "People's Bank of China",
        "source_url": final,
        "available_at": available,
        "collector_version": COLLECTOR_VERSION,
        "evidence_sha256": sha,
        "dimensions": {
            "source_semantic": "PBOC_DIRECT_BROAD_REQUIRED_RESERVE_RATIO_CHANGE_ASOF_EVENT",
            "announcement_date": announcement.isoformat(),
            "effective_date": eff.isoformat() if eff else None,
            "matched_text": matched,
            "targeted_adjustment_context": targeted[:10],
            "broad_rrr_only": True,
            "historical_review_articles_excluded_from_root": True,
        },
    }


def collect(target: date):
    obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    page_evidence: list[dict[str, Any]] = []
    try:
        start, total, locate_ev = v3._locate_page_for_date(target)
        page_evidence.extend(locate_ev)
    except Exception as exc:
        gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"source_url":v3.FIRST,"reason":"HISTORY_LOCALIZATION_FAILURE","error":repr(exc)})
        return obs,gaps,{"status":"INCOMPLETE","target_date":target.isoformat()}

    found = None; pages_scanned = 0
    for page_no in range(start, min(total, start + MAX_OLDER_SCAN_PAGES - 1) + 1):
        try:
            p = v3._page(page_no); pages_scanned += 1
            page_evidence.append({k:(v.isoformat() if isinstance(v,date) else v) for k,v in p.items() if k!='rows'})
        except Exception as exc:
            diagnostics.append({"page":page_no,"reason":"PAGE_FETCH_FAILURE","error":repr(exc)}); continue
        for row in p["rows"]:
            d=row.get("list_date")
            if d is None or d>target or not _direct_rrr_title(row["title"]):
                continue
            try:
                parsed=_parse_direct(row["url"],d)
                if parsed is not None:
                    found=parsed;break
            except Exception as exc:
                diagnostics.append({"source_url":row["url"],"title":row["title"],"reason":"DIRECT_ARTICLE_PARSE_FAILURE","error":repr(exc)})
        if found is not None:break

    if found is None:
        gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"reason":"ASOF_DIRECT_BROAD_RRR_EVENT_NOT_ESTABLISHED","pages_scanned":pages_scanned,"max_older_scan_pages":MAX_OLDER_SCAN_PAGES})
    else:
        found["dimensions"].update({"as_of_target_date":target.isoformat(),"state_carried_forward":found["reference_date"]<target.isoformat(),"point_in_time_safe":True,"target_day_announcement_required":False})
        obs.append(found)

    run={
        "module":"china_financial_rrr_event_family",
        "collector_version":COLLECTOR_VERSION,
        "target_date":target.isoformat(),
        "completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        "status":"PASS" if not gaps else "INCOMPLETE",
        "observation_count":len(obs),"gap_count":len(gaps),
        "start_page":start,"total_pages":total,"pages_scanned_after_localization":pages_scanned,
        "diagnostics":diagnostics,"page_evidence":page_evidence,
        "semantic_rules":{"as_of_latest_official_broad_rrr_event":True,"direct_policy_announcement_only_for_root":True,"chronology_review_is_qc_only":True,"target_day_announcement_required":False,"future_list_rows_forbidden":True,"announcement_date_is_reference_date":True,"effective_date_separate":True,"targeted_adjustments_do_not_replace_broad_rrr":True,"bounded_history_scan":MAX_OLDER_SCAN_PAGES}
    }
    return obs,gaps,run


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    target=date.fromisoformat(a.date);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(target)
    for n,payload in (("observations.json",obs),("gaps.json",gaps),("run.json",run)):(out/n).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));
    if gaps:print(json.dumps(gaps,ensure_ascii=False,indent=2))
    return 0 if run['status']=='PASS' else 2

if __name__=='__main__':raise SystemExit(main())
