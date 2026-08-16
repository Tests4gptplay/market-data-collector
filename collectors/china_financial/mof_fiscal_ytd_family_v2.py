#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION="V1.9-CANDIDATE-MOF-FISCAL-YTD-FAMILY-V2"
METHOD_VERSION="FISC_YTD_PERIOD_FLOW_V1"
LIST_URL="https://gks.mof.gov.cn/tongjishuju/"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
ROOTS={
 "FISC_GENERAL_REVENUE":"全国一般公共预算收入",
 "FISC_GENERAL_EXPENDITURE":"全国一般公共预算支出",
 "FISC_GOV_FUND_REVENUE":"全国政府性基金预算收入",
 "FISC_GOV_FUND_EXPENDITURE":"全国政府性基金预算支出",
}
FLOW_MAP={
 "FISC_GENERAL_REVENUE":"FISC_GENERAL_REVENUE_PERIOD_FLOW",
 "FISC_GENERAL_EXPENDITURE":"FISC_GENERAL_EXPENDITURE_PERIOD_FLOW",
 "FISC_GOV_FUND_REVENUE":"FISC_GOV_FUND_REVENUE_PERIOD_FLOW",
 "FISC_GOV_FUND_EXPENDITURE":"FISC_GOV_FUND_EXPENDITURE_PERIOD_FLOW",
}

