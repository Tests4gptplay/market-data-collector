#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE-PBC-MONTHLY-CREDIT-FAMILY-V1"
STATS_INDEX = "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"


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


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_cell = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("td", "th"):
            self.in_cell = True
            self.cell = []
        elif tag.lower() == "br" and self.in_cell:
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self.in_cell:
            self.row.append(" ".join("".join(self.cell).replace("\xa0", " ").split()))
            self.in_cell = False
            self.cell = []
        elif tag == "tr":
            if any(c.strip() for c in self.row):
                self.rows.append(self.row)
            self.row = []


def fetch(url: str) -> tuple[str, bytes, str]:
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Connection": "close"})
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


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("\xa0", ""))


def parse_num(s: str) -> float | None:
    t = s.replace(",", "").replace("，", "").strip()
    if not t or t in {"-", "—", "--", "　"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def discover_year_sections(year: int) -> dict[str, str]:
    final, _, html = fetch(STATS_INDEX)
    p = LinkParser(); p.feed(html)
    active_year: int | None = None
    out: dict[str, str] = {}
    for href, title in p.links:
        m = re.fullmatch(r"(20\d{2})年统计数据", title)
        if m:
            active_year = int(m.group(1))
            continue
        if active_year != year or not href:
            continue
        if title == "金融机构信贷收支统计" and "credit" not in out:
            out["credit"] = urljoin(final, href)
        elif title == "社会融资规模" and "tsf" not in out:
            out["tsf"] = urljoin(final, href)
        if len(out) == 2:
            break
    if "credit" not in out:
        raise ValueError(f"Could not discover {year} credit-statistics section from official index")
    return out


def discover_htm(section_url: str, label: str) -> tuple[str, dict[str, Any]]:
    final, raw, html = fetch(section_url)
    pos = html.find(label)
    if pos < 0:
        raise ValueError(f"Label not found on official section: {label}")
    chunk = html[pos:pos + 5000]
    # pick the first anchor explicitly labelled htm after the exact section label
    m = re.search(r'href=["\']([^"\']+)["\'][^>]*>\s*(?:<[^>]+>\s*)*htm\s*(?:</[^>]+>\s*)*</a>', chunk, re.I | re.S)
    if not m:
        # more permissive fallback within the bounded label chunk
        p = LinkParser(); p.feed(chunk)
        cand = next((href for href, title in p.links if title.lower() == "htm"), None)
        if not cand:
            raise ValueError(f"HTM link not found after label {label}")
        url = urljoin(final, cand)
    else:
        url = urljoin(final, m.group(1))
    return url, {
        "section_url": final,
        "section_sha256": hashlib.sha256(raw).hexdigest(),
        "label": label,
    }


def parse_table(url: str) -> tuple[list[list[str]], dict[str, Any]]:
    final, raw, html = fetch(url)
    p = TableParser(); p.feed(html)
    return p.rows, {
        "table_url": final,
        "table_sha256": hashlib.sha256(raw).hexdigest(),
        "row_count": len(p.rows),
    }


def month_columns(rows: list[list[str]], year: int) -> tuple[dict[int, int], int]:
    for i, row in enumerate(rows):
        mapping: dict[int, int] = {}
        for j, cell in enumerate(row):
            m = re.fullmatch(rf"{year}[.]([01]?\d)", cell.strip())
            if m:
                mo = int(m.group(1))
                if 1 <= mo <= 12:
                    mapping[mo] = j
        if mapping:
            return mapping, i
    raise ValueError(f"Month header row not found for {year}")


def find_rows_by_occurrence(rows: list[list[str]], needle: str) -> list[int]:
    n = norm(needle)
    out = []
    for i, row in enumerate(rows):
        if not row:
            continue
        if n in norm(row[0]):
            out.append(i)
    return out


def exactish_row(rows: list[list[str]], needles: list[str], occurrence: int = 0) -> int:
    matches = []
    for i, row in enumerate(rows):
        if not row:
            continue
        c = norm(row[0])
        if all(norm(x) in c for x in needles):
            matches.append(i)
    if len(matches) <= occurrence:
        raise ValueError(f"Row not found: {needles}, occurrence={occurrence}, matches={matches}")
    return matches[occurrence]


def value_at(rows: list[list[str]], row_i: int, col_i: int) -> float | None:
    if row_i >= len(rows) or col_i >= len(rows[row_i]):
        return None
    return parse_num(rows[row_i][col_i])


def mk_obs(series_id: str, period: str, value: float, evidence: dict[str, Any], retrieved_at: str,
           root_id: str, unit: str = "CNY_100M", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "reference_period": period,
        "value": value,
        "unit": unit,
        "provider": "People's Bank of China",
        "source_url": evidence["table_url"],
        "source_sha256": evidence["table_sha256"],
        "retrieved_at": retrieved_at,
        "available_at": retrieved_at,
        "availability_basis": "CONSERVATIVE_BACKFILL_RETRIEVAL_TIME",
        "backfill_status": "BACKFILL_FROM_OFFICIAL_ANNUAL_TABLE",
        "collector_version": COLLECTOR_VERSION,
        "evidence_root_id": root_id,
        "dimensions": extra or {},
    }


def parse_credit_year(year: int, rows: list[list[str]], evidence: dict[str, Any], retrieved_at: str) -> tuple[list[dict[str, Any]], dict[str, dict[int, float]]]:
    cols, _ = month_columns(rows, year)
    selectors = {
        "FISC_DEPOSIT_STOCK": exactish_row(rows, ["财政性存款"]),
        "CC_RMB_LOAN_STOCK": exactish_row(rows, ["一、各项贷款", "TotalLoans"]),
        "CC_HH_ST_LOAN_STOCK": exactish_row(rows, ["短期贷款", "Short-termLoans"], occurrence=0),
        "CC_HH_LT_LOAN_STOCK": exactish_row(rows, ["中长期贷款", "Long-termLoans"], occurrence=0),
        "CC_CORP_ST_LOAN_STOCK": exactish_row(rows, ["短期贷款", "Short-termLoans"], occurrence=1),
        "CC_CORP_LT_LOAN_STOCK": exactish_row(rows, ["中长期贷款", "Long-termLoans"], occurrence=1),
        "CC_BILL_FINANCING_STOCK": exactish_row(rows, ["票据融资", "PaperFinancing"]),
    }
    values: dict[str, dict[int, float]] = {sid: {} for sid in selectors}
    obs: list[dict[str, Any]] = []
    for sid, row_i in selectors.items():
        for month, col_i in sorted(cols.items()):
            v = value_at(rows, row_i, col_i)
            if v is None:
                continue
            values[sid][month] = v
            period = f"{year}-{month:02d}"
            obs.append(mk_obs(sid, period, v, evidence, retrieved_at, f"PBOC_RMB_CREDIT_TABLE_{period}"))
    return obs, values


def derive_credit(current_year: int, curr: dict[str, dict[int, float]], prev: dict[str, dict[int, float]], evidence: dict[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    flow_map = {
        "FISC_DEPOSIT_STOCK": ("FISC_DEPOSIT_CHANGE", "FISC_DEPOSIT_STOCK_CHANGE_V1"),
        "CC_RMB_LOAN_STOCK": ("CC_RMB_LOAN_INCREMENT", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
        "CC_CORP_ST_LOAN_STOCK": ("CC_CORP_ST_LOAN", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
        "CC_CORP_LT_LOAN_STOCK": ("CC_CORP_LT_LOAN", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
        "CC_HH_ST_LOAN_STOCK": ("CC_HH_ST_LOAN", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
        "CC_HH_LT_LOAN_STOCK": ("CC_HH_LT_LOAN", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
        "CC_BILL_FINANCING_STOCK": ("CC_BILL_FINANCING", "CREDIT_STOCK_TO_MONTHLY_CHANGE_V1"),
    }
    out: list[dict[str, Any]] = []
    for parent, (sid, method) in flow_map.items():
        for month, cur in sorted(curr.get(parent, {}).items()):
            prior = curr[parent].get(month - 1) if month > 1 else prev.get(parent, {}).get(12)
            if prior is None:
                continue
            period = f"{current_year}-{month:02d}"
            x = mk_obs(sid, period, cur - prior, evidence, retrieved_at, f"PBOC_RMB_CREDIT_TABLE_{period}")
            x["method_version"] = method
            x["parent_series_ids"] = [parent]
            x["parent_periods"] = [period, f"{current_year if month > 1 else current_year-1}-{month-1 if month>1 else 12:02d}"]
            out.append(x)
    # Same-calendar-month YoY from exact stock roots.
    for month, cur in sorted(curr.get("CC_RMB_LOAN_STOCK", {}).items()):
        prior = prev.get("CC_RMB_LOAN_STOCK", {}).get(month)
        if prior in (None, 0):
            continue
        period = f"{current_year}-{month:02d}"
        x = mk_obs("CC_RMB_LOAN_STOCK_YOY", period, (cur / prior - 1.0) * 100.0, evidence, retrieved_at,
                   f"PBOC_RMB_CREDIT_TABLE_{period}", unit="percent")
        x["method_version"] = "CREDIT_STOCK_YOY_V1"
        x["parent_series_ids"] = ["CC_RMB_LOAN_STOCK"]
        x["parent_periods"] = [period, f"{current_year-1}-{month:02d}"]
        out.append(x)
    return out


def parse_tsf(year: int, rows: list[list[str]], evidence: dict[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    # The official flow table is row-oriented by month, with stable ordered columns.
    out: list[dict[str, Any]] = []
    mapping = [
        ("CC_TSF_INCREMENT", 1),
        ("CC_TSF_RMB_LOANS", 2),
        ("CC_TSF_ENTRUSTED_LOAN", 4),
        ("CC_TSF_TRUST_LOAN", 5),
        ("CC_TSF_UNDISCOUNTED_BA", 6),
        ("CC_TSF_CORP_BOND_NET", 7),
        ("FISC_GOV_BOND_NET_FINANCING_TSF", 8),
    ]
    for row in rows:
        if not row:
            continue
        m = re.fullmatch(rf"{year}[.]([01]?\d)", row[0].strip())
        if not m:
            continue
        month = int(m.group(1))
        period = f"{year}-{month:02d}"
        if len(row) < 9:
            continue
        for sid, idx in mapping:
            v = parse_num(row[idx]) if idx < len(row) else None
            if v is None:
                continue
            dims: dict[str, Any] = {}
            root_id = f"PBOC_TSF_FLOW_{period}"
            if sid == "CC_TSF_INCREMENT":
                dims["role"] = "RECONCILIATION_HEADLINE"
            if sid == "FISC_GOV_BOND_NET_FINANCING_TSF":
                dims["shared_component"] = "GOVERNMENT_BOND_NET_FINANCING"
            out.append(mk_obs(sid, period, v, evidence, retrieved_at, root_id, extra=dims))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    year = args.year
    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    gaps: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    evidence_bundle: dict[str, Any] = {}

    try:
        current_sections = discover_year_sections(year)
        previous_sections = discover_year_sections(year - 1)
        credit_url, credit_section_ev = discover_htm(current_sections["credit"], "金融机构人民币信贷收支表")
        prev_credit_url, prev_credit_section_ev = discover_htm(previous_sections["credit"], "金融机构人民币信贷收支表")
        tsf_url, tsf_section_ev = discover_htm(current_sections["tsf"], "社会融资规模增量统计表")

        credit_rows, credit_ev = parse_table(credit_url)
        prev_credit_rows, prev_credit_ev = parse_table(prev_credit_url)
        tsf_rows, tsf_ev = parse_table(tsf_url)

        credit_obs, curr_values = parse_credit_year(year, credit_rows, credit_ev, retrieved_at)
        _, prev_values = parse_credit_year(year - 1, prev_credit_rows, prev_credit_ev, retrieved_at)
        tsf_obs = parse_tsf(year, tsf_rows, tsf_ev, retrieved_at)
        roots.extend(credit_obs)
        roots.extend(tsf_obs)
        derived.extend(derive_credit(year, curr_values, prev_values, credit_ev, retrieved_at))
        evidence_bundle = {
            "stats_index": STATS_INDEX,
            "current_sections": current_sections,
            "previous_sections": previous_sections,
            "credit_section": credit_section_ev,
            "previous_credit_section": prev_credit_section_ev,
            "tsf_section": tsf_section_ev,
            "credit_table": credit_ev,
            "previous_credit_table": prev_credit_ev,
            "tsf_table": tsf_ev,
        }
    except Exception as e:
        gaps.append({"family": "PBC_MONTHLY_CREDIT", "year": year, "reason": "SOURCE_OR_PARSER_FAILURE", "error": repr(e)})

    required_root_series = {
        "FISC_DEPOSIT_STOCK", "CC_RMB_LOAN_STOCK", "CC_CORP_ST_LOAN_STOCK", "CC_CORP_LT_LOAN_STOCK",
        "CC_HH_ST_LOAN_STOCK", "CC_HH_LT_LOAN_STOCK", "CC_BILL_FINANCING_STOCK",
        "CC_TSF_INCREMENT", "CC_TSF_RMB_LOANS", "CC_TSF_CORP_BOND_NET", "CC_TSF_ENTRUSTED_LOAN",
        "CC_TSF_TRUST_LOAN", "CC_TSF_UNDISCOUNTED_BA", "FISC_GOV_BOND_NET_FINANCING_TSF",
    }
    present = {x["series_id"] for x in roots}
    missing_families = sorted(required_root_series - present)
    if missing_families and not gaps:
        gaps.append({"family": "PBC_MONTHLY_CREDIT", "year": year, "reason": "REQUIRED_SERIES_FAMILY_EMPTY", "missing_series": missing_families})

    run = {
        "module": "china_financial_pbc_monthly_credit_family",
        "collector_version": COLLECTOR_VERSION,
        "year": year,
        "completed_at": retrieved_at,
        "status": "PASS" if not gaps else "INCOMPLETE",
        "root_observation_count": len(roots),
        "derived_observation_count": len(derived),
        "gap_count": len(gaps),
        "present_root_series": sorted(present),
        "evidence": evidence_bundle,
        "semantic_rules": {
            "one_release_one_observation": True,
            "stock_to_flow_parent_lineage_required": True,
            "tsf_headline_is_reconciliation": True,
            "backfill_available_at_is_conservative_retrieval_time": True,
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
