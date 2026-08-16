#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COLLECTOR_VERSION = "V1.9-CANDIDATE-PBC-MONTHLY-POLICY-TOOLS-V1"
LIST_URL = "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/5727710/index.html"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
ROW_MAP = {
    "常备借贷便利（SLF）": "POL_SLF_NET_MONTHLY",
    "中期借贷便利（MLF）": "POL_MLF_NET_MONTHLY",
    "抵押补充贷款（PSL）": "POL_PSL_NET_MONTHLY",
    "其他结构性货币政策工具": "POL_STRUCTURAL_TOOLS_NET_MONTHLY",
    "公开市场国债买卖": "POL_PBOC_CGB_NET_MONTHLY",
    "中央国库现金管理": "POL_TREASURY_CASH_MGMT_NET",
}

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="a": self.href=dict(attrs).get("href"); self.buf=[]
    def handle_data(self,d):
        if self.href is not None: self.buf.append(d)
    def handle_endtag(self,tag):
        if tag.lower()=="a" and self.href is not None:
            self.links.append((self.href," ".join("".join(self.buf).split()))); self.href=None; self.buf=[]

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.incell=False; self.cell=[]; self.row=[]; self.rows=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower() in ("td","th"): self.incell=True; self.cell=[]
    def handle_data(self,d):
        if self.incell: self.cell.append(d)
    def handle_endtag(self,tag):
        if tag.lower() in ("td","th") and self.incell:
            self.row.append(" ".join("".join(self.cell).replace("\xa0"," ").split())); self.incell=False
        elif tag.lower()=="tr":
            if any(self.row): self.rows.append(self.row)
            self.row=[]

def fetch(url:str, attempts:int=5):
    errors=[]; last=None
    for i in range(attempts):
        try:
            req=Request(url,headers={"User-Agent":UA,"Accept-Language":"zh-CN,zh;q=0.9","Connection":"close"})
            with urlopen(req,timeout=45) as r:
                raw=r.read()
                for enc in ("utf-8","gb18030"):
                    try: text=raw.decode(enc); break
                    except UnicodeDecodeError: pass
                else: text=raw.decode("utf-8","replace")
                return r.geturl(),raw,text,{"attempts_used":i+1,"prior_errors":errors,"http_status":getattr(r,"status",200)}
        except Exception as e:
            last=e; errors.append(repr(e))
            if i+1<attempts: time.sleep(min(2*(i+1),8))
    raise RuntimeError(json.dumps({"url":url,"errors":errors},ensure_ascii=False)) from last

def parse_num(s:str)->float:
    t=s.replace(",","").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?",t): raise ValueError(f"not numeric: {s!r}")
    return float(t)

def discover(year:int):
    final,raw,html,meta=fetch(LIST_URL); p=LinkParser(); p.feed(html); out=[]; seen=set()
    for href,title in p.links:
        m=re.fullmatch(rf"{year}年(\d{{1,2}})月中央银行各项工具流动性投放情况",title)
        if not m or not href: continue
        month=int(m.group(1)); url=urljoin(final,href)
        key=(month,url)
        if key in seen: continue
        seen.add(key); out.append((month,title,url))
    out.sort()
    return out,{"list_url":final,"list_sha256":hashlib.sha256(raw).hexdigest(),"list_fetch":meta}

def parse_article(year:int, month:int, title:str, url:str, retrieved_at:str):
    final,raw,html,fetch_meta=fetch(url); p=TableParser(); p.feed(html)
    pub=re.search(r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",html)
    if not pub: raise ValueError("visible publication timestamp missing")
    published=pub.group(1).replace(" ","T")+"+08:00"
    obs=[]; found=set()
    for row in p.rows:
        name=next((x for x in ROW_MAP if x in row),None)
        if not name: continue
        idx=row.index(name)
        tail=row[idx+1:]
        nums=[]
        for x in tail:
            if re.fullmatch(r"-?\d+(?:\.\d+)?",x.replace(",","")):
                nums.append(parse_num(x))
        if len(nums)<3: raise ValueError(f"{name}: expected 投放/回笼/净投放, row={row}")
        injected,withdrawn,net=nums[:3]
        if abs((injected-withdrawn)-net)>1e-9:
            raise ValueError(f"{name}: net arithmetic mismatch {injected}-{withdrawn}!={net}")
        sid=ROW_MAP[name]; found.add(sid)
        obs.append({
            "series_id":sid,"reference_period":f"{year}-{month:02d}","value":net,"unit":"CNY_100M",
            "provider":"People's Bank of China","source_url":final,"source_sha256":hashlib.sha256(raw).hexdigest(),
            "published_at":published,"available_at":published,"retrieved_at":retrieved_at,"collector_version":COLLECTOR_VERSION,
            "dimensions":{"tool_name":name,"injection":injected,"withdrawal":withdrawn,"source_semantic":"OFFICIAL_MONTHLY_NET_INJECTION"},
        })
    missing=sorted(set(ROW_MAP.values())-found)
    if missing: raise ValueError(f"missing required policy-tool rows: {missing}")
    return obs,{"reference_period":f"{year}-{month:02d}","title":title,"url":final,"sha256":hashlib.sha256(raw).hexdigest(),"published_at":published,"fetch":fetch_meta}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    obs=[]; gaps=[]; evidence=[]; list_ev={}; releases=[]
    try:
        releases,list_ev=discover(a.year)
        for month,title,url in releases:
            try:
                rows,ev=parse_article(a.year,month,title,url,now); obs.extend(rows); evidence.append(ev)
            except Exception as e:
                gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reference_period":f"{a.year}-{month:02d}","source_url":url,"reason":"OFFICIAL_RELEASE_FETCH_OR_PARSE_FAILURE","error":repr(e)})
        periods=sorted({x["reference_period"] for x in obs})
        expected_periods=[f"{a.year}-{m:02d}" for m,_,_ in releases]
        if periods!=expected_periods:
            gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reason":"PERIOD_COVERAGE_MISMATCH","expected":expected_periods,"actual":periods})
        expected_count=len(releases)*len(ROW_MAP)
        if len(obs)!=expected_count:
            gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reason":"COUNT_GATE_FAILED","expected":expected_count,"actual":len(obs)})
    except Exception as e:
        gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reason":"LIST_OR_COLLECTOR_FAILURE","error":repr(e)})
    run={"module":"china_financial_pbc_monthly_policy_tools","collector_version":COLLECTOR_VERSION,"year":a.year,"completed_at":now,"status":"PASS" if not gaps else "INCOMPLETE","release_count":len(releases),"observation_count":len(obs),"gap_count":len(gaps),"series_ids":sorted(ROW_MAP.values()),"list_evidence":list_ev,"release_evidence":evidence,"semantic_rules":{"use_official_net_column":True,"net_arithmetic_crosscheck":True,"policy_flow_not_realized_financial_condition":True,"one_release_one_observation":True,"unknown_is_never_zero":True}}
    for name,obj in (("observations.json",obs),("gaps.json",gaps),("run.json",run)): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(run,ensure_ascii=False,indent=2)); print(json.dumps(gaps,ensure_ascii=False,indent=2)); return 0 if not gaps else 2
if __name__=="__main__": raise SystemExit(main())
