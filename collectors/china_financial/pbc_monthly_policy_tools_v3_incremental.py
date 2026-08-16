#!/usr/bin/env python3
"""Store-aware incremental PBC monthly policy-tools collector V3."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

import pbc_monthly_policy_tools_v1 as v1
import pbc_monthly_policy_tools_v2 as v2

COLLECTOR_VERSION='V1.9-READY-PBC-MONTHLY-POLICY-TOOLS-INCREMENTAL-V3'


def load_rows(path:Path)->list[dict[str,Any]]:
    if not path.exists():return []
    x=json.loads(path.read_text(encoding='utf-8'));return x if isinstance(x,list) else []

def max_month(rows:list[dict[str,Any]],year:int)->int:
    vals=[]
    for r in rows:
        p=str(r.get('reference_period') or '')
        if p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit():vals.append(int(p[5:7]))
    return max(vals) if vals else 0


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    store_file=Path(a.store_root)/'observations'/str(a.year)/f'{a.year}-pbc-monthly-policy-tools-v2.json';stored=max_month(load_rows(store_file),a.year)
    obs=[];gaps=[];release_ev=[];list_ev={};official=0
    try:
        releases,list_ev=v1.discover(a.year);official=max([m for m,_,_ in releases],default=0)
        if official<stored:raise ValueError(f'official list behind persisted Store: official={official} stored={stored}')
        for month,title,url in [x for x in releases if x[0]>stored]:
            rows,ev=v2.parse_article(a.year,month,title,url,now)
            for r in rows:r['collector_version']=COLLECTOR_VERSION
            obs.extend(rows);release_ev.append(ev)
        expected=max(0,official-stored)*len(v1.ROW_MAP)
        if official>stored and len(obs)!=expected:
            gaps.append({'family':'PBC_MONTHLY_POLICY_TOOLS','reason':'NEW_RELEASE_COUNT_GATE_FAILED','expected':expected,'actual':len(obs)})
    except Exception as e:gaps.append({'family':'PBC_MONTHLY_POLICY_TOOLS','reason':'INCREMENTAL_RELEASE_CHECK_OR_PARSE_FAILURE','error':repr(e)})
    mode='NO_NEW_RELEASE_CARRY_STORE' if not gaps and official<=stored else ('NEW_RELEASE_INCREMENT' if not gaps else 'INCOMPLETE')
    run={'module':'china_financial_pbc_monthly_policy_tools_incremental','collector_version':COLLECTOR_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'stored_latest_month':stored,'official_latest_month':official,'release_count':len(release_ev),'observation_count':len(obs),'gap_count':len(gaps),'series_ids':sorted(v1.ROW_MAP.values()),'list_evidence':list_ev,'release_evidence':release_ev,'semantic_rules':{'daily_list_check_only_when_unchanged':True,'only_new_release_details_fetched':True,'no_new_release_carries_store':True,'historical_release_recrawl_default':False,'policy_flow_not_realized_financial_condition':True,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
