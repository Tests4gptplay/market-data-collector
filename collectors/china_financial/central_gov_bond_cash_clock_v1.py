#!/usr/bin/env python3
"""Central-government book-entry Treasury cash-clock collector V1.

Source: Ministry of Finance Debt Management Department business announcements.

This collector is intentionally narrow. It uses official issuance notices and
post-auction Treasury announcements to extract only the dated cash-flow facts
needed by the China Financial Draft:

- auction / issuance event date and amount;
- issuance-payment deadline / cash-drain date and amount;
- principal maturity date and amount;
- coupon schedule dates and coupon-cash estimate after the tranche is issued.

Scope boundary:
- central-government book-entry Treasuries and explicitly named special
  Treasuries published in the MOF debt-management business-announcement area;
- savings bonds are not claimed by this collector;
- local-government bonds are handled by local_gov_bond_cash_clock_v3.py;
- planned and actual amounts remain distinguishable through dimensions;
- no-event is valid only after the publication window was checked successfully.

The collector does not attempt to maintain a security master. Each issuance or
reopening tranche contributes its own principal/coupon cash-flow observations;
a downstream Store can aggregate tranches by event date without pretending the
collector knows more than the official announcement states.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE-CENTRAL-GOV-BOND-CASH-CLOCK-V1"
BASE = "https://zwgls.mof.gov.cn/ywgg/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch(url: str, tries: int = 4) -> tuple[str, bytes]:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "close",
            })
            with urlopen(req, timeout=45) as r:
                return r.geturl(), r.read()
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    assert last is not None
    raise last


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def text_from_html(html: str) -> str:
    x = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    x = re.sub(r"(?i)<br\s*/?>|</(?:p|li|tr|div|h\d|td|th)>", "\n", x)
    x = re.sub(r"(?s)<[^>]+>", " ", x)
    x = unescape(x).replace("\u3000", " ").replace("\xa0", " ")
    # MOF pages occasionally insert spaces inside OCR-like digit strings.
    x = re.sub(r"(?<=\d)\s+(?=\d)", "", x)
    return "\n".join(" ".join(line.split()) for line in x.splitlines() if " ".join(line.split()))


def parse_cn_date(text: str) -> date | None:
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def all_cn_dates(text: str) -> list[date]:
    out: list[date] = []
    for y, m, d in re.findall(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return out


def list_url(page: int) -> str:
    return BASE if page == 0 else f"{BASE}index_{page}.htm"


def parse_list_page(page: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final, raw = fetch(list_url(page))
    html = decode(raw)
    rows: list[dict[str, Any]] = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = urljoin(final, m.group(1))
        title = " ".join(re.sub(r"<[^>]+>", " ", unescape(m.group(2))).split())
        if not title or "/ywgg/" not in href:
            continue
        if not ("国债业务公告" in title or "发行工作有关事宜" in title):
            continue
        ctx = text_from_html(html[m.end():m.end() + 500])
        d = parse_cn_date(ctx)
        if d is None:
            m2 = re.search(r"(20\d{2})-(\d{2})-(\d{2})", ctx)
            if m2:
                d = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        rows.append({"title": title, "url": href, "publication_date": d})
    dates = [r["publication_date"] for r in rows if isinstance(r.get("publication_date"), date)]
    ev = {
        "page": page,
        "url": final,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "row_count": len(rows),
        "min_date": min(dates).isoformat() if dates else None,
        "max_date": max(dates).isoformat() if dates else None,
    }
    return rows, ev


def documents_in_window(start: date, end: date, max_pages: int = 45) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    docs: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    crossed = False
    for page in range(max_pages):
        rows, ev = parse_list_page(page)
        evidence.append(ev)
        page_dates = [r["publication_date"] for r in rows if isinstance(r.get("publication_date"), date)]
        for row in rows:
            d = row.get("publication_date")
            if isinstance(d, date) and start <= d <= end:
                docs[row["url"]] = row
        if page_dates and min(page_dates) < start:
            crossed = True
            break
        if not rows and page > 2:
            break
    if not crossed and evidence:
        evidence[-1]["window_lower_bound_not_crossed"] = True
    return sorted(docs.values(), key=lambda x: (x["publication_date"], x["url"])), evidence


def bond_name(text: str) -> str | None:
    patterns = (
        r"(20\d{2}年记账式(?:贴现|附息)[（(][^）)\n]{1,20}[）)]国债)",
        r"(20\d{2}年超长期特别国债[（(][^）)\n]{1,20}[）)])",
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return " ".join(m.group(1).split())
    return None


def amount_100m(text: str, actual: bool) -> float | None:
    pats = (
        (r"实际(?:续)?发行面值金额\s*([0-9,.]+)\s*亿元", r"实际发行面值金额\s*([0-9,.]+)\s*亿元")
        if actual else
        (r"竞争性招标面值总额\s*([0-9,.]+)\s*亿元", r"计划(?:续)?发行\s*([0-9,.]+)\s*亿元")
    )
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def coupon_rate(text: str) -> float | None:
    for pat in (
        r"票面利率(?:与[^。]{0,80}?相同，)?为\s*([0-9.]+)\s*%",
        r"经招标确定的票面利率为\s*([0-9.]+)\s*%",
    ):
        m = re.search(pat, text)
        if m:
            return float(m.group(1))
    return None


def auction_date(text: str) -> date | None:
    m = re.search(r"招标时间[。\s]*?(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    return parse_cn_date(m.group(1)) if m else None


def payment_date(text: str) -> date | None:
    # Explicit MOF issue-payment deadline. Do not infer from accrual date if this
    # language is absent.
    m = re.search(r"发行款缴纳[^\n。]{0,200}?(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)前", text, re.S)
    if not m:
        m = re.search(r"中标承销团成员于\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)前", text)
    return parse_cn_date(m.group(1)) if m else None


def distribution_end_date(text: str) -> date | None:
    m = re.search(r"招标结束后至\s*(\d{1,2})月(\d{1,2})日进行分销", text)
    if not m:
        return None
    year_match = re.search(r"(20\d{2})年", text)
    if not year_match:
        return None
    try:
        return date(int(year_match.group(1)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def maturity_date(text: str) -> date | None:
    # Prefer dates immediately tied to principal redemption wording.
    for pat in (
        r"(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)[^。\n]{0,50}?偿还本金",
        r"于\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)[^。\n]{0,50}?按面值偿还",
    ):
        m = re.search(pat, text)
        if m:
            return parse_cn_date(m.group(1))
    return None


def accrual_start_date(text: str) -> date | None:
    m = re.search(r"(?:自|起息日为)\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)开始计息", text)
    if not m:
        m = re.search(r"起息日为\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    return parse_cn_date(m.group(1)) if m else None


def coupon_month_days(text: str) -> list[tuple[int, int]]:
    if "到期一次还本付息" in text:
        return []
    matches = re.findall(r"(?:每年)?\s*(\d{1,2})月\s*(\d{1,2})日", text)
    out: list[tuple[int, int]] = []
    # Limit to dates in the sentence carrying 付息/利息支付 semantics when possible.
    for line in text.splitlines():
        if "付息" not in line and "利息支付日" not in line:
            continue
        for m, d in re.findall(r"(\d{1,2})月\s*(\d{1,2})日", line):
            pair = (int(m), int(d))
            if pair not in out:
                out.append(pair)
    if out:
        return out
    # Avoid using unrelated month/day pairs from the whole document.
    return []


def future_coupon_dates(start_after: date, maturity: date, md: list[tuple[int, int]]) -> list[date]:
    out: list[date] = []
    for year in range(start_after.year, maturity.year + 1):
        for month, day in md:
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if start_after < d <= maturity:
                out.append(d)
    return sorted(set(out))


def base_obs(series_id: str, reference_date: date, value: float, unit: str, source_url: str,
             publication_date: date, evidence_sha256: str, semantic: str, dimensions: dict[str, Any]) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "reference_date": reference_date.isoformat(),
        "value": value,
        "unit": unit,
        "provider": "Ministry of Finance of the People's Republic of China / Debt Management Department",
        "source_semantic": semantic,
        "collected_at": utc_now(),
        "available_at": publication_date.isoformat(),
        "collector_version": COLLECTOR_VERSION,
        "source_url": source_url,
        "evidence_sha256": evidence_sha256,
        "dimensions": dimensions,
    }


def parse_document(url: str, publication_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    final, raw = fetch(url)
    text = text_from_html(decode(raw))
    sha = hashlib.sha256(raw).hexdigest()
    name = bond_name(text)
    if not name:
        return [], [], {"url": final, "publication_date": publication_date.isoformat(), "status": "OUT_OF_SCOPE", "reason": "NO_BOOK_ENTRY_TREASURY_NAME"}

    is_preissue = "发行工作有关事宜" in text and "拟" in text
    is_result = "已完成招标工作" in text
    if not is_preissue and not is_result:
        return [], [], {"url": final, "publication_date": publication_date.isoformat(), "status": "OUT_OF_SCOPE", "reason": "NOT_ISSUANCE_NOTICE_OR_RESULT"}

    amount = amount_100m(text, actual=is_result)
    if amount is None:
        return [], [{
            "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
            "reference_date": publication_date.isoformat(),
            "gap_class": "SEMANTIC_PARSE_FAILURE",
            "stage": "CENTRAL_GOV_BOND_DOCUMENT",
            "message": "ISSUANCE_AMOUNT_NOT_PARSED",
            "collector_version": COLLECTOR_VERSION,
            "source_url": final,
        }], {"url": final, "publication_date": publication_date.isoformat(), "status": "INCOMPLETE", "bond_name": name}

    common = {
        "government_level": "CENTRAL",
        "instrument_scope": "BOOK_ENTRY_TREASURY",
        "bond_name": name,
        "publication_date": publication_date.isoformat(),
        "point_in_time_safe": True,
    }
    obs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    if is_preissue:
        auct = auction_date(text)
        pay = payment_date(text)
        mat = maturity_date(text)
        if auct:
            obs.append(base_obs("FISC_GOV_BOND_AUCTION_EVENT", auct, amount, "CNY 100m", final, publication_date, sha,
                                "MOF_CENTRAL_GOV_BOND_PLANNED_AUCTION_FACE_VALUE",
                                {**common, "event_stage": "PLANNED", "amount_semantic": "PLANNED_COMPETITIVE_AUCTION_FACE_VALUE"}))
        else:
            gaps.append({"series_id": "FISC_GOV_BOND_AUCTION_EVENT", "reference_date": publication_date.isoformat(), "gap_class": "SEMANTIC_PARSE_FAILURE", "stage": "PREISSUE", "message": "EXPLICIT_AUCTION_DATE_NOT_PARSED", "collector_version": COLLECTOR_VERSION, "source_url": final})
        if pay:
            obs.append(base_obs("FISC_GOV_BOND_PAYMENT_EVENT", pay, amount, "CNY 100m", final, publication_date, sha,
                                "MOF_CENTRAL_GOV_BOND_PLANNED_PAYMENT_FACE_VALUE",
                                {**common, "event_stage": "PLANNED", "amount_semantic": "PLANNED_FACE_VALUE", "explicit_payment_deadline": True}))
        else:
            gaps.append({"series_id": "FISC_GOV_BOND_PAYMENT_EVENT", "reference_date": publication_date.isoformat(), "gap_class": "SEMANTIC_PARSE_FAILURE", "stage": "PREISSUE", "message": "EXPLICIT_PAYMENT_DATE_NOT_PARSED", "collector_version": COLLECTOR_VERSION, "source_url": final})
        if mat:
            obs.append(base_obs("FISC_GOV_BOND_MATURITY_EVENT", mat, amount, "CNY 100m", final, publication_date, sha,
                                "MOF_CENTRAL_GOV_BOND_PLANNED_PRINCIPAL_MATURITY",
                                {**common, "event_stage": "PLANNED", "amount_semantic": "PLANNED_TRANCHE_FACE_VALUE"}))

    if is_result:
        # MOF result announcements are published when the auction result is known.
        # Publication date is therefore an availability/event proxy, not a hidden
        # reconstruction of intraday auction time.
        obs.append(base_obs("FISC_GOV_BOND_AUCTION_EVENT", publication_date, amount, "CNY 100m", final, publication_date, sha,
                            "MOF_CENTRAL_GOV_BOND_ACTUAL_AUCTION_RESULT_FACE_VALUE",
                            {**common, "event_stage": "ACTUAL_RESULT", "amount_semantic": "ACTUAL_ISSUED_FACE_VALUE", "auction_date_semantic": "RESULT_PUBLICATION_DATE"}))
        pay = distribution_end_date(text)
        mat = maturity_date(text)
        if pay:
            obs.append(base_obs("FISC_GOV_BOND_PAYMENT_EVENT", pay, amount, "CNY 100m", final, publication_date, sha,
                                "MOF_CENTRAL_GOV_BOND_ACTUAL_AMOUNT_SCHEDULED_PAYMENT",
                                {**common, "event_stage": "ACTUAL_AMOUNT_SCHEDULED_DATE", "amount_semantic": "ACTUAL_ISSUED_FACE_VALUE", "payment_date_source": "DISTRIBUTION_END_DATE"}))
        if mat:
            obs.append(base_obs("FISC_GOV_BOND_MATURITY_EVENT", mat, amount, "CNY 100m", final, publication_date, sha,
                                "MOF_CENTRAL_GOV_BOND_ACTUAL_TRANCHE_PRINCIPAL_MATURITY",
                                {**common, "event_stage": "ACTUAL_TRANCHE", "amount_semantic": "ACTUAL_ISSUED_FACE_VALUE"}))

        rate = coupon_rate(text)
        md = coupon_month_days(text)
        start = pay or accrual_start_date(text) or publication_date
        if rate is not None and mat is not None:
            if "到期一次还本付息" in text:
                # One-time interest at maturity; for a one-year Treasury this is
                # face * annual coupon rate. Longer unusual one-time structures
                # are left without a cash estimate instead of assuming compounding.
                accrual = accrual_start_date(text)
                if accrual and 360 <= (mat - accrual).days <= 370:
                    obs.append(base_obs("FISC_GOV_BOND_COUPON_SCHEDULE_EVENT", mat, rate, "percent_and_date", final, publication_date, sha,
                                        "MOF_CENTRAL_GOV_BOND_COUPON_SCHEDULE",
                                        {**common, "event_stage": "SCHEDULED", "coupon_rate_percent": rate, "frequency_per_year": 1}))
                    obs.append(base_obs("FISC_GOV_BOND_COUPON_CASH_ESTIMATE", mat, amount * rate / 100.0, "CNY 100m", final, publication_date, sha,
                                        "MOF_CENTRAL_GOV_BOND_COUPON_CASH_ESTIMATE",
                                        {**common, "event_stage": "SCHEDULED_ESTIMATE", "coupon_rate_percent": rate, "frequency_per_year": 1, "formula": "tranche_face*coupon_rate"}))
            elif md:
                freq = len(md)
                for d in future_coupon_dates(start, mat, md):
                    obs.append(base_obs("FISC_GOV_BOND_COUPON_SCHEDULE_EVENT", d, rate, "percent_and_date", final, publication_date, sha,
                                        "MOF_CENTRAL_GOV_BOND_COUPON_SCHEDULE",
                                        {**common, "event_stage": "SCHEDULED", "coupon_rate_percent": rate, "frequency_per_year": freq}))
                    obs.append(base_obs("FISC_GOV_BOND_COUPON_CASH_ESTIMATE", d, amount * rate / 100.0 / freq, "CNY 100m", final, publication_date, sha,
                                        "MOF_CENTRAL_GOV_BOND_COUPON_CASH_ESTIMATE",
                                        {**common, "event_stage": "SCHEDULED_ESTIMATE", "coupon_rate_percent": rate, "frequency_per_year": freq, "formula": "tranche_face*coupon_rate/frequency"}))

    return obs, gaps, {
        "url": final,
        "publication_date": publication_date.isoformat(),
        "status": "PASS" if not gaps else "INCOMPLETE",
        "bond_name": name,
        "document_type": "PREISSUE" if is_preissue else "RESULT",
        "observation_count": len(obs),
        "gap_count": len(gaps),
        "evidence_sha256": sha,
    }


def dedupe_key(obs: dict[str, Any]) -> tuple[Any, ...]:
    dims = obs.get("dimensions") or {}
    return (
        obs.get("series_id"), obs.get("reference_date"), obs.get("value"),
        obs.get("source_url"), dims.get("bond_name"), dims.get("event_stage"),
    )


def collect(as_of: date, lookback_days: int = 7, detail_url: str | None = None, publication_date: date | None = None):
    if lookback_days < 0 or lookback_days > 31:
        raise ValueError("lookback_days must be between 0 and 31")
    observations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    docs_run: list[dict[str, Any]] = []
    list_evidence: list[dict[str, Any]] = []

    if detail_url:
        pd = publication_date or as_of
        o, g, r = parse_document(detail_url, pd)
        observations += o; gaps += g; docs_run.append(r)
        window_proven = True
        start = pd
    else:
        start = as_of - timedelta(days=lookback_days)
        try:
            docs, list_evidence = documents_in_window(start, as_of)
            window_proven = not bool(list_evidence and list_evidence[-1].get("window_lower_bound_not_crossed"))
        except Exception as exc:
            docs = []
            window_proven = False
            gaps.append({
                "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
                "reference_date": as_of.isoformat(),
                "gap_class": "SOURCE_FAILURE",
                "stage": "MOF_BUSINESS_ANNOUNCEMENT_LIST",
                "message": repr(exc),
                "collector_version": COLLECTOR_VERSION,
            })
        for doc in docs:
            try:
                o, g, r = parse_document(doc["url"], doc["publication_date"])
                observations += o; gaps += g; docs_run.append(r)
            except Exception as exc:
                gaps.append({
                    "series_id": "FISC_GOV_BOND_EVENT_WINDOW",
                    "reference_date": doc["publication_date"].isoformat(),
                    "gap_class": "SOURCE_OR_PARSER_FAILURE",
                    "stage": "MOF_BUSINESS_ANNOUNCEMENT_DETAIL",
                    "message": repr(exc),
                    "collector_version": COLLECTOR_VERSION,
                    "source_url": doc["url"],
                })
                docs_run.append({"url": doc["url"], "publication_date": doc["publication_date"].isoformat(), "status": "FAIL"})

    unique = {dedupe_key(x): x for x in observations}
    observations = sorted(unique.values(), key=lambda x: (x["reference_date"], x["series_id"], x["source_url"]))
    if observations:
        context_status = "PASS_EVENTS_FOUND" if window_proven else "PARTIAL_EVENTS_FOUND_WITH_WINDOW_GAP"
    else:
        context_status = "PASS_NO_EVENT_IN_PROVEN_WINDOW" if window_proven else "UNKNOWN_WINDOW_NOT_PROVEN"

    context = {
        "module": "china_financial_central_gov_bond_cash_clock_context",
        "collector_version": COLLECTOR_VERSION,
        "as_of": as_of.isoformat(),
        "publication_window_start": start.isoformat(),
        "publication_window_end": as_of.isoformat(),
        "status": context_status,
        "scope": "CENTRAL_GOVERNMENT_BOOK_ENTRY_TREASURIES",
        "savings_bonds_included": False,
        "local_government_bonds_included": False,
        "window_proof": {
            "publication_window_checked": window_proven,
            "unknown_is_never_zero": True,
            "no_event_claim_requires_complete_window": True,
        },
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "documents": docs_run,
        "list_evidence": list_evidence,
    }
    run = {
        "module": "china_financial_central_gov_bond_cash_clock_v1",
        "collector_version": COLLECTOR_VERSION,
        "as_of": as_of.isoformat(),
        "completed_at": utc_now(),
        "status": "PASS" if window_proven and not gaps else "INCOMPLETE",
        "observation_count": len(observations),
        "gap_count": len(gaps),
        "context_status": context_status,
        "scope": context["scope"],
    }
    return observations, gaps, context, run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--out", required=True)
    ap.add_argument("--detail-url")
    ap.add_argument("--publication-date")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)
    pd = date.fromisoformat(args.publication_date) if args.publication_date else None
    obs, gaps, context, run = collect(as_of, args.lookback_days, args.detail_url, pd)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    for name, payload in (("observations.json", obs), ("gaps.json", gaps), ("context.json", context), ("run.json", run)):
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": run, "context": context}, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
