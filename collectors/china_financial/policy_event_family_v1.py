#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

SOURCES = {
    "OMO": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html",
    "BUYOUT": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/index.html",
    "MLF": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125437/125446/125873/index.html",
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


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.in_cell = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("td", "th"):
            self.in_cell = True
            self.cell = []
        elif tag in ("br", "p", "div", "li", "tr"):
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
            self.cell = []
        elif tag == "tr":
            if any(x for x in self.row):
                self.rows.append(self.row)
            self.row = []


def fetch(url: str) -> tuple[str, bytes, str]:
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
        return r.geturl(), raw, text


def clean_article(html: str) -> tuple[str, list[list[str]], str | None]:
    p = ArticleParser()
    p.feed(html)
    clean = "\n".join(x.strip() for x in "".join(p.text).splitlines() if x.strip())
    meta = re.search(r'<meta\s+name=["\']createDate["\']\s+content=["\']([^"\']+)', html, re.I)
    return clean, p.rows, meta.group(1) if meta else None


def parse_visible_date(text: str) -> date | None:
    m = re.search(r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def article_links(family: str, list_url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw, html = fetch(list_url)
    p = LinkParser()
    p.feed(html)
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, title in p.links:
        if not href or not title:
            continue
        full = urljoin(final, href)
        if full in seen:
            continue
        keep = False
        if family == "OMO":
            keep = "公开市场业务交易公告" in title and re.search(r"\[20\d{2}\]", title) is not None
        elif family == "BUYOUT":
            keep = "买断式逆回购" in title and "公告" in title
        elif family == "MLF":
            keep = "中期借贷便利" in title and "公告" in title and re.search(r"20\d{2}年\d{1,2}月", title) is not None
        if keep:
            seen.add(full)
            links.append({"title": title, "url": full})
    return links, {
        "list_url": final,
        "list_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_count": len(links),
    }


def amount_from_cny100m(text: str) -> float | None:
    text = text.replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*亿元", text)
    return float(m.group(1)) if m else None


def make_obs(series_id: str, ref_date: date, value: float, url: str, available_at: str | None,
             raw_sha256: str, dimensions: dict[str, Any] | None = None, unit: str = "CNY_100M") -> dict[str, Any]:
    return {
        "series_id": series_id,
        "reference_date": ref_date.isoformat(),
        "value": value,
        "unit": unit,
        "provider": "People's Bank of China",
        "source_url": url,
        "available_at": available_at,
        "collector_version": COLLECTOR_VERSION,
        "evidence_sha256": raw_sha256,
        "dimensions": dimensions or {},
    }


def parse_omo(target: date, url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw, html = fetch(url)
    text, rows, available_at = clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()
    observations: list[dict[str, Any]] = []

    # Article must actually refer to the target operation date.
    if target.isoformat() not in text and f"{target.year}年{target.month}月{target.day}日" not in text:
        return [], {"article_url": final, "status": "NOT_TARGET_OPERATION_DATE", "sha256": sha}

    table_rows = [r for r in rows if r and any("天" in c for c in r)]
    for row in table_rows:
        term = next((c for c in row if re.fullmatch(r"\d+天", c)), None)
        if not term:
            continue
        values = [amount_from_cny100m(c) for c in row]
        amounts = [v for v in values if v is not None]
        if not amounts:
            continue
        awarded = amounts[-1]
        if term == "7天":
            observations.append(make_obs(
                "POL_OMO_7D_AMOUNT", target, awarded, final, available_at, sha,
                {"tenor": term, "source_semantic": "PBOC_REVERSE_REPO_AWARDED_AMOUNT"},
            ))
        else:
            observations.append(make_obs(
                "POL_OMO_OTHER_AMOUNT", target, awarded, final, available_at, sha,
                {"tenor": term, "source_semantic": "PBOC_REVERSE_REPO_AWARDED_AMOUNT"},
            ))

    # Newer PBOC notices can carry additional tenors in prose outside the table.
    prose_patterns = [
        (r"开展了\s*(\d+(?:\.\d+)?)\s*亿元\s*隔夜逆回购操作", "隔夜"),
        (r"开展了\s*(\d+(?:\.\d+)?)\s*亿元\s*(\d+天)期?逆回购操作", None),
    ]
    existing = {(x["series_id"], x["dimensions"].get("tenor")) for x in observations}
    for pat, fixed_term in prose_patterns:
        for m in re.finditer(pat, text):
            amount = float(m.group(1))
            term = fixed_term or m.group(2)
            sid = "POL_OMO_7D_AMOUNT" if term == "7天" else "POL_OMO_OTHER_AMOUNT"
            key = (sid, term)
            if key in existing:
                continue
            observations.append(make_obs(
                sid, target, amount, final, available_at, sha,
                {"tenor": term, "source_semantic": "PBOC_REVERSE_REPO_OPERATION_AMOUNT_PROSE"},
            ))
            existing.add(key)

    return observations, {
        "article_url": final,
        "status": "PARSED",
        "sha256": sha,
        "available_at": available_at,
        "parsed_observation_count": len(observations),
    }


def parse_buyout(target: date, url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw, html = fetch(url)
    text, _, available_at = clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()
    cn_date = f"{target.year}年{target.month}月{target.day}日"
    if cn_date not in text:
        return [], {"article_url": final, "status": "NOT_TARGET_OPERATION_DATE", "sha256": sha}
    m = re.search(
        re.escape(cn_date) + r"[^。]{0,180}?开展\s*(\d+(?:\.\d+)?)\s*亿元买断式逆回购操作，期限为([^，。]+)",
        text,
    )
    if not m:
        raise ValueError("BUYOUT article matched target date but amount/tenor could not be parsed")
    amount = float(m.group(1))
    tenor_text = m.group(2).strip()
    d = re.search(r"（(\d+)天）", tenor_text)
    dims: dict[str, Any] = {"tenor_text": tenor_text, "source_semantic": "PBOC_BUYOUT_REVERSE_REPO_AMOUNT"}
    if d:
        dims["tenor_days"] = int(d.group(1))
    return [make_obs("POL_BUYOUT_REPO_AMOUNT", target, amount, final, available_at, sha, dims)], {
        "article_url": final,
        "status": "PARSED",
        "sha256": sha,
        "available_at": available_at,
        "parsed_observation_count": 1,
    }


def parse_mlf(target: date, url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw, html = fetch(url)
    text, _, available_at = clean_article(html)
    sha = hashlib.sha256(raw).hexdigest()
    cn_date = f"{target.year}年{target.month}月{target.day}日"
    if cn_date not in text:
        return [], {"article_url": final, "status": "NOT_TARGET_OPERATION_DATE", "sha256": sha}
    m = re.search(
        re.escape(cn_date) + r"[^。]{0,180}?开展\s*(\d+(?:\.\d+)?)\s*亿元MLF操作，期限为([^。]+)",
        text,
        re.I,
    )
    if not m:
        raise ValueError("MLF article matched target date but amount/tenor could not be parsed")
    amount = float(m.group(1))
    tenor = m.group(2).strip()
    return [make_obs(
        "POL_MLF_AMOUNT", target, amount, final, available_at, sha,
        {"tenor": tenor, "source_semantic": "PBOC_MLF_OPERATION_AMOUNT"},
    )], {
        "article_url": final,
        "status": "PARSED",
        "sha256": sha,
        "available_at": available_at,
        "parsed_observation_count": 1,
    }


def collect(target: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    family_runs: list[dict[str, Any]] = []
    parsers = {"OMO": parse_omo, "BUYOUT": parse_buyout, "MLF": parse_mlf}

    for family, list_url in SOURCES.items():
        try:
            links, list_evidence = article_links(family, list_url)
            # Operation notices are normally published same day or shortly before operation.
            # Inspect a bounded recent window; no-event is certified only if the list itself was fetched successfully.
            inspected = 0
            found = 0
            article_evidence: list[dict[str, Any]] = []
            for item in links[:40]:
                title = item["title"]
                pub_hint = parse_visible_date(title)
                # Titles often omit exact day for MLF, so do not filter MLF solely by title.
                if family != "MLF" and pub_hint is not None and abs((pub_hint - target).days) > 8:
                    continue
                inspected += 1
                try:
                    obs, ev = parsers[family](target, item["url"])
                    article_evidence.append(ev)
                    if obs:
                        observations.extend(obs)
                        found += len(obs)
                except Exception as e:
                    gaps.append({
                        "family": family,
                        "target_date": target.isoformat(),
                        "source_url": item["url"],
                        "reason": "ARTICLE_PARSE_FAILURE",
                        "error": repr(e),
                    })
            family_runs.append({
                "family": family,
                "status": "EVENTS_FOUND" if found else "NO_EVENT_CONFIRMED",
                "target_date": target.isoformat(),
                "list_evidence": list_evidence,
                "inspected_articles": inspected,
                "observation_count": found,
                "article_evidence": article_evidence,
            })
        except Exception as e:
            gaps.append({
                "family": family,
                "target_date": target.isoformat(),
                "source_url": list_url,
                "reason": "SOURCE_LIST_FAILURE",
                "error": repr(e),
            })
            family_runs.append({"family": family, "status": "SOURCE_FAILURE", "target_date": target.isoformat()})

    # Deduplicate exact event records while preserving distinct tenors.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for obs in observations:
        key = (
            obs["series_id"],
            obs["reference_date"],
            json.dumps(obs.get("dimensions", {}), sort_keys=True, ensure_ascii=False),
        )
        unique[key] = obs
    observations = sorted(unique.values(), key=lambda x: (x["series_id"], json.dumps(x.get("dimensions", {}), sort_keys=True, ensure_ascii=False)))

    run = {
        "module": "china_financial_policy_event_family",
        "collector_version": COLLECTOR_VERSION,
        "target_date": target.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "families": family_runs,
        "semantic_rules": {
            "scheduled_check_is_not_observation": True,
            "no_event_is_not_zero": True,
            "explicit_official_zero_may_be_observed": True,
            "missing_is_gap_not_zero": True,
        },
    }
    return observations, gaps, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Operation/reference date YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    target = date.fromisoformat(args.date)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    observations, gaps, run = collect(target)
    (out / "observations.json").write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "gaps.json").write_text(json.dumps(gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
