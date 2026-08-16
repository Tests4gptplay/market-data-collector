#!/usr/bin/env python3
"""PBOC policy-event collector V2 with OMO7D policy-rate state-change collection."""
from __future__ import annotations
import argparse, hashlib, json, re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from collectors.china_financial import policy_event_family_v1 as v1

COLLECTOR_VERSION="V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V2"

def _num(s:str):
    m=re.search(r"(-?\d+(?:\.\d+)?)",s.replace(',',''))
    return float(m.group(1)) if m else None

def _is_7d(s:str)->bool:
    s=re.sub(r"\s+","",s)
    return s in {"7天","7天期"} or ("7天" in s and "逆回购" in s)

def parse_7d_rate_from_article(url:str):
    final,raw,html=v1.fetch(url)
    text,rows,available=v1.clean_article(html)
    sha=hashlib.sha256(raw).hexdigest()
    candidates=[]; rate_col=None
    for row in rows:
        norm=[re.sub(r"\s+","",c) for c in row]
        for i,c in enumerate(norm):
            if "操作利率" in c or "中标利率" in c or c=="利率": rate_col=i
        if not any(_is_7d(c) for c in row): continue
        rate=None; sel=None
        if rate_col is not None and rate_col<len(row):
            rate=_num(row[rate_col]); sel=f"table_rate_column_{rate_col}"
        if rate is None:
            pct=[(i,_num(c)) for i,c in enumerate(row) if ('%' in c or '％' in c) and _num(c) is not None]
            if len(pct)==1: rate=pct[0][1]; sel=f"unique_percent_cell_{pct[0][0]}"
        if rate is None:
            ti=next((i for i,c in enumerate(row) if _is_7d(c)),None)
            plausible=[]
            for i,c in enumerate(row):
                if i==ti: continue
                n=_num(c)
                if n is not None and 0.1<=n<=10: plausible.append((i,n))
            if len(plausible)==1: rate=plausible[0][1]; sel=f"unique_plausible_rate_cell_{plausible[0][0]}"
        if rate is not None: candidates.append((float(rate),sel,row))
    for pat in [
        r"7天(?:期)?逆回购[^。；\n]{0,80}?(?:操作利率|中标利率)[为：:]?\s*(\d+(?:\.\d+)?)\s*[%％]",
        r"(?:操作利率|中标利率)[为：:]?\s*(\d+(?:\.\d+)?)\s*[%％][^。；\n]{0,80}?7天(?:期)?逆回购",
    ]:
        for m in re.finditer(pat,text): candidates.append((float(m.group(1)),"explicit_prose",None))
    rates=sorted({round(x[0],10) for x in candidates})
    if not rates: return None
    if len(rates)!=1: raise ValueError(f"ambiguous official 7D rates: {rates}")
    chosen=next(x for x in candidates if round(x[0],10)==rates[0])
    return {"rate":rates[0],"source_url":final,"available_at":available,"sha256":sha,"selection":chosen[1]}

def collect_omo7d_rate_change(target:date):
    obs=[]; gaps=[]
    try: links,list_ev=v1.article_links("OMO",v1.SOURCES["OMO"])
    except Exception as e:
        return [],[{"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"SOURCE_LIST_FAILURE","error":repr(e)}],{"status":"SOURCE_FAILURE"}
    parsed=[]
    for item in links[:60]:
        d=v1.parse_visible_date(item["title"])
        if d is None or d>target or (target-d).days>45: continue
        try:
            r=parse_7d_rate_from_article(item["url"])
            if r: parsed.append({"date":d,"title":item["title"],**r})
        except Exception as e:
            gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"source_url":item["url"],"reason":"ARTICLE_RATE_PARSE_FAILURE","error":repr(e)})
    parsed.sort(key=lambda x:x["date"],reverse=True)
    cur=[x for x in parsed if x["date"]==target]; prev=[x for x in parsed if x["date"]<target]
    if not cur:
        target_links=[x for x in links if v1.parse_visible_date(x["title"])==target]
        return obs,gaps,{"status":"NO_7D_RATE_EVENT_CONFIRMED" if target_links else "NO_TARGET_OMO_NOTICE","target_date":target.isoformat(),"list_evidence":list_ev}
    rates={x["rate"] for x in cur}
    if len(rates)!=1:
        gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"TARGET_RATE_CONFLICT","rates":sorted(rates)})
        return obs,gaps,{"status":"INCOMPLETE"}
    if not prev:
        gaps.append({"family":"OMO7D_RATE","target_date":target.isoformat(),"reason":"PREVIOUS_STATE_NOT_ESTABLISHED"})
        return obs,gaps,{"status":"INCOMPLETE"}
    c=cur[0]; p=prev[0]; change=round((c["rate"]-p["rate"])*100,8)
    if abs(change)>1e-9:
        obs.append({"series_id":"POL_OMO7D_RATE","reference_date":target.isoformat(),"value":c["rate"],"unit":"percent","provider":"People's Bank of China","source_url":c["source_url"],"available_at":c["available_at"],"collector_version":COLLECTOR_VERSION,"evidence_sha256":c["sha256"],"dimensions":{"source_semantic":"PBOC_7D_REVERSE_REPO_POLICY_RATE_STATE_CHANGE","previous_rate":p["rate"],"previous_reference_date":p["date"].isoformat(),"change_bps":change,"selection_rule":c["selection"]}})
        status="RATE_CHANGE_FOUND"
    else: status="UNCHANGED_CONFIRMED"
    return obs,gaps,{"status":status,"target_date":target.isoformat(),"current_rate":c["rate"],"previous_rate":p["rate"],"previous_reference_date":p["date"].isoformat(),"change_bps":change,"list_evidence":list_ev}

def collect(target:date):
    observations,gaps,run=v1.collect(target)
    r_obs,r_gaps,r_run=collect_omo7d_rate_change(target)
    observations.extend(r_obs); gaps.extend(r_gaps)
    out=dict(run); out.update({"collector_version":COLLECTOR_VERSION,"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"PASS" if not gaps else "INCOMPLETE","observation_count":len(observations),"gap_count":len(gaps),"omo7d_rate_state":r_run})
    return observations,gaps,out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    target=date.fromisoformat(a.date); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(target)
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2)); return 0 if run['status']=='PASS' else 2
if __name__=='__main__': raise SystemExit(main())