class LinkParser(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
 def handle_starttag(self,tag,attrs):
  if tag.lower()=="a": self.href=dict(attrs).get("href"); self.buf=[]
 def handle_data(self,d):
  if self.href is not None: self.buf.append(d)
 def handle_endtag(self,tag):
  if tag.lower()=="a" and self.href is not None:
   self.links.append((self.href," ".join("".join(self.buf).split()))); self.href=None; self.buf=[]
class TextParser(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]
 def handle_starttag(self,tag,attrs):
  if tag.lower() in ("p","div","li","br","tr","td","h1","h2","h3"): self.parts.append("\n")
 def handle_data(self,d): self.parts.append(d)

def fetch(url:str, attempts:int=6):
 last=None; errors=[]
 for i in range(attempts):
  try:
   req=Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*","Accept-Language":"zh-CN,zh;q=0.9","Connection":"close"})
   with urlopen(req,timeout=45) as r:
    raw=r.read()
    for enc in ("utf-8","gb18030"):
     try: text=raw.decode(enc); break
     except UnicodeDecodeError: pass
    else: text=raw.decode("utf-8","replace")
    return r.geturl(),raw,text,{"attempts_used":i+1,"http_status":getattr(r,"status",200),"prior_errors":errors}
  except Exception as e:
   last=e; errors.append(repr(e))
   if i+1<attempts: time.sleep(min(3*(i+1),12))
 raise RuntimeError(json.dumps({"url":url,"attempts":attempts,"errors":errors},ensure_ascii=False)) from last

def clean(html):
 p=TextParser(); p.feed(html); return "\n".join(x.strip() for x in "".join(p.parts).splitlines() if x.strip())
def pubdate(text):
 m=re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(?:来源[:：])",text)
 return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
def period(title,year):
 if f"{year}年" not in title or "财政收支情况" not in title: return None
 if re.search(r"1[-—–]2月",title): return {"period_key":f"{year}-01_02_COMBINED","period_end_month":2,"period_kind":"JAN_FEB_COMBINED_YTD"}
 if "一季度" in title: return {"period_key":f"{year}-Q1","period_end_month":3,"period_kind":"YTD_QUARTER"}
 if "上半年" in title: return {"period_key":f"{year}-H1","period_end_month":6,"period_kind":"YTD_HALF_YEAR"}
 m=re.search(r"1[-—–](\d{1,2})月",title)
 if m:
  mo=int(m.group(1)); return {"period_key":f"{year}-01_{mo:02d}_YTD","period_end_month":mo,"period_kind":"YTD"}
 return None
def total(text,label):
 vals={float(x) for x in re.findall(re.escape(label)+r"\s*(\d+(?:\.\d+)?)\s*亿元",text)}
 if len(vals)!=1: raise ValueError(f"{label}: expected one national total, got {sorted(vals)}")
 return next(iter(vals))
def mkroot(sid,p,v,src):
 return {"series_id":sid,"reference_period":p["period_key"],"period_end_month":p["period_end_month"],"period_kind":p["period_kind"],"value":v,"unit":"CNY_100M","provider":"Ministry of Finance of the People's Republic of China","source_url":src["url"],"source_sha256":src["sha256"],"published_at":src["published_at"],"available_at":src["published_at"],"retrieved_at":src["retrieved_at"],"collector_version":COLLECTOR_VERSION,"vintage_policy":"ONE_RELEASE_ONE_OBSERVATION"}

def discover(year,retrieved_at):
 final,raw,html,lmeta=fetch(LIST_URL); lp=LinkParser(); lp.feed(html)
 candidates=[]; seen=set()
 for href,title in lp.links:
  if not href or href in seen: continue
  p=period(title,year)
  if p is None: continue
  seen.add(href); candidates.append((p,title,urljoin(final,href)))
 candidates.sort(key=lambda x:x[0]["period_end_month"])
 releases=[]; gaps=[]
 for idx,(p,title,url) in enumerate(candidates):
  if idx: time.sleep(1.5)
  try:
   f,r,h,fmeta=fetch(url); text=clean(h); pd=pubdate(text)
   if pd is None: raise ValueError("visible official publication date missing")
   vals={sid:total(text,label) for sid,label in ROOTS.items()}
   releases.append({**p,"title":title,"url":f,"sha256":hashlib.sha256(r).hexdigest(),"published_at":pd,"retrieved_at":retrieved_at,"fetch":fmeta,"values":vals})
  except Exception as e:
   gaps.append({"title":title,"url":url,"period":p,"reason":"OFFICIAL_RELEASE_FETCH_OR_PARSE_FAILURE","error":repr(e)})
 return releases,gaps,{"list_url":final,"list_sha256":hashlib.sha256(raw).hexdigest(),"list_fetch":lmeta,"candidate_count":len(candidates)}

def derive(roots):
 by={}
 for r in roots: by.setdefault(r["series_id"],[]).append(r)
 out=[]
 for parent,sid in FLOW_MAP.items():
  prev=None
  for cur in sorted(by.get(parent,[]),key=lambda x:x["period_end_month"]):
   if cur["period_kind"]=="JAN_FEB_COMBINED_YTD": val=cur["value"]; kind="JAN_FEB_COMBINED_PERIOD_FLOW"; parents=[cur["reference_period"]]
   else:
    if prev is None: raise ValueError(f"missing previous eligible YTD for {sid} {cur['reference_period']}")
    val=cur["value"]-prev["value"]; kind="INCREMENTAL_PERIOD_FLOW"; parents=[cur["reference_period"],prev["reference_period"]]
   out.append({"series_id":sid,"reference_period":cur["reference_period"],"period_end_month":cur["period_end_month"],"period_kind":kind,"value":val,"unit":"CNY_100M","provider":cur["provider"],"source_url":cur["source_url"],"source_sha256":cur["source_sha256"],"published_at":cur["published_at"],"available_at":cur["available_at"],"retrieved_at":cur["retrieved_at"],"collector_version":COLLECTOR_VERSION,"method_version":METHOD_VERSION,"parent_series_ids":[parent],"parent_periods":parents,"lineage_rule":"CURRENT_ELIGIBLE_YTD_MINUS_PREVIOUS_ELIGIBLE_YTD; JAN_FEB_COMBINED_REMAINS_COMBINED"})
   prev=cur
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
 now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z"); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
 roots=[]; der=[]; gaps=[]; ev={}; rel=[]
 try:
  rel,rgaps,ev=discover(a.year,now); gaps.extend(rgaps)
  for x in rel:
   for sid,v in x["values"].items(): roots.append(mkroot(sid,x,v,x))
  if not gaps:
   der=derive(roots)
  if len(rel)!=5: gaps.append({"family":"MOF_FISCAL_YTD","reason":"EXPECTED_2026_RELEASE_SET_INCOMPLETE","expected_release_count":5,"actual_release_count":len(rel)})
  if not gaps and (len(roots)!=20 or len(der)!=20): gaps.append({"family":"MOF_FISCAL_YTD","reason":"COUNT_GATE_FAILED","roots":len(roots),"derived":len(der)})
 except Exception as e: gaps.append({"family":"MOF_FISCAL_YTD","reason":"COLLECTOR_FAILURE","error":repr(e)})
 run={"module":"china_financial_mof_fiscal_ytd_family","collector_version":COLLECTOR_VERSION,"method_version":METHOD_VERSION,"year":a.year,"completed_at":now,"status":"PASS" if not gaps else "INCOMPLETE","release_count":len(rel),"root_observation_count":len(roots),"derived_observation_count":len(der),"gap_count":len(gaps),"evidence":ev,"release_evidence":[{k:x[k] for k in ("period_key","title","url","sha256","published_at","fetch")} for x in rel],"semantic_rules":{"jan_feb_combined_not_split":True,"jan_feb_period_flow_equals_combined_ytd":True,"regular_period_is_current_ytd_minus_previous_ytd":True,"all_expected_releases_required":True,"bounded_normal_retry_only":True,"unknown_is_never_zero":True}}
 for name,obj in (("observations.json",roots),("derived.json",der),("gaps.json",gaps),("run.json",run)): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps(run,ensure_ascii=False,indent=2)); print(json.dumps(gaps,ensure_ascii=False,indent=2))
 return 0 if not gaps else 2
if __name__=="__main__": raise SystemExit(main())
