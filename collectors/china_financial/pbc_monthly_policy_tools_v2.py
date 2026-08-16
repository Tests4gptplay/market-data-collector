#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

import pbc_monthly_policy_tools_v1 as v1

COLLECTOR_VERSION = "V1.9-CANDIDATE-PBC-MONTHLY-POLICY-TOOLS-V2"


def visible_publish_timestamp(rows: list[list[str]]) -> str:
    for row in rows:
        for i, cell in enumerate(row):
            if "文章来源" not in cell:
                continue
            for candidate in row[i + 1:]:
                candidate = candidate.strip()
                if len(candidate) == 19 and candidate[4] == '-' and candidate[7] == '-' and candidate[10] == ' ' and candidate[13] == ':' and candidate[16] == ':':
                    return candidate.replace(' ', 'T') + '+08:00'
    raise ValueError("exact visible publication timestamp next to 文章来源 not found")


def parse_article(year:int, month:int, title:str, url:str, retrieved_at:str):
    final,raw,html,fetch_meta=v1.fetch(url)
    p=v1.TableParser(); p.feed(html)
    published=visible_publish_timestamp(p.rows)
    obs=[]; found=set()
    for row in p.rows:
        name=next((x for x in v1.ROW_MAP if x in row),None)
        if not name: continue
        idx=row.index(name); tail=row[idx+1:]; nums=[]
        for x in tail:
            t=x.replace(',','').strip()
            try:
                if t and all(c in '-.0123456789' for c in t): nums.append(v1.parse_num(x))
            except ValueError:
                pass
        if len(nums)<3: raise ValueError(f"{name}: expected 投放/回笼/净投放, row={row}")
        injected,withdrawn,net=nums[:3]
        if abs((injected-withdrawn)-net)>1e-9: raise ValueError(f"{name}: arithmetic mismatch")
        sid=v1.ROW_MAP[name]; found.add(sid)
        obs.append({"series_id":sid,"reference_period":f"{year}-{month:02d}","value":net,"unit":"CNY_100M","provider":"People's Bank of China","source_url":final,"source_sha256":hashlib.sha256(raw).hexdigest(),"published_at":published,"available_at":published,"retrieved_at":retrieved_at,"collector_version":COLLECTOR_VERSION,"dimensions":{"tool_name":name,"injection":injected,"withdrawal":withdrawn,"source_semantic":"OFFICIAL_MONTHLY_NET_INJECTION"}})
    missing=sorted(set(v1.ROW_MAP.values())-found)
    if missing: raise ValueError(f"missing required rows: {missing}")
    return obs,{"reference_period":f"{year}-{month:02d}","title":title,"url":final,"sha256":hashlib.sha256(raw).hexdigest(),"published_at":published,"fetch":fetch_meta}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    obs=[]; gaps=[]; evidence=[]; list_ev={}; releases=[]
    try:
        releases,list_ev=v1.discover(a.year)
        for month,title,url in releases:
            try:
                rows,ev=parse_article(a.year,month,title,url,now); obs.extend(rows); evidence.append(ev)
            except Exception as e:
                gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reference_period":f"{a.year}-{month:02d}","source_url":url,"reason":"OFFICIAL_RELEASE_FETCH_OR_PARSE_FAILURE","error":repr(e)})
        expected=len(releases)*len(v1.ROW_MAP)
        if len(obs)!=expected: gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reason":"COUNT_GATE_FAILED","expected":expected,"actual":len(obs)})
    except Exception as e:
        gaps.append({"family":"PBC_MONTHLY_POLICY_TOOLS","reason":"LIST_OR_COLLECTOR_FAILURE","error":repr(e)})
    run={"module":"china_financial_pbc_monthly_policy_tools","collector_version":COLLECTOR_VERSION,"year":a.year,"completed_at":now,"status":"PASS" if not gaps else "INCOMPLETE","release_count":len(releases),"observation_count":len(obs),"gap_count":len(gaps),"series_ids":sorted(v1.ROW_MAP.values()),"list_evidence":list_ev,"release_evidence":evidence,"semantic_rules":{"use_official_net_column":True,"net_arithmetic_crosscheck":True,"published_at_must_be_visible_文章来源_timestamp":True,"policy_flow_not_realized_financial_condition":True,"one_release_one_observation":True,"unknown_is_never_zero":True}}
    for name,obj in (("observations.json",obs),("gaps.json",gaps),("run.json",run)): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2)); print(json.dumps(gaps,ensure_ascii=False,indent=2)); return 0 if not gaps else 2
if __name__=='__main__': raise SystemExit(main())
