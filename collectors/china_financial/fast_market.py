#!/usr/bin/env python3
"""China Financial V1.9 candidate: unified Fast-market collector.

This module consolidates source paths that were independently smoke-tested before
migration into the public market-data-collector repository.

Semantic guardrails:
- DR007 is the actual interbank pledged-repo weighted transaction rate.
- FDR007 is a fixing/QC benchmark and never substitutes for DR007.
- GC001 is the SSE actual weighted-average repo rate for security code 204001.
- NCD AAA 3M must use the pure ChinaMoney NCD(AAA) curve CYCC41B at 0.25Y.
- Credit-spread parents must be same-provider and same-date. The currently
  implemented fallback bundle is ChinaBond CP&Note AAA + ChinaBond CGB.
- Unknown/missing data becomes an explicit GAP, never zero or neutral.

The collector writes normalized JSON only. Raw response bytes are preserved in the
run output directory for evidence/QC but are not intended to be automatically
committed to the public dataset without source-specific redistribution review.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE"
INTERFACE_VERSION = "CF_INTERFACE_V1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"

CHINAMONEY_ROOT = "https://www.chinamoney.com.cn"
DR_CHART = CHINAMONEY_ROOT + "/r/cms/www/chinamoney/data/currency/prr-chrt.csv"
FDR_CHART = CHINAMONEY_ROOT + "/r/cms/www/chinamoney/data/currency/fdr-chrt.csv"
NCD_HISTORY = CHINAMONEY_ROOT + "/ags/ms/cm-u-bk-currency/ClsYldCurvHis"
NCD_REFERER = CHINAMONEY_ROOT + "/chinese/bkcurvclosedy/?bondType=CYCC41B"
SSE_QUERY = "https://query.sse.com.cn/commonQuery.do"
SSE_REFERER = "https://bond.sse.com.cn/data/statistics/overview/Pledgerepo/"
CHINABOND_HISTORY = "https://yield.chinabond.com.cn/cbweb-cbrc-web/cbrc/historyQuery"

CGB_NAME = "中债国债收益率曲线"
AAA_MTN_NAME = "中债中短期票据收益率曲线(AAA)"
CHINABOND_TENORS = ["3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalize_date_text(value: str) -> str:
    return re.sub(r"[^0-9]", "", value or "")


def decode(raw: bytes, content_type: str = "") -> str:
    candidates: list[str] = []
    m = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    head = raw[:4096].decode("latin1", "ignore")
    m = re.search(r"charset\s*=\s*['\"]?([^;\s'\">]+)", head, re.I)
    if m:
        candidates.append(m.group(1))
    candidates.extend(["utf-8", "gb18030", "gbk"])
    seen: set[str] = set()
    for enc in candidates:
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


@dataclass
class HTTPResult:
    url: str
    status: int | None
    content_type: str
    raw: bytes
    ok: bool
    error: str | None = None


def http(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    referer: str | None = None,
    accept: str = "*/*",
    headers: dict[str, str] | None = None,
    timeout: int = 40,
) -> HTTPResult:
    h = {
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Connection": "close",
    }
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return HTTPResult(
                url=resp.geturl(),
                status=getattr(resp, "status", 200),
                content_type=resp.headers.get("Content-Type", ""),
                raw=raw,
                ok=True,
            )
    except HTTPError as exc:
        return HTTPResult(
            url=url,
            status=exc.code,
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            raw=exc.read()[:500000],
            ok=False,
            error=f"HTTP {exc.code}",
        )
    except URLError as exc:
        return HTTPResult(url, None, "", b"", False, f"URLError: {exc}")
    except Exception as exc:  # fail closed; caller records GAP
        return HTTPResult(url, None, "", b"", False, f"{type(exc).__name__}: {exc}")


class ChinaBondTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self.in_cell = True
            self.cell_parts = []
        elif tag == "br" and self.in_cell:
            self.cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            self.row.append("".join(self.cell_parts).strip())
            self.in_cell = False
            self.cell_parts = []
        elif tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = []


def norm_html(text: str) -> str:
    return re.sub(r"\s+", "", unescape(text).replace("\xa0", " "))


def observation(
    *,
    series_id: str,
    reference_date: str,
    value: float,
    unit: str,
    provider: str,
    semantic: str,
    source_url: str,
    evidence_sha256: str,
    collected_at: str,
    role: str = "CORE",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "series_id": series_id,
        "reference_date": reference_date,
        "value": value,
        "unit": unit,
        "provider": provider,
        "source_semantic": semantic,
        "collected_at": collected_at,
        "collector_version": COLLECTOR_VERSION,
        "interface_version": INTERFACE_VERSION,
        "source_url": source_url,
        "evidence_sha256": evidence_sha256,
        "role": role,
    }
    if extra:
        row.update(extra)
    return row


def gap(series_id: str, reference_date: str, gap_class: str, stage: str, message: str) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "reference_date": reference_date,
        "gap_class": gap_class,
        "stage": stage,
        "message": message,
        "collector_version": COLLECTOR_VERSION,
        "interface_version": INTERFACE_VERSION,
        "created_at": now_iso(),
    }


def save_raw(raw_dir: Path, name: str, result: HTTPResult) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_bytes(result.raw)
    return sha256_bytes(result.raw)


def collect_dr007(date: str, raw_dir: Path) -> dict[str, Any]:
    result = http(DR_CHART, method="POST", data=b"", referer=CHINAMONEY_ROOT + "/chinese/mkdatapm/")
    digest = save_raw(raw_dir, "chinamoney_prr_chart.csv", result)
    if not result.ok:
        raise RuntimeError(result.error or "DR chart request failed")
    text = decode(result.raw, result.content_type)
    target = normalize_date_text(date)
    matches: list[list[str]] = []
    for row in csv.reader(text.splitlines()):
        if row and normalize_date_text(row[0]) == target:
            matches.append([x.strip() for x in row])
    if not matches:
        raise RuntimeError("target date not found in official prr-chrt.csv")
    row = matches[-1]
    if len(row) <= 7:
        raise RuntimeError(f"unexpected prr chart schema: {row!r}")
    value = float(row[7])  # official page JS mapping: vArr[7] -> DR007
    return observation(
        series_id="FUND_DR007",
        reference_date=date,
        value=value,
        unit="percent",
        provider="ChinaMoney",
        semantic="ACTUAL_PLEDGED_REPO_WEIGHTED_RATE_DR007",
        source_url=result.url,
        evidence_sha256=digest,
        collected_at=now_iso(),
        extra={"selection_rule": "official prr-chart mapping column index 7 -> DR007"},
    )


def collect_fdr007(date: str, raw_dir: Path) -> dict[str, Any]:
    result = http(FDR_CHART, method="POST", data=b"", referer=CHINAMONEY_ROOT + "/chinese/bkfrr/")
    digest = save_raw(raw_dir, "chinamoney_fdr_chart.csv", result)
    if not result.ok:
        raise RuntimeError(result.error or "FDR chart request failed")
    text = decode(result.raw, result.content_type)
    target = normalize_date_text(date)
    matches: list[list[str]] = []
    for row in csv.reader(text.splitlines()):
        if row and normalize_date_text(row[0]) == target:
            matches.append([x.strip() for x in row])
    if not matches:
        raise RuntimeError("target date not found in official fdr-chrt.csv")
    row = matches[-1]
    if len(row) < 3:
        raise RuntimeError(f"unexpected FDR chart schema: {row!r}")
    value = float(row[2])
    return observation(
        series_id="FUND_FDR007",
        reference_date=date,
        value=value,
        unit="percent",
        provider="ChinaMoney",
        semantic="FDR007_FIXING_QC_ONLY",
        source_url=result.url,
        evidence_sha256=digest,
        collected_at=now_iso(),
        role="QC_DIAGNOSTIC",
        extra={"substitution_prohibited_for": "FUND_DR007"},
    )


def collect_ncd_aaa_3m(date: str, raw_dir: Path) -> dict[str, Any]:
    query = {
        "lang": "CN",
        "reference": "1",
        "bondType": "CYCC41B",
        "startDate": date,
        "endDate": date,
        "termId": "0.25",
        "pageNum": "1",
        "pageSize": "50",
    }
    url = NCD_HISTORY + "?" + urlencode(query)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": CHINAMONEY_ROOT,
        "X-Requested-With": "XMLHttpRequest",
    }
    result = http(
        url,
        method="POST",
        data=b"",
        referer=NCD_REFERER,
        accept="application/json, text/javascript, */*; q=0.01",
        headers=headers,
    )
    digest = save_raw(raw_dir, "chinamoney_ncd_aaa_3m.json", result)
    if not result.ok:
        raise RuntimeError(result.error or "NCD history request failed")
    payload = json.loads(decode(result.raw, result.content_type))
    records = payload.get("records", []) if isinstance(payload, dict) else []
    exact = []
    for item in records:
        try:
            same_date = str(item.get("newDateValueCN", "")) == date
            same_term = abs(float(item.get("yearTermStr")) - 0.25) < 1e-9
        except Exception:
            continue
        if same_date and same_term:
            exact.append(item)
    if len(exact) != 1:
        raise RuntimeError(f"expected exactly one CYCC41B 0.25Y record, got {len(exact)}")
    value = float(exact[0]["maturityYieldStr"])
    return observation(
        series_id="FUND_NCD_AAA_3M",
        reference_date=date,
        value=value,
        unit="percent",
        provider="ChinaMoney",
        semantic="PURE_NCD_AAA_CURVE_CYCC41B_0_25Y",
        source_url=result.url,
        evidence_sha256=digest,
        collected_at=now_iso(),
        extra={"bond_type": "CYCC41B", "tenor_years": 0.25},
    )


def collect_gc001(date: str, raw_dir: Path) -> dict[str, Any]:
    params = {
        "jsonCallBack": "jsonpCallback",
        "isPagination": "false",
        "sqlId": "COMMON_SSEBOND_SCSJ_SCTJ_SCGL_ZQZYSHGSCGL_CX_L",
        "TRADE_DATE": date,
    }
    url = SSE_QUERY + "?" + urlencode(params)
    result = http(url, referer=SSE_REFERER)
    digest = save_raw(raw_dir, "sse_gc001.jsonp", result)
    if not result.ok:
        raise RuntimeError(result.error or "SSE GC request failed")
    text = decode(result.raw, result.content_type)
    match = re.match(r"^jsonpCallback\((.*)\)\s*;?\s*$", text, re.S)
    if not match:
        raise RuntimeError("unexpected SSE JSONP wrapper")
    payload = json.loads(match.group(1))
    records = payload.get("result", []) if isinstance(payload, dict) else []
    exact = [
        x
        for x in records
        if x.get("BOND_CODE") == "204001"
        and x.get("BOND_NAME") == "GC001"
        and x.get("TRADE_DATE") == date
    ]
    if len(exact) != 1:
        raise RuntimeError(f"expected exactly one GC001 row, got {len(exact)}")
    value = float(exact[0]["WEIGHT_RATE"])
    return observation(
        series_id="FUND_GC001",
        reference_date=date,
        value=value,
        unit="percent",
        provider="SSE",
        semantic="ACTUAL_GC001_WEIGHTED_AVERAGE_RATE",
        source_url=result.url,
        evidence_sha256=digest,
        collected_at=now_iso(),
        extra={"bond_code": "204001", "field": "WEIGHT_RATE"},
    )


def collect_chinabond(date: str, raw_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "startDate": date,
        "endDate": date,
        "gjqx": "0",
        "qxId": "ycqx",
        "locale": "cn_ZH",
        "mark": "1",
    }
    url = CHINABOND_HISTORY + "?" + urlencode(params)
    result = http(
        url,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    )
    digest = save_raw(raw_dir, "chinabond_history.html", result)
    if not result.ok:
        raise RuntimeError(result.error or "ChinaBond request failed")
    parser = ChinaBondTableParser()
    parser.feed(decode(result.raw, result.content_type))
    wanted = {norm_html(CGB_NAME): CGB_NAME, norm_html(AAA_MTN_NAME): AAA_MTN_NAME}
    rows: dict[str, dict[str, float | None]] = {}
    target = norm_html(date)
    for raw_row in parser.rows:
        cells = [c.strip() for c in raw_row]
        ncells = [norm_html(c) for c in cells]
        curve_idx = next((i for i, c in enumerate(ncells) if c in wanted), None)
        date_idx = next((i for i, c in enumerate(ncells) if c == target), None)
        if curve_idx is None or date_idx is None:
            continue
        name = wanted[ncells[curve_idx]]
        values = cells[date_idx + 1 : date_idx + 1 + len(CHINABOND_TENORS)]
        values += [""] * (len(CHINABOND_TENORS) - len(values))
        parsed: dict[str, float | None] = {}
        for tenor, raw_value in zip(CHINABOND_TENORS, values):
            parsed[tenor] = float(raw_value.replace("%", "")) if raw_value.strip() else None
        if name in rows and rows[name] != parsed:
            raise RuntimeError(f"conflicting duplicate ChinaBond row for {name}")
        rows[name] = parsed
    for required in (CGB_NAME, AAA_MTN_NAME):
        if required not in rows:
            raise RuntimeError(f"missing ChinaBond curve: {required}")

    cgb = rows[CGB_NAME]
    aaa = rows[AAA_MTN_NAME]
    required_values = {
        "SOV_CGB_3Y": cgb.get("3年"),
        "SOV_CGB_10Y": cgb.get("10年"),
        "CRD_MTN_AAA_3Y": aaa.get("3年"),
    }
    if any(v is None for v in required_values.values()):
        raise RuntimeError(f"required ChinaBond tenor missing: {required_values!r}")

    observations = [
        observation(
            series_id="SOV_CGB_3Y",
            reference_date=date,
            value=float(required_values["SOV_CGB_3Y"]),
            unit="percent",
            provider="ChinaBond",
            semantic="CGB_YIELD_CURVE_3Y",
            source_url=result.url,
            evidence_sha256=digest,
            collected_at=now_iso(),
            extra={"curve": CGB_NAME, "tenor": "3Y"},
        ),
        observation(
            series_id="SOV_CGB_10Y",
            reference_date=date,
            value=float(required_values["SOV_CGB_10Y"]),
            unit="percent",
            provider="ChinaBond",
            semantic="CGB_YIELD_CURVE_10Y",
            source_url=result.url,
            evidence_sha256=digest,
            collected_at=now_iso(),
            extra={"curve": CGB_NAME, "tenor": "10Y"},
        ),
        observation(
            series_id="CRD_MTN_AAA_3Y",
            reference_date=date,
            value=float(required_values["CRD_MTN_AAA_3Y"]),
            unit="percent",
            provider="ChinaBond",
            semantic="CP_NOTE_AAA_YIELD_CURVE_3Y_SAME_PROVIDER_FALLBACK",
            source_url=result.url,
            evidence_sha256=digest,
            collected_at=now_iso(),
            extra={"curve": AAA_MTN_NAME, "tenor": "3Y"},
        ),
    ]
    spread = round(
        (float(required_values["CRD_MTN_AAA_3Y"]) - float(required_values["SOV_CGB_3Y"])) * 100.0,
        6,
    )
    derived = {
        "series_id": "CRD_SPREAD_AAA_3Y",
        "reference_date": date,
        "value": spread,
        "unit": "bp",
        "method_id": "CREDIT_SPREAD_PROXY_V1.1",
        "provider_consistency": "ChinaBond+ChinaBond",
        "parent_series": ["CRD_MTN_AAA_3Y", "SOV_CGB_3Y"],
        "parent_evidence": [digest],
        "formula": "(CRD_MTN_AAA_3Y - SOV_CGB_3Y) * 100",
        "collector_version": COLLECTOR_VERSION,
        "interface_version": INTERFACE_VERSION,
    }
    return observations, derived


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="target trade/reference date YYYY-MM-DD")
    ap.add_argument("--out", default="out/china_financial_fast")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        raise SystemExit("--date must be YYYY-MM-DD")

    root = Path(args.out) / args.date
    raw_dir = root / "raw"
    root.mkdir(parents=True, exist_ok=True)

    observations: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    jobs = [
        ("FUND_DR007", collect_dr007),
        ("FUND_FDR007", collect_fdr007),
        ("FUND_NCD_AAA_3M", collect_ncd_aaa_3m),
        ("FUND_GC001", collect_gc001),
    ]
    for series_id, fn in jobs:
        try:
            observations.append(fn(args.date, raw_dir))
            attempts.append({"series_id": series_id, "status": "SUCCESS"})
        except Exception as exc:
            gaps.append(gap(series_id, args.date, "SOURCE_OR_PARSER_GAP", "COLLECT", str(exc)))
            attempts.append({"series_id": series_id, "status": "GAP", "message": str(exc)})

    try:
        obs, der = collect_chinabond(args.date, raw_dir)
        observations.extend(obs)
        derived.append(der)
        attempts.append({"series_id": "CHINABOND_BUNDLE", "status": "SUCCESS"})
    except Exception as exc:
        for sid in ["SOV_CGB_3Y", "SOV_CGB_10Y", "CRD_MTN_AAA_3Y", "CRD_SPREAD_AAA_3Y"]:
            gaps.append(gap(sid, args.date, "SOURCE_OR_PARSER_GAP", "COLLECT_OR_DERIVE", str(exc)))
        attempts.append({"series_id": "CHINABOND_BUNDLE", "status": "GAP", "message": str(exc)})

    required = {
        "FUND_DR007",
        "FUND_FDR007",
        "FUND_NCD_AAA_3M",
        "FUND_GC001",
        "SOV_CGB_10Y",
        "CRD_MTN_AAA_3Y",
        "CRD_SPREAD_AAA_3Y",
    }
    present = {x["series_id"] for x in observations} | {x["series_id"] for x in derived}
    missing = sorted(required - present)
    hard_gate = not missing

    run = {
        "module": "china_financial",
        "collector_version": COLLECTOR_VERSION,
        "interface_version": INTERFACE_VERSION,
        "target_date": args.date,
        "started_or_completed_at": now_iso(),
        "required_fast_series": sorted(required),
        "present_fast_series": sorted(required & present),
        "missing_fast_series": missing,
        "hard_fast_gate": hard_gate,
        "status": "PASS" if hard_gate else "INCOMPLETE",
        "attempts": attempts,
    }

    for name, payload in [
        ("observations.json", observations),
        ("derived.json", derived),
        ("gaps.json", gaps),
        ("run.json", run),
    ]:
        (root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if hard_gate else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
