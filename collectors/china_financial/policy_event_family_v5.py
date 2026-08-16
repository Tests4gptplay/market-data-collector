#!/usr/bin/env python3
"""PBOC policy-event collector V5: efficient point-in-time OMO7D policy-rate state.

Preserves V1-V4. V5 keeps the approved as-of semantics from V4 but removes the
expensive double-fetch of each OMO article. Each official article is fetched
once, operation date and 7-day rate are parsed from the same payload, and the
history scan stops as soon as two valid quote dates at/before target are known.

Rules:
- latest official parseable 7D reverse-repo policy-rate quote <= target_date;
- no target-day quote required; weekend/non-operation days carry prior state;
- no future article/operation date may influence historical state;
- reference_date is the official quote/operation date;
- older versions remain untouched for audit/rollback.
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

COLLECTOR_VERSION = "V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V5"
OMO_BASE = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/"
OMO_FIRST = urljoin(OMO_BASE, "index.html")
OMO_MODULE_ID = "17081"


def _page_url(page: int) -> str:
    return OMO_FIRST if page <= 1 else urljoin(OMO_BASE, f"{OMO_MODULE_ID}-{page}.html")


def _total_pages(html: str) -> int:
    m = re.search(r'name=["\']article_paging_list_hidden["\'][^>]*totalpage=["\'](\d+)', html, re.I)
    if not m:
        raise ValueError("PBC OMO totalpage missing")
    return int(m.group(1))


def _list_page(page: int) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    final, raw, html = v1.fetch(_page_url(page))
    p = v1.LinkParser(); p.feed(html)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, title in p.links:
        if not href or not title or "公开市场业务交易公告" not in title:
            continue
        ym = re.search(r"\[(20\d{2})\]", title)
        if not ym:
            continue
        full = urljoin(final, href)
        if full in seen:
            continue
        seen.add(full)
        rows.append({"title": title, "url": full, "title_year": int(ym.group(1))})
    return rows, {
        "page": page,
        "url": final,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_count": len(rows),
    }, _total_pages(html)


def _num(s: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)", s.replace(",", ""))
    return float(m.group(1)) if m else None


def _is_7d(s: str) -> bool:
    x = re.sub(r"\s+", "", s)
    return x in {"7天", "7天期"} or ("7天" in x and "逆回购" in x)


def _parse_article_once(url: str) -> dict[str, Any]:
    final, raw, html = v1.fetch(url)
    text, rows, available = v1.clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()

    op_date = None
    for pat in (
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^。\n]{0,200}?(?:逆回购|公开市场)",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        r"(20\d{2})-(\d{1,2})-(\d{1,2})",
    ):
        m = re.search(pat, text)
        if m:
            try:
                op_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                pass

    candidates: list[tuple[float, str]] = []
    rate_col: int | None = None
    for row in rows:
        norm = [re.sub(r"\s+", "", c) for c in row]
        for i, c in enumerate(norm):
            if "操作利率" in c or "中标利率" in c or c == "利率":
                rate_col = i
        if not any(_is_7d(c) for c in row):
            continue
        rate = None; selection = None
        if rate_col is not None and rate_col < len(row):
            n = _num(row[rate_col])
            if n is not None and 0.1 <= n <= 10:
                rate = n; selection = f"table_rate_column_{rate_col}"
        if rate is None:
            pct = [(i, _num(c)) for i, c in enumerate(row) if ("%" in c or "％" in c) and _num(c) is not None]
            plausible = [(i, n) for i, n in pct if n is not None and 0.1 <= n <= 10]
            if len(plausible) == 1:
                rate = float(plausible[0][1]); selection = f"unique_percent_cell_{plausible[0][0]}"
        if rate is None:
            ti = next((i for i, c in enumerate(row) if _is_7d(c)), None)
            plausible = []
            for i, c in enumerate(row):
                if i == ti:
                    continue
                n = _num(c)
                if n is not None and 0.1 <= n <= 10:
                    plausible.append((i, n))
            # Headerless newer tables can include tenor numeric 7; exclude values
            # that are obviously tenor/amount by requiring a unique plausible rate.
            if len(plausible) == 1:
                rate = plausible[0][1]; selection = f"unique_plausible_rate_cell_{plausible[0][0]}"
        if rate is not None:
            candidates.append((round(float(rate), 10), selection or "table"))

    for pat in (
        r"7天(?:期)?逆回购[^。；\n]{0,100}?(?:操作利率|中标利率)[为：:]?\s*(\d+(?:\.\d+)?)\s*[%％]",
        r"(?:操作利率|中标利率)[为：:]?\s*(\d+(?:\.\d+)?)\s*[%％][^。；\n]{0,100}?7天(?:期)?逆回购",
    ):
        for m in re.finditer(pat, text):
            candidates.append((round(float(m.group(1)), 10), "explicit_prose"))

    rates = sorted({x[0] for x in candidates})
    if len(rates) > 1:
        raise ValueError(f"ambiguous 7D rates {rates}")
    return {
        "date": op_date,
        "rate": rates[0] if rates else None,
        "selection": next((x[1] for x in candidates if x[0] == rates[0]), None) if rates else None,
        "source_url": final,
        "available_at": available,
        "sha256": sha,
    }


def collect_omo7d_state_asof(target: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    blocking_gaps: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    quotes: dict[date, list[dict[str, Any]]] = {}
    page_evidence: list[dict[str, Any]] = []
    article_count = 0

    try:
        first_rows, first_ev, total = _list_page(1)
    except Exception as exc:
        blocking_gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"SOURCE_LIST_FAILURE","source_url":OMO_FIRST,"error":repr(exc)})
        return [], blocking_gaps, {"status":"INCOMPLETE","target_date":target.isoformat()}

    for page in range(1, total + 1):
        try:
            rows, ev, _ = (first_rows, first_ev, total) if page == 1 else _list_page(page)
            page_evidence.append(ev)
        except Exception as exc:
            blocking_gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"HISTORY_PAGE_FAILURE","page":page,"source_url":_page_url(page),"error":repr(exc)})
            break

        for item in rows:
            if item["title_year"] > target.year:
                continue
            try:
                parsed = _parse_article_once(item["url"]); article_count += 1
                d = parsed["date"]
                if d is None or d > target or parsed["rate"] is None:
                    continue
                quotes.setdefault(d, []).append({"title":item["title"], **parsed})
            except Exception as exc:
                diagnostics.append({"source_url":item["url"],"reason":"ARTICLE_PARSE_FAILURE","error":repr(exc)})

            valid_dates = sorted(quotes.keys(), reverse=True)
            if len(valid_dates) >= 2:
                break
        if len(quotes) >= 2:
            break

        # Once we have crossed into a year older than target and still have no quote,
        # continue because the state may legitimately have been unchanged for months.

    valid_dates = sorted(quotes.keys(), reverse=True)
    if not valid_dates:
        blocking_gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"ASOF_STATE_NOT_ESTABLISHED"})
        return [], blocking_gaps, {"status":"INCOMPLETE","target_date":target.isoformat(),"pages_inspected":len(page_evidence),"articles_inspected":article_count,"diagnostics":diagnostics}

    current_date = valid_dates[0]
    current_rates = sorted({round(float(x["rate"]), 10) for x in quotes[current_date]})
    if len(current_rates) != 1:
        blocking_gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reference_date":current_date.isoformat(),"reason":"ASOF_RATE_CONFLICT","rates":current_rates})
        return [], blocking_gaps, {"status":"INCOMPLETE","target_date":target.isoformat()}
    current = quotes[current_date][0]
    previous = None
    if len(valid_dates) >= 2:
        prev_date = valid_dates[1]
        prev_rates = sorted({round(float(x["rate"]), 10) for x in quotes[prev_date]})
        if len(prev_rates) == 1:
            previous = quotes[prev_date][0]

    dims: dict[str, Any] = {
        "source_semantic":"PBOC_7D_REVERSE_REPO_POLICY_RATE_ASOF_STATE",
        "as_of_target_date":target.isoformat(),
        "state_quote_date":current_date.isoformat(),
        "state_carried_forward":current_date < target,
        "target_day_quote_required":False,
        "point_in_time_safe":True,
        "selection_rule":current["selection"],
    }
    if previous is not None:
        change = round((float(current["rate"]) - float(previous["rate"])) * 100.0, 8)
        dims.update({"previous_quote_date":previous["date"].isoformat(),"previous_rate":float(previous["rate"]),"change_bps_vs_previous_quote":change,"state_change_detected_on_latest_quote":abs(change)>1e-9})

    obs = [{
        "series_id":"POL_OMO7D_RATE",
        "reference_date":current_date.isoformat(),
        "value":float(current["rate"]),
        "unit":"percent",
        "provider":"People's Bank of China",
        "source_url":current["source_url"],
        "available_at":current["available_at"],
        "collector_version":COLLECTOR_VERSION,
        "evidence_sha256":current["sha256"],
        "dimensions":dims,
    }]
    return obs, blocking_gaps, {
        "status":"PASS" if not blocking_gaps else "INCOMPLETE",
        "target_date":target.isoformat(),
        "state_reference_date":current_date.isoformat(),
        "current_rate":float(current["rate"]),
        "state_carried_forward":current_date < target,
        "previous_quote_date":previous["date"].isoformat() if previous else None,
        "previous_rate":float(previous["rate"]) if previous else None,
        "pages_inspected":len(page_evidence),
        "articles_inspected":article_count,
        "total_pages":total,
        "diagnostics":diagnostics,
        "page_evidence":page_evidence,
        "semantic_rules":{"as_of_latest_official_state":True,"target_day_quote_required":False,"future_information_forbidden":True,"no_event_or_weekend_is_not_gap":True,"one_http_fetch_per_article":True},
    }


def collect(target: date):
    observations, gaps, run = v1.collect(target)
    rate_obs, rate_gaps, rate_run = collect_omo7d_state_asof(target)
    observations.extend(rate_obs); gaps.extend(rate_gaps)
    out = dict(run)
    out.update({"collector_version":COLLECTOR_VERSION,"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"PASS" if not gaps else "INCOMPLETE","observation_count":len(observations),"gap_count":len(gaps),"omo7d_rate_state":rate_run})
    return observations, gaps, out


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    target=date.fromisoformat(a.date); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(target)
    for n,payload in (("observations.json",obs),("gaps.json",gaps),("run.json",run)):
        (out/n).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));
    if gaps: print(json.dumps(gaps,ensure_ascii=False,indent=2))
    return 0 if run['status']=='PASS' else 2

if __name__=='__main__':
    raise SystemExit(main())
