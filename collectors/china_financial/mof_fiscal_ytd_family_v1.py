#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE-MOF-FISCAL-YTD-FAMILY-V1"
METHOD_VERSION = "FISC_YTD_PERIOD_FLOW_V1"
LIST_URL = "https://gks.mof.gov.cn/tongjishuju/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

ROOTS = {
    "FISC_GENERAL_REVENUE": "全国一般公共预算收入",
    "FISC_GENERAL_EXPENDITURE": "全国一般公共预算支出",
    "FISC_GOV_FUND_REVENUE": "全国政府性基金预算收入",
    "FISC_GOV_FUND_EXPENDITURE": "全国政府性基金预算支出",
}
FLOW_MAP = {
    "FISC_GENERAL_REVENUE": "FISC_GENERAL_REVENUE_PERIOD_FLOW",
    "FISC_GENERAL_EXPENDITURE": "FISC_GENERAL_EXPENDITURE_PERIOD_FLOW",
    "FISC_GOV_FUND_REVENUE": "FISC_GOV_FUND_REVENUE_PERIOD_FLOW",
    "FISC_GOV_FUND_EXPENDITURE": "FISC_GOV_FUND_EXPENDITURE_PERIOD_FLOW",
}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._buf).split())))
            self._href = None
            self._buf = []


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("p", "div", "li", "br", "tr", "td", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def fetch(url: str, attempts: int = 4) -> tuple[str, bytes, str, dict[str, Any]]:
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Connection": "close",
                },
            )
            with urlopen(req, timeout=45) as r:
                raw = r.read()
                for enc in ("utf-8", "gb18030"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = raw.decode("utf-8", "replace")
                return r.geturl(), raw, text, {"attempts_used": i + 1, "http_status": getattr(r, "status", 200)}
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
    assert last is not None
    raise last


def clean_text(html: str) -> str:
    p = TextParser()
    p.feed(html)
    return "\n".join(x.strip() for x in "".join(p.parts).splitlines() if x.strip())


def visible_publish_date(text: str) -> str | None:
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(?:来源[:：])", text)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def period_from_title(title: str, year: int) -> dict[str, Any] | None:
    if f"{year}年" not in title or "财政收支情况" not in title:
        return None
    if "1-2月" in title or "1—2月" in title or "1–2月" in title:
        return {"period_key": f"{year}-01_02_COMBINED", "period_end_month": 2, "period_kind": "JAN_FEB_COMBINED_YTD"}
    if "一季度" in title:
        return {"period_key": f"{year}-Q1", "period_end_month": 3, "period_kind": "YTD_QUARTER"}
    m = re.search(r"1[-—–](\d{1,2})月", title)
    if m:
        month = int(m.group(1))
        return {"period_key": f"{year}-01_{month:02d}_YTD", "period_end_month": month, "period_kind": "YTD"}
    if "上半年" in title:
        return {"period_key": f"{year}-H1", "period_end_month": 6, "period_kind": "YTD_HALF_YEAR"}
    return None


def exact_total(text: str, label: str) -> float:
    # Anchor on the national total phrase; stop before central/local subtotals.
    pattern = re.compile(re.escape(label) + r"\s*(\d+(?:\.\d+)?)\s*亿元")
    matches = pattern.findall(text)
    if not matches:
        raise ValueError(f"National total not found for {label}")
    # Duplicate rendering is acceptable only if the numeric value is identical.
    nums = {float(x) for x in matches}
    if len(nums) != 1:
        raise ValueError(f"Ambiguous multiple totals for {label}: {sorted(nums)}")
    return next(iter(nums))


def mk_root(series_id: str, period: dict[str, Any], value: float, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "reference_period": period["period_key"],
        "period_end_month": period["period_end_month"],
        "period_kind": period["period_kind"],
        "value": value,
        "unit": "CNY_100M",
        "provider": "Ministry of Finance of the People's Republic of China",
        "source_url": source["url"],
        "source_sha256": source["sha256"],
        "published_at": source["published_at"],
        "available_at": source["published_at"],
        "retrieved_at": source["retrieved_at"],
        "collector_version": COLLECTOR_VERSION,
        "vintage_policy": "ONE_RELEASE_ONE_OBSERVATION",
    }


def discover_releases(year: int, retrieved_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw, html, fetch_meta = fetch(LIST_URL)
    p = LinkParser(); p.feed(html)
    releases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, title in p.links:
        if not href or not title or href in seen:
            continue
        period = period_from_title(title, year)
        if period is None:
            continue
        seen.add(href)
        url = urljoin(final, href)
        article_final, article_raw, article_html, article_fetch = fetch(url)
        text = clean_text(article_html)
        published_at = visible_publish_date(text)
        if published_at is None:
            raise ValueError(f"Visible publication date missing: {title}")
        values = {sid: exact_total(text, label) for sid, label in ROOTS.items()}
        releases.append({
            **period,
            "title": title,
            "url": article_final,
            "sha256": hashlib.sha256(article_raw).hexdigest(),
            "published_at": published_at,
            "retrieved_at": retrieved_at,
            "fetch": article_fetch,
            "values": values,
        })
    releases.sort(key=lambda x: x["period_end_month"])
    return releases, {
        "list_url": final,
        "list_sha256": hashlib.sha256(raw).hexdigest(),
        "list_fetch": fetch_meta,
        "release_count": len(releases),
    }


def derive_period_flows(roots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sid: dict[str, list[dict[str, Any]]] = {}
    for row in roots:
        by_sid.setdefault(row["series_id"], []).append(row)
    out: list[dict[str, Any]] = []
    for parent_sid, flow_sid in FLOW_MAP.items():
        rows = sorted(by_sid.get(parent_sid, []), key=lambda x: x["period_end_month"])
        prev: dict[str, Any] | None = None
        for cur in rows:
            if cur["period_kind"] == "JAN_FEB_COMBINED_YTD":
                flow_value = cur["value"]
                flow_kind = "JAN_FEB_COMBINED_PERIOD_FLOW"
                parents = [cur["reference_period"]]
            else:
                if prev is None:
                    raise ValueError(f"No previous eligible YTD parent for {flow_sid} {cur['reference_period']}")
                flow_value = cur["value"] - prev["value"]
                flow_kind = "INCREMENTAL_PERIOD_FLOW"
                parents = [cur["reference_period"], prev["reference_period"]]
            out.append({
                "series_id": flow_sid,
                "reference_period": cur["reference_period"],
                "period_end_month": cur["period_end_month"],
                "period_kind": flow_kind,
                "value": flow_value,
                "unit": "CNY_100M",
                "provider": cur["provider"],
                "source_url": cur["source_url"],
                "source_sha256": cur["source_sha256"],
                "published_at": cur["published_at"],
                "available_at": cur["available_at"],
                "retrieved_at": cur["retrieved_at"],
                "collector_version": COLLECTOR_VERSION,
                "method_version": METHOD_VERSION,
                "parent_series_ids": [parent_sid],
                "parent_periods": parents,
                "lineage_rule": "CURRENT_ELIGIBLE_YTD_MINUS_PREVIOUS_ELIGIBLE_YTD; JAN_FEB_COMBINED_REMAINS_COMBINED",
            })
            prev = cur
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    roots: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    releases: list[dict[str, Any]] = []
    try:
        releases, evidence = discover_releases(args.year, retrieved_at)
        for rel in releases:
            for sid, value in rel["values"].items():
                roots.append(mk_root(sid, rel, value, rel))
        derived = derive_period_flows(roots)
        present_roots = {x["series_id"] for x in roots}
        present_derived = {x["series_id"] for x in derived}
        missing_roots = sorted(set(ROOTS) - present_roots)
        missing_flows = sorted(set(FLOW_MAP.values()) - present_derived)
        if missing_roots or missing_flows:
            gaps.append({"family": "MOF_FISCAL_YTD", "reason": "REQUIRED_SERIES_FAMILY_EMPTY", "missing_roots": missing_roots, "missing_flows": missing_flows})
    except Exception as e:
        gaps.append({"family": "MOF_FISCAL_YTD", "year": args.year, "reason": "SOURCE_OR_PARSER_FAILURE", "error": repr(e)})

    run = {
        "module": "china_financial_mof_fiscal_ytd_family",
        "collector_version": COLLECTOR_VERSION,
        "method_version": METHOD_VERSION,
        "year": args.year,
        "completed_at": retrieved_at,
        "status": "PASS" if not gaps else "INCOMPLETE",
        "release_count": len(releases),
        "root_observation_count": len(roots),
        "derived_observation_count": len(derived),
        "gap_count": len(gaps),
        "evidence": evidence,
        "semantic_rules": {
            "jan_feb_combined_not_split": True,
            "regular_period_is_current_ytd_minus_previous_ytd": True,
            "published_at_is_visible_official_release_date": True,
            "bounded_normal_retry_only": True,
            "unknown_is_never_zero": True,
        },
    }
    (out_dir / "observations.json").write_text(json.dumps(roots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "derived.json").write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "gaps.json").write_text(json.dumps(gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if not gaps else 2


if __name__ == "__main__":
    raise SystemExit(main())
