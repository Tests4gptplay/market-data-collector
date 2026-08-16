#!/usr/bin/env python3
"""Local-government bond cash-clock collector V1 (CELMA official platform).

Data-only scope. This collector deliberately keeps planned and actual facts
separate; later reconciliation is outside this version.

Official nationwide channels (ad_code=87, ad_name=全国):
  193 发行前公告 -> planned auction/payment facts and explicit payment date
  194 发行结果   -> actual auction/issuance facts and coupon terms
  196 还本付息   -> actual debt-service cash, coupon/maturity classification

Point-in-time rule:
- A document is usable only when its CELMA list publication date <= as_of date.
- Normal daily mode ingests documents published exactly on --date. A no-document
  day is PASS/NO_EVENT, not GAP.
- --detail-url/--detail-type are diagnostic/test overrides and do not change
  production daily semantics.

Semantic safeguards:
- Never substitute accrual date for payment date.
- Planned face value and actual issuance amount remain distinct.
- Debt-service amount comes from the official payment notice when available.
- If coupon/principal split cannot be classified with a tight mathematical
  tolerance, preserve the row and emit a GAP instead of guessing.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from pypdf import PdfReader

COLLECTOR_VERSION = "V1.9-CANDIDATE-LOCAL-GOV-BOND-CASH-CLOCK-V1"
BASE = "https://www.celma.org.cn/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
CHANNELS = {"PREISSUE": "193", "RESULT": "194", "DEBT_SERVICE": "196"}
DETAIL_PREFIX = {"PREISSUE": "/fxqgg/", "RESULT": "/fxjg/", "DEBT_SERVICE": "/fxdf/"}


def fetch_bytes(url: str, tries: int = 4) -> tuple[str, bytes, dict[str, str]]:
    last = None
    for i in range(tries):
        try:
            req = Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": BASE,
                "Connection": "close",
            })
            with urlopen(req, timeout=45) as r:
                return r.geturl(), r.read(), dict(r.headers)
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    raise last


def decode(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def norm_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return "\n".join(" ".join(x.split()) for x in text.splitlines() if " ".join(x.split()))


def pdf_text(raw: bytes) -> str:
    if not raw.startswith(b"%PDF"):
        raise ValueError("attachment is not PDF")
    reader = PdfReader(io.BytesIO(raw))
    return norm_text("\n".join((p.extract_text() or "") for p in reader.pages))


def html_text(html: str) -> str:
    x = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    x = re.sub(r"(?i)<br\s*/?>|</(?:p|li|tr|div|h\d)>", "\n", x)
    x = re.sub(r"(?s)<[^>]+>", " ", x)
    return norm_text(unescape(x))


def parse_date(s: str) -> date | None:
    for pat in (r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?",):
        m = re.search(pat, s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
    return None


def list_url(channel: str, page: int = 1) -> str:
    name = "zqsclb.jhtml" if page <= 1 else f"zqsclb_{page}.jhtml"
    return BASE + name + "?" + urlencode({"ad_code": "87", "ad_name": "全国", "channelId": channel})


def parse_total_pages(html: str) -> int:
    for pat in (r'id=["\']totalPage["\'][^>]*value=["\'](\d+)', r'zqsclb_(\d+)\.jhtml[^\n]{0,120}?尾页'):
        vals = [int(x) for x in re.findall(pat, html, re.I)]
        if vals:
            return max(vals)
    m = re.search(r"共\s*\d+\s*条[^\d]{0,40}(\d+)\s*页", html)
    if m:
        return int(m.group(1))
    raise ValueError("CELMA totalPage not found")


def list_page(channel: str, page: int) -> dict[str, Any]:
    final, raw, _ = fetch_bytes(list_url(channel, page))
    html = decode(raw)
    total = parse_total_pages(html)
    prefix = next((DETAIL_PREFIX[k] for k, v in CHANNELS.items() if v == channel), None)
    rows: list[dict[str, Any]] = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href, body = m.group(1), m.group(2)
        full = urljoin(final, href)
        if prefix and prefix not in full:
            continue
        title = " ".join(re.sub(r"<[^>]+>", " ", unescape(body)).split())
        if not title:
            continue
        ctx = " ".join(re.sub(r"<[^>]+>", " ", html[max(0, m.start()-450):m.end()+600]).split())
        d = parse_date(ctx)
        rows.append({"title": title, "url": full, "publication_date": d})
    ds = [r["publication_date"] for r in rows if r["publication_date"]]
    return {
        "page": page,
        "url": final,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "total_pages": total,
        "min_date": min(ds) if ds else None,
        "max_date": max(ds) if ds else None,
        "rows": rows,
    }


def locate_page(channel: str, target: date) -> tuple[int, int, list[dict[str, Any]]]:
    first = list_page(channel, 1)
    total = first["total_pages"]
    ev = [{k:(v.isoformat() if isinstance(v,date) else v) for k,v in first.items() if k!="rows"}]
    if first["min_date"] and target >= first["min_date"]:
        return 1, total, ev
    lo, hi, best = 1, total, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        p = first if mid == 1 else list_page(channel, mid)
        ev.append({k:(v.isoformat() if isinstance(v,date) else v) for k,v in p.items() if k!="rows"})
        mn, mx = p["min_date"], p["max_date"]
        if mn is None or mx is None:
            hi = mid - 1
        elif target > mx:
            hi = mid - 1; best = max(1, mid-1)
        elif target < mn:
            lo = mid + 1; best = mid
        else:
            return mid, total, ev
    return min(max(best,1),total), total, ev


def documents_on_date(channel: str, target: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start,total,ev=locate_page(channel,target)
    docs=[]
    # Target date can straddle two adjacent pages, inspect +/-1.
    for pno in range(max(1,start-1),min(total,start+1)+1):
        p=list_page(channel,pno)
        ev.append({k:(v.isoformat() if isinstance(v,date) else v) for k,v in p.items() if k!="rows"})
        for row in p["rows"]:
            if row["publication_date"] == target:
                docs.append(row)
    unique={x["url"]:x for x in docs}
    return list(unique.values()),ev


def detail_attachments(url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    final,raw,_=fetch_bytes(url);html=decode(raw)
    at=[]
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',html,re.I|re.S):
        href=m.group(1);label=" ".join(re.sub(r"<[^>]+>"," ",unescape(m.group(2))).split())
        full=urljoin(final,href)
        if ".pdf" in full.lower() or "attachFiles" in full:
            at.append({"label":label,"url":full})
    return at,{"detail_url":final,"detail_sha256":hashlib.sha256(raw).hexdigest(),"detail_text":html_text(html)[:2000]}


def _fetch_pdf_text(url: str) -> tuple[str,str]:
    final,raw,_=fetch_bytes(url)
    return pdf_text(raw),hashlib.sha256(raw).hexdigest()


def parse_preissue(url: str, publication_date: date) -> tuple[list[dict[str,Any]],list[dict[str,Any]],dict[str,Any]]:
    obs=[];gaps=[];ats,dev=detail_attachments(url);parsed=[]
    # Prefer official issuance notice, not rating/investment-project files.
    candidates=[a for a in ats if any(k in a["label"] for k in ("有关事项的通知","公开发行","发行通知")) and "评级" not in a["label"]]
    if not candidates:
        candidates=[a for a in ats if "发行公开" in a["label"]]
    for a in candidates:
        try:
            txt,sha=_fetch_pdf_text(a["url"]);parsed.append(a["url"])
            pay=None
            for pat in (r"缴款日[（(]?\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",r"缴款起息日\s*(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?"):
                m=re.search(pat,txt)
                if m:
                    pay=date(int(m.group(1)),int(m.group(2)),int(m.group(3)));break
            auction=None
            # Explicit auction/issue time; do not infer from payment/accrual.
            for pat in (r"招标时间[^20]{0,120}(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",r"发行时间\s*(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?"):
                m=re.search(pat,txt,re.S)
                if m:
                    auction=date(int(m.group(1)),int(m.group(2)),int(m.group(3)));break
            amount=None
            for pat in (r"招标总量\s*([0-9,.]+)\s*亿元",r"发行总额\s*([0-9,.]+)\s*亿元",r"发行规模(?:（亿元）)?\s*([0-9,.]+)"):
                m=re.search(pat,txt)
                if m:
                    amount=float(m.group(1).replace(',',''));break
            if pay is None:
                gaps.append({"family":"LOCAL_GOV_PAYMENT_EVENT","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"EXPLICIT_PAYMENT_DATE_NOT_PARSED"})
                continue
            if amount is None:
                gaps.append({"family":"LOCAL_GOV_PAYMENT_EVENT","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"PLANNED_FACE_AMOUNT_NOT_PARSED","payment_date":pay.isoformat()})
                continue
            common={"provider":"China Local Government Bond Information Disclosure Platform / Provincial Finance Department","source_url":a["url"],"available_at":publication_date.isoformat(),"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha}
            obs.append({"series_id":"FISC_GOV_BOND_PAYMENT_EVENT","reference_date":pay.isoformat(),"value":amount,"unit":"CNY 100m",**common,"dimensions":{"government_level":"LOCAL","event_stage":"PLANNED","amount_semantic":"PREISSUE_PLANNED_FACE_VALUE","publication_date":publication_date.isoformat(),"explicit_payment_date":True,"point_in_time_safe":True}})
            if auction is not None:
                obs.append({"series_id":"FISC_GOV_BOND_AUCTION_EVENT","reference_date":auction.isoformat(),"value":amount,"unit":"CNY 100m",**common,"dimensions":{"government_level":"LOCAL","event_stage":"PLANNED","amount_semantic":"PREISSUE_PLANNED_FACE_VALUE","publication_date":publication_date.isoformat(),"explicit_auction_date":True,"point_in_time_safe":True}})
        except Exception as exc:
            gaps.append({"family":"LOCAL_GOV_PREISSUE","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"PDF_PARSE_FAILURE","error":repr(exc)})
    return obs,gaps,{"detail":dev,"attachments":len(ats),"candidate_attachments":len(candidates),"parsed":parsed}


def parse_result_records(text: str) -> list[dict[str,Any]]:
    # PDF text layout is semi-structured. Use each bond-code occurrence as anchor,
    # then parse tenor/amount before and coupon/date fields after it.
    records=[]
    code_matches=list(re.finditer(r"\b(\d{6,7})\b",text))
    for i,m in enumerate(code_matches):
        start=max(0,m.start()-220); end=code_matches[i+1].start() if i+1<len(code_matches) else min(len(text),m.end()+700)
        seg=text[start:end]
        # Require issuance-table semantics to avoid random document numbers.
        if "债" not in seg or not re.search(r"20\d{2}[-年]\d{1,2}[-月]\d{1,2}",seg):continue
        after=seg[seg.find(m.group(1))-start if False else max(0,m.start()-start):]
        tm=re.search(r"(\d+(?:\.\d+)?)年",seg)
        dates=re.findall(r"(20\d{2}-\d{2}-\d{2})",seg)
        if not tm or not dates:continue
        tenor_end=tm.end()
        nums=[float(x.replace(',','')) for x in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)",seg[tenor_end:])]
        amount=nums[0] if nums else None
        # Rate is the last plausible 0.1-10 numeric immediately before first ISO date.
        first_date_pos=seg.find(dates[0]); pre=seg[tenor_end:first_date_pos]
        pnums=[float(x) for x in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)",pre) if 0.1<=float(x)<=10]
        rate=pnums[-1] if pnums else None
        if amount is None or rate is None:continue
        records.append({"bond_code":m.group(1),"tenor_years":float(tm.group(1)),"amount_100m":amount,"coupon_pct":rate,"issue_date":dates[0],"accrual_date":dates[1] if len(dates)>1 else None,"raw_segment":seg[:500]})
    # Dedup code; malformed docs sometimes repeat code in footnotes.
    return list({r["bond_code"]:r for r in records}.values())


def parse_result(url:str,publication_date:date):
    obs=[];gaps=[];ats,dev=detail_attachments(url);parsed=[]
    candidates=[a for a in ats if "发行" in a["label"] and "评级" not in a["label"] and "信息披露" not in a["label"]]
    for a in candidates:
        try:
            txt,sha=_fetch_pdf_text(a["url"]); recs=parse_result_records(txt);parsed.append({"url":a["url"],"records":len(recs)})
            if not recs:
                gaps.append({"family":"LOCAL_GOV_AUCTION_EVENT","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"ISSUANCE_RESULT_ROWS_NOT_PARSED"});continue
            for r in recs:
                obs.append({"series_id":"FISC_GOV_BOND_AUCTION_EVENT","reference_date":r["issue_date"],"value":r["amount_100m"],"unit":"CNY 100m","provider":"China Local Government Bond Information Disclosure Platform / Provincial Finance Department","source_url":a["url"],"available_at":publication_date.isoformat(),"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha,"dimensions":{"government_level":"LOCAL","event_stage":"ACTUAL_RESULT","amount_semantic":"ACTUAL_ISSUED_FACE_VALUE","bond_code":r["bond_code"],"tenor_years":r["tenor_years"],"coupon_pct":r["coupon_pct"],"accrual_date":r["accrual_date"],"publication_date":publication_date.isoformat(),"point_in_time_safe":True}})
                obs.append({"series_id":"FISC_GOV_BOND_COUPON_SCHEDULE_EVENT","reference_date":r["accrual_date"] or r["issue_date"],"value":1,"unit":"event","provider":"China Local Government Bond Information Disclosure Platform / Provincial Finance Department","source_url":a["url"],"available_at":publication_date.isoformat(),"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha,"dimensions":{"government_level":"LOCAL","event_stage":"ISSUANCE_TERMS","bond_code":r["bond_code"],"coupon_pct":r["coupon_pct"],"tenor_years":r["tenor_years"],"accrual_date":r["accrual_date"],"publication_date":publication_date.isoformat(),"schedule_anchor_only":True,"point_in_time_safe":True}})
        except Exception as exc:gaps.append({"family":"LOCAL_GOV_RESULT","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"PDF_PARSE_FAILURE","error":repr(exc)})
    return obs,gaps,{"detail":dev,"attachments":len(ats),"candidates":len(candidates),"parsed":parsed}


def parse_debt_service_records(text:str)->list[dict[str,Any]]:
    records=[]
    blocks=re.split(r"(?=债券名称\s)",text)
    for b in blocks:
        cm=re.search(r"债券代码\s*(\d{6,7})",b); am=re.search(r"发行总额（亿元）\s*([0-9.]+)",b); rm=re.search(r"票面利率\s*([0-9.]+)%",b); dm=re.search(r"还本日/本次付息日\s*(20\d{2})年(\d{1,2})月(\d{1,2})日",b); pm=re.search(r"还本/付息金额（元）\s*([0-9.]+)",b)
        if not all((cm,am,rm,dm,pm)):continue
        records.append({"bond_code":cm.group(1),"principal_100m":float(am.group(1)),"coupon_pct":float(rm.group(1)),"payment_date":date(int(dm.group(1)),int(dm.group(2)),int(dm.group(3))),"actual_payment_cny":float(pm.group(1)),"raw_segment":b[:600]})
    return records


def classify_debt_service(r:dict[str,Any])->dict[str,Any]:
    principal=r["principal_100m"]*1e8; rate=r["coupon_pct"]/100.0; pay=r["actual_payment_cny"]
    candidates=[]
    for freq,frac in (("ANNUAL",1.0),("SEMIANNUAL",0.5),("QUARTERLY",0.25)):
        coupon=principal*rate*frac
        candidates.append((abs(pay-coupon),"COUPON_ONLY",freq,coupon,0.0))
        candidates.append((abs(pay-(principal+coupon)),"PRINCIPAL_PLUS_COUPON",freq,coupon,principal))
    err,kind,freq,coupon,principal_component=min(candidates,key=lambda x:x[0])
    tol=max(1.0,abs(pay)*1e-7)
    if err<=tol:return {"classification":kind,"frequency_guess":freq,"coupon_cny":coupon,"principal_cny":principal_component,"error_cny":err}
    return {"classification":"UNCLASSIFIED_COMPLEX_OR_PARTIAL_PRINCIPAL","frequency_guess":None,"coupon_cny":None,"principal_cny":None,"error_cny":err}


def parse_debt_service(url:str,publication_date:date):
    obs=[];gaps=[];ats,dev=detail_attachments(url);parsed=[]
    candidates=[a for a in ats if "还本付息" in a["label"] or "付息公告" in a["label"]]
    if not candidates:candidates=[a for a in ats if ".pdf" in a["url"].lower()]
    for a in candidates:
        try:
            txt,sha=_fetch_pdf_text(a["url"]);recs=parse_debt_service_records(txt);parsed.append({"url":a["url"],"records":len(recs)})
            if not recs:
                gaps.append({"family":"LOCAL_GOV_DEBT_SERVICE","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"DEBT_SERVICE_ROWS_NOT_PARSED"});continue
            for r in recs:
                c=classify_debt_service(r); common={"provider":"China Local Government Bond Information Disclosure Platform / Provincial Finance Department","source_url":a["url"],"available_at":publication_date.isoformat(),"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha}
                dims={"government_level":"LOCAL","bond_code":r["bond_code"],"principal_face_100m":r["principal_100m"],"coupon_pct":r["coupon_pct"],"official_actual_payment_cny":r["actual_payment_cny"],"classification":c["classification"],"frequency_inferred_from_cash_math":c["frequency_guess"],"publication_date":publication_date.isoformat(),"point_in_time_safe":True}
                obs.append({"series_id":"FISC_GOV_BOND_COUPON_SCHEDULE_EVENT","reference_date":r["payment_date"].isoformat(),"value":1,"unit":"event",**common,"dimensions":{**dims,"event_stage":"OFFICIAL_DEBT_SERVICE_NOTICE"}})
                if c["classification"] in ("COUPON_ONLY","PRINCIPAL_PLUS_COUPON"):
                    obs.append({"series_id":"FISC_GOV_BOND_COUPON_CASH_ESTIMATE","reference_date":r["payment_date"].isoformat(),"value":c["coupon_cny"]/1e8,"unit":"CNY 100m",**common,"dimensions":{**dims,"cash_semantic":"COUPON_COMPONENT_BACKED_BY_OFFICIAL_TOTAL_PAYMENT","classification_error_cny":c["error_cny"]}})
                    if c["classification"]=="PRINCIPAL_PLUS_COUPON":
                        obs.append({"series_id":"FISC_GOV_BOND_MATURITY_EVENT","reference_date":r["payment_date"].isoformat(),"value":c["principal_cny"]/1e8,"unit":"CNY 100m",**common,"dimensions":{**dims,"cash_semantic":"MATURITY_PRINCIPAL_COMPONENT_BACKED_BY_OFFICIAL_TOTAL_PAYMENT","classification_error_cny":c["error_cny"]}})
                else:
                    gaps.append({"family":"LOCAL_GOV_DEBT_SERVICE_SPLIT","source_url":a["url"],"publication_date":publication_date.isoformat(),"bond_code":r["bond_code"],"payment_date":r["payment_date"].isoformat(),"reason":"ACTUAL_PAYMENT_NOT_CLASSIFIABLE_WITH_STANDARD_COUPON_OR_FULL_MATURITY","actual_payment_cny":r["actual_payment_cny"],"principal_face_100m":r["principal_100m"],"coupon_pct":r["coupon_pct"]})
        except Exception as exc:gaps.append({"family":"LOCAL_GOV_DEBT_SERVICE","source_url":a["url"],"publication_date":publication_date.isoformat(),"reason":"PDF_PARSE_FAILURE","error":repr(exc)})
    return obs,gaps,{"detail":dev,"attachments":len(ats),"candidates":len(candidates),"parsed":parsed}


def collect(target:date,detail_url:str|None=None,detail_type:str|None=None):
    obs=[];gaps=[];runs=[]
    if detail_url:
        if detail_type not in ("PREISSUE","RESULT","DEBT_SERVICE"):raise ValueError("--detail-type must be PREISSUE, RESULT or DEBT_SERVICE")
        fn={"PREISSUE":parse_preissue,"RESULT":parse_result,"DEBT_SERVICE":parse_debt_service}[detail_type]
        o,g,r=fn(detail_url,target);obs+=o;gaps+=g;runs.append({"family":detail_type,"diagnostic_override":True,**r})
    else:
        for fam,ch in CHANNELS.items():
            try:docs,ev=documents_on_date(ch,target)
            except Exception as exc:
                gaps.append({"family":fam,"target_date":target.isoformat(),"source_url":list_url(ch,1),"reason":"LIST_DISCOVERY_FAILURE","error":repr(exc)});continue
            fr={"family":fam,"document_count":len(docs),"list_evidence":ev,"documents":[]}
            fn={"PREISSUE":parse_preissue,"RESULT":parse_result,"DEBT_SERVICE":parse_debt_service}[fam]
            for d in docs:
                o,g,r=fn(d["url"],d["publication_date"]);obs+=o;gaps+=g;fr["documents"].append({"title":d["title"],"url":d["url"],"publication_date":d["publication_date"].isoformat(),**r})
            runs.append(fr)
    unique={json.dumps([x["series_id"],x["reference_date"],x.get("value"),x.get("source_url"),x.get("dimensions",{}).get("bond_code"),x.get("dimensions",{}).get("event_stage")],ensure_ascii=False,sort_keys=True):x for x in obs}
    obs=list(unique.values())
    run={"module":"china_financial_local_gov_bond_cash_clock","collector_version":COLLECTOR_VERSION,"target_date":target.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"PASS" if not gaps else "INCOMPLETE","observation_count":len(obs),"gap_count":len(gaps),"families":runs,"semantic_rules":{"no_document_day_is_no_event_not_gap":True,"publication_date_must_not_exceed_target":True,"payment_date_must_be_explicit":True,"accrual_date_never_substituted_for_payment":True,"planned_and_actual_amounts_kept_distinct":True,"unclassified_debt_service_split_is_gap":True}}
    return obs,gaps,run


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--out',required=True);ap.add_argument('--detail-url');ap.add_argument('--detail-type');a=ap.parse_args()
    target=date.fromisoformat(a.date);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(target,a.detail_url,a.detail_type)
    for n,payload in (("observations.json",obs),("gaps.json",gaps),("run.json",run)):(out/n).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));
    if gaps:print(json.dumps(gaps,ensure_ascii=False,indent=2))
    return 0 if run['status']=='PASS' else 2

if __name__=='__main__':raise SystemExit(main())
