#!/usr/bin/env python3
"""Latest-first PBC monthly credit incremental collector V4.

V1-V3 are preserved. V4 is the active daily path and intentionally does not
force all monthly subfamilies to share one synchronized month. Each official
series advances whenever its own source has a newer published observation.
Backtest/as-of filtering is a downstream model responsibility, not a live
collector responsibility.
"""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

import pbc_monthly_credit_family_v1 as v1

COLLECTOR_VERSION='V1.9-READY-PBC-MONTHLY-CREDIT-INCREMENTAL-V4'


def load_rows(path:Path)->list[dict[str,Any]]:
    if not path.exists():return []
    x=json.loads(path.read_text(encoding='utf-8'))
    return x if isinstance(x,list) else []


def latest_by_series(rows:list[dict[str,Any]])->dict[str,str]:
    out={}
    for r in rows:
        sid=str(r.get('series_id') or '');p=str(r.get('reference_period') or '')
        if sid and p and p>out.get(sid,''):out[sid]=p
    return out


def store_values(rows:list[dict[str,Any]],year:int)->dict[str,dict[int,float]]:
    out:dict[str,dict[int,float]]={}
    for r in rows:
        p=str(r.get('reference_period') or '')
        if not (p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit()):continue
        try:v=float(r['value'])
        except Exception:continue
        out.setdefault(str(r.get('series_id') or ''),{})[int(p[5:7])]=v
    return out


def newer(rows:list[dict[str,Any]],latest:dict[str,str])->list[dict[str,Any]]:
    out=[]
    for r in rows:
        sid=str(r.get('series_id') or '');p=str(r.get('reference_period') or '')
        if sid and p and p>latest.get(sid,''):out.append(r)
    return out


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    store=Path(a.store_root);obs_store=store/'observations'/str(a.year)/f'{a.year}-pbc-monthly-credit-v1.json';der_store=store/'derived'/str(a.year)/f'{a.year}-pbc-monthly-credit-v1.json';prev_store=store/'observations'/str(a.year-1)/f'{a.year-1}-pbc-monthly-credit-v1.json'
    persisted=load_rows(obs_store);persisted_der=load_rows(der_store);persisted_prev=load_rows(prev_store)
    obs_latest=latest_by_series(persisted);der_latest=latest_by_series(persisted_der)
    roots=[];derived=[];gaps=[];diagnostics=[];evidence={}
    try:
        sections=v1.discover_year_sections(a.year)
        credit_url,credit_section_ev=v1.discover_htm(sections['credit'],'金融机构人民币信贷收支表')
        tsf_url,tsf_section_ev=v1.discover_htm(sections['tsf'],'社会融资规模增量统计表')
        credit_rows,credit_ev=v1.parse_table(credit_url);tsf_rows,tsf_ev=v1.parse_table(tsf_url)
        credit_obs,curr_values=v1.parse_credit_year(a.year,credit_rows,credit_ev,now);tsf_obs=v1.parse_tsf(a.year,tsf_rows,tsf_ev,now)
        roots=newer(credit_obs+tsf_obs,obs_latest)
        for r in roots:r['collector_version']=COLLECTOR_VERSION

        # Deterministic monthly changes use the current table itself. YoY uses a
        # persisted prior-year parent when available; absence does not block new
        # root data from advancing.
        prev_values=store_values(persisted_prev,a.year-1)
        try:
            all_der=v1.derive_credit(a.year,curr_values,prev_values,credit_ev,now)
            derived=newer(all_der,der_latest)
            for r in derived:r['collector_version']=COLLECTOR_VERSION
        except Exception as e:
            diagnostics.append({'reason':'DERIVED_SERIES_PARTIAL_PARENT_UNAVAILABLE','error':repr(e),'root_collection_remains_valid':True})
            # Build stock-to-monthly-change subset without requiring prior-year YoY.
            flow_map={
                'FISC_DEPOSIT_STOCK':('FISC_DEPOSIT_CHANGE','FISC_DEPOSIT_STOCK_CHANGE_V1'),
                'CC_RMB_LOAN_STOCK':('CC_RMB_LOAN_INCREMENT','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
                'CC_CORP_ST_LOAN_STOCK':('CC_CORP_ST_LOAN','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
                'CC_CORP_LT_LOAN_STOCK':('CC_CORP_LT_LOAN','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
                'CC_HH_ST_LOAN_STOCK':('CC_HH_ST_LOAN','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
                'CC_HH_LT_LOAN_STOCK':('CC_HH_LT_LOAN','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
                'CC_BILL_FINANCING_STOCK':('CC_BILL_FINANCING','CREDIT_STOCK_TO_MONTHLY_CHANGE_V1'),
            }
            tmp=[]
            for parent,(sid,method) in flow_map.items():
                vals=curr_values.get(parent,{})
                for month,cur in sorted(vals.items()):
                    prior=vals.get(month-1)
                    if prior is None:continue
                    period=f'{a.year}-{month:02d}'
                    row=v1.mk_obs(sid,period,cur-prior,credit_ev,now,f'PBOC_RMB_CREDIT_TABLE_{period}')
                    row['method_version']=method;row['parent_series_ids']=[parent];row['parent_periods']=[period,f'{a.year}-{month-1:02d}'];row['collector_version']=COLLECTOR_VERSION
                    tmp.append(row)
            derived=newer(tmp,der_latest)
        evidence={'current_sections':sections,'credit_section':credit_section_ev,'tsf_section':tsf_section_ev,'credit_table':credit_ev,'tsf_table':tsf_ev}
    except Exception as e:gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'LATEST_OFFICIAL_SOURCE_OR_PARSE_FAILURE','error':repr(e)})
    mode='NEW_LATEST_DATA' if (roots or derived) and not gaps else ('NO_NEW_RELEASE_CARRY_STORE' if not gaps else 'INCOMPLETE')
    run={'module':'china_financial_pbc_monthly_credit_incremental','collector_version':COLLECTOR_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'root_observation_count':len(roots),'derived_observation_count':len(derived),'gap_count':len(gaps),'diagnostics':diagnostics,'evidence':evidence,'semantic_rules':{'latest_available_official_data_first':True,'subfamilies_may_advance_asynchronously':True,'backtest_asof_filtering_is_downstream':True,'no_new_release_carries_store':True,'historical_backfill_default':False,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',roots),('derived.json',derived),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
