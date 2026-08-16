#!/usr/bin/env python3
"""Store-aware incremental NAFMII DFI gross issuance collector V2."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

import nafmii_dfi_issuance_family_v1 as v1

COLLECTOR_VERSION='V1.9-READY-NAFMII-DFI-ISSUANCE-INCREMENTAL-V2'


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
    store_file=Path(a.store_root)/'observations'/str(a.year)/f'{a.year}-nafmii-dfi-issuance-v1.json';stored=max_month(load_rows(store_file),a.year)
    obs=[];gaps=[];ev=[];list_ev={};official=0
    try:
        releases,list_ev=v1.discover(a.year);official=max([x['month'] for x in releases],default=0)
        if official<stored:raise ValueError(f'official list behind persisted Store: official={official} stored={stored}')
        for x in [r for r in releases if r['month']>stored]:
            o,e=v1.parse_release(a.year,x['month'],x['title'],x['url'],now);o['collector_version']=COLLECTOR_VERSION;obs.append(o);ev.append(e)
        if official>stored and len(obs)!=(official-stored):gaps.append({'family':'NAFMII_DFI_ISSUANCE','reason':'NEW_RELEASE_COUNT_GATE_FAILED','expected':official-stored,'actual':len(obs)})
    except Exception as e:gaps.append({'family':'NAFMII_DFI_ISSUANCE','reason':'INCREMENTAL_RELEASE_CHECK_OR_PARSE_FAILURE','error':repr(e)})
    mode='NO_NEW_RELEASE_CARRY_STORE' if not gaps and official<=stored else ('NEW_RELEASE_INCREMENT' if not gaps else 'INCOMPLETE')
    run={'module':'china_financial_nafmii_dfi_issuance_incremental','collector_version':COLLECTOR_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'stored_latest_month':stored,'official_latest_month':official,'release_count':len(ev),'observation_count':len(obs),'gap_count':len(gaps),'list_evidence':list_ev,'release_evidence':ev,'semantic_rules':{'daily_list_check_only_when_unchanged':True,'only_new_release_pdfs_fetched':True,'historical_pdf_recrawl_default':False,'no_new_release_carries_store':True,'own_release_month_only':True,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
