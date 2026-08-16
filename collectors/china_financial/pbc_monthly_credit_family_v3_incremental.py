#!/usr/bin/env python3
"""Store-aware PBC monthly credit incremental collector V3.

V2 is preserved. V3 advances the Store only when both the current-year RMB
credit table and TSF flow table expose the same complete new month. If one side
updates first, the prior persisted month remains authoritative and the run
records a non-destructive partial-release defer state.
"""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

import pbc_monthly_credit_family_v1 as v1

COLLECTOR_VERSION='V1.9-READY-PBC-MONTHLY-CREDIT-INCREMENTAL-V3'
REQUIRED={
 'FISC_DEPOSIT_STOCK','CC_RMB_LOAN_STOCK','CC_CORP_ST_LOAN_STOCK','CC_CORP_LT_LOAN_STOCK',
 'CC_HH_ST_LOAN_STOCK','CC_HH_LT_LOAN_STOCK','CC_BILL_FINANCING_STOCK','CC_TSF_INCREMENT',
 'CC_TSF_RMB_LOANS','CC_TSF_CORP_BOND_NET','CC_TSF_ENTRUSTED_LOAN','CC_TSF_TRUST_LOAN',
 'CC_TSF_UNDISCOUNTED_BA','FISC_GOV_BOND_NET_FINANCING_TSF'
}


def load_rows(path:Path)->list[dict[str,Any]]:
    if not path.exists():return []
    x=json.loads(path.read_text(encoding='utf-8'));return x if isinstance(x,list) else []

def max_month(rows:list[dict[str,Any]],year:int)->int:
    vals=[]
    for r in rows:
        p=str(r.get('reference_period') or '')
        if p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit():vals.append(int(p[5:7]))
    return max(vals) if vals else 0

def values_from_store(rows:list[dict[str,Any]],year:int)->dict[str,dict[int,float]]:
    out:dict[str,dict[int,float]]={}
    for r in rows:
        p=str(r.get('reference_period') or '')
        if not (p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit()):continue
        try:v=float(r['value'])
        except Exception:continue
        out.setdefault(str(r.get('series_id') or ''),{})[int(p[5:7])]=v
    return out

def tsf_latest_month(rows:list[dict[str,Any]],year:int)->int:
    return max_month(rows,year)


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    store=Path(a.store_root);cur_path=store/'observations'/str(a.year)/f'{a.year}-pbc-monthly-credit-v1.json';prev_path=store/'observations'/str(a.year-1)/f'{a.year-1}-pbc-monthly-credit-v1.json'
    persisted=load_rows(cur_path);persisted_prev=load_rows(prev_path);stored=max_month(persisted,a.year)
    roots=[];derived=[];gaps=[];diagnostics=[];evidence={};credit_latest=0;tsf_latest=0;complete_latest=0
    try:
        sections=v1.discover_year_sections(a.year)
        credit_url,credit_section_ev=v1.discover_htm(sections['credit'],'金融机构人民币信贷收支表')
        tsf_url,tsf_section_ev=v1.discover_htm(sections['tsf'],'社会融资规模增量统计表')
        credit_rows,credit_ev=v1.parse_table(credit_url);tsf_rows,tsf_ev=v1.parse_table(tsf_url)
        cols,_=v1.month_columns(credit_rows,a.year);credit_latest=max(cols) if cols else 0
        all_credit,curr_values=v1.parse_credit_year(a.year,credit_rows,credit_ev,now);all_tsf=v1.parse_tsf(a.year,tsf_rows,tsf_ev,now);tsf_latest=tsf_latest_month(all_tsf,a.year)
        complete_latest=min(credit_latest,tsf_latest)
        evidence={'current_sections':sections,'credit_section':credit_section_ev,'tsf_section':tsf_section_ev,'credit_table':credit_ev,'tsf_table':tsf_ev}
        if complete_latest<stored:raise ValueError(f'complete official tables behind persisted Store: complete={complete_latest} stored={stored}')
        if credit_latest!=tsf_latest:
            diagnostics.append({'reason':'PARTIAL_OFFICIAL_RELEASE_DEFER_STORE','credit_latest_month':credit_latest,'tsf_latest_month':tsf_latest,'complete_latest_month':complete_latest})
        store_curr=values_from_store(persisted,a.year);store_prev=values_from_store(persisted_prev,a.year-1)
        for sid,months in store_curr.items():curr_values.setdefault(sid,{}).update({m:v for m,v in months.items() if m not in curr_values.get(sid,{})})
        all_derived=v1.derive_credit(a.year,curr_values,store_prev,credit_ev,now)
        eligible={m for m in range(stored+1,complete_latest+1)}
        roots=[r for r in all_credit+all_tsf if int(str(r['reference_period'])[5:7]) in eligible]
        derived=[r for r in all_derived if int(str(r['reference_period'])[5:7]) in eligible]
        for r in roots:r['collector_version']=COLLECTOR_VERSION
        for r in derived:r['collector_version']=COLLECTOR_VERSION
        for month in sorted(eligible):
            present={r['series_id'] for r in roots if int(str(r['reference_period'])[5:7])==month}
            missing=sorted(REQUIRED-present)
            if missing:gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'NEW_COMPLETE_MONTH_REQUIRED_SERIES_MISSING','reference_period':f'{a.year}-{month:02d}','missing_series':missing})
    except Exception as e:gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'CURRENT_RELEASE_CHECK_OR_PARSE_FAILURE','error':repr(e)})
    if gaps:mode='INCOMPLETE'
    elif complete_latest>stored:mode='NEW_COMPLETE_RELEASE_INCREMENT'
    elif diagnostics:mode='PARTIAL_OFFICIAL_RELEASE_DEFER_STORE'
    else:mode='NO_NEW_RELEASE_CARRY_STORE'
    run={'module':'china_financial_pbc_monthly_credit_incremental','collector_version':COLLECTOR_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'stored_latest_month':stored,'credit_official_latest_month':credit_latest,'tsf_official_latest_month':tsf_latest,'complete_official_latest_month':complete_latest,'root_observation_count':len(roots),'derived_observation_count':len(derived),'gap_count':len(gaps),'diagnostics':diagnostics,'evidence':evidence,'semantic_rules':{'daily_current_year_only':True,'historical_web_refetch_default':False,'prior_parents_read_from_store':True,'advance_requires_credit_and_tsf_complete_same_month':True,'partial_official_update_defers_store':True,'no_new_release_carries_store':True,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',roots),('derived.json',derived),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
