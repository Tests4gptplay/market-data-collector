#!/usr/bin/env python3
"""Store-aware incremental PBC monthly credit collector V2.

Daily mode reads only the current-year official PBC tables. Prior-month and
prior-year parents required for deterministic derived series are loaded from the
persisted Store, not re-fetched from historical PBC pages.
"""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

import pbc_monthly_credit_family_v1 as v1

COLLECTOR_VERSION="V1.9-READY-PBC-MONTHLY-CREDIT-INCREMENTAL-V2"


def load_rows(path:Path)->list[dict[str,Any]]:
    if not path.exists(): return []
    x=json.loads(path.read_text(encoding='utf-8'))
    return x if isinstance(x,list) else []


def max_month(rows:list[dict[str,Any]],year:int)->int:
    out=[]
    for r in rows:
        p=str(r.get('reference_period') or '')
        if p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit(): out.append(int(p[5:7]))
    return max(out) if out else 0


def values_from_store(rows:list[dict[str,Any]],year:int)->dict[str,dict[int,float]]:
    out:dict[str,dict[int,float]]={}
    for r in rows:
        p=str(r.get('reference_period') or '')
        if not (p.startswith(f'{year}-') and len(p)>=7 and p[5:7].isdigit()): continue
        try:v=float(r['value'])
        except Exception:continue
        out.setdefault(str(r.get('series_id') or ''),{})[int(p[5:7])]=v
    return out


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    store=Path(a.store_root);cur_path=store/'observations'/str(a.year)/f'{a.year}-pbc-monthly-credit-v1.json';prev_path=store/'observations'/str(a.year-1)/f'{a.year-1}-pbc-monthly-credit-v1.json'
    persisted=load_rows(cur_path);persisted_prev=load_rows(prev_path);stored_month=max_month(persisted,a.year)
    roots=[];derived=[];gaps=[];evidence={};latest_official_month=0
    try:
        sections=v1.discover_year_sections(a.year)
        credit_url,credit_section_ev=v1.discover_htm(sections['credit'],'金融机构人民币信贷收支表')
        tsf_url,tsf_section_ev=v1.discover_htm(sections['tsf'],'社会融资规模增量统计表')
        credit_rows,credit_ev=v1.parse_table(credit_url);tsf_rows,tsf_ev=v1.parse_table(tsf_url)
        cols,_=v1.month_columns(credit_rows,a.year);latest_official_month=max(cols) if cols else 0
        all_credit,curr_values=v1.parse_credit_year(a.year,credit_rows,credit_ev,now)
        all_tsf=v1.parse_tsf(a.year,tsf_rows,tsf_ev,now)
        # Store is the parent source for old current-year months and prior year.
        store_curr=values_from_store(persisted,a.year);store_prev=values_from_store(persisted_prev,a.year-1)
        for sid,months in store_curr.items(): curr_values.setdefault(sid,{}).update({m:v for m,v in months.items() if m not in curr_values.get(sid,{})})
        all_derived=v1.derive_credit(a.year,curr_values,store_prev,credit_ev,now)
        roots=[r for r in all_credit+all_tsf if int(str(r['reference_period'])[5:7])>stored_month]
        derived=[r for r in all_derived if int(str(r['reference_period'])[5:7])>stored_month]
        evidence={'current_sections':sections,'credit_section':credit_section_ev,'tsf_section':tsf_section_ev,'credit_table':credit_ev,'tsf_table':tsf_ev}
        if latest_official_month<stored_month:
            gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'OFFICIAL_TABLE_BEHIND_PERSISTED_STORE','stored_month':stored_month,'official_month':latest_official_month})
        if latest_official_month>stored_month:
            present_months={int(str(x['reference_period'])[5:7]) for x in roots}
            expected=set(range(stored_month+1,latest_official_month+1))
            if not expected<=present_months:
                gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'NEW_RELEASE_MONTH_ROOTS_INCOMPLETE','expected_months':sorted(expected),'present_months':sorted(present_months)})
    except Exception as e:
        gaps.append({'family':'PBC_MONTHLY_CREDIT','reason':'CURRENT_RELEASE_CHECK_OR_PARSE_FAILURE','error':repr(e)})
    mode='NO_NEW_RELEASE_CARRY_STORE' if not gaps and latest_official_month<=stored_month else ('NEW_RELEASE_INCREMENT' if not gaps else 'INCOMPLETE')
    run={'module':'china_financial_pbc_monthly_credit_incremental','collector_version':COLLECTOR_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'stored_latest_month':stored_month,'official_latest_month':latest_official_month,'root_observation_count':len(roots),'derived_observation_count':len(derived),'gap_count':len(gaps),'evidence':evidence,'semantic_rules':{'daily_current_year_only':True,'historical_web_refetch_default':False,'prior_parents_read_from_store':True,'no_new_release_carries_store':True,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',roots),('derived.json',derived),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
