#!/usr/bin/env python3
"""Store-aware MOF fiscal YTD incremental collector V3.

Daily mode checks the official fiscal-statistics list once. If there is no new
eligible YTD release, it emits PASS/NO_NEW_RELEASE and carries the Store. New
release pages only are fetched; PeriodFlow uses the previously persisted YTD
root as its parent instead of re-downloading older releases.
"""
from __future__ import annotations
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import mof_fiscal_ytd_family_v2 as v2

COLLECTOR_VERSION='V1.9-READY-MOF-FISCAL-YTD-INCREMENTAL-V3'
METHOD_VERSION='FISC_YTD_PERIOD_FLOW_V1'


def load_rows(path:Path)->list[dict[str,Any]]:
    if not path.exists():return []
    x=json.loads(path.read_text(encoding='utf-8'));return x if isinstance(x,list) else []


def latest_end_month(rows:list[dict[str,Any]])->int:
    vals=[]
    for r in rows:
        try:vals.append(int(r.get('period_end_month') or 0))
        except Exception:pass
    return max(vals) if vals else 0


def previous_by_series(rows:list[dict[str,Any]])->dict[str,dict[str,Any]]:
    out={}
    for r in rows:
        sid=str(r.get('series_id') or '')
        if sid not in v2.ROOTS:continue
        try:m=int(r.get('period_end_month') or 0)
        except Exception:continue
        if sid not in out or m>int(out[sid].get('period_end_month') or 0):out[sid]=r
    return out


def root_row(sid:str,p:dict[str,Any],value:float,src:dict[str,Any])->dict[str,Any]:
    return {'series_id':sid,'reference_period':p['period_key'],'period_end_month':p['period_end_month'],'period_kind':p['period_kind'],'value':value,'unit':'CNY_100M','provider':"Ministry of Finance of the People's Republic of China",'source_url':src['url'],'source_sha256':src['sha256'],'published_at':src['published_at'],'available_at':src['published_at'],'retrieved_at':src['retrieved_at'],'collector_version':COLLECTOR_VERSION,'vintage_policy':'ONE_RELEASE_ONE_OBSERVATION'}


def flow_row(parent:str,cur:dict[str,Any],prev:dict[str,Any]|None)->dict[str,Any]:
    sid=v2.FLOW_MAP[parent]
    if cur['period_kind']=='JAN_FEB_COMBINED_YTD':
        value=cur['value'];kind='JAN_FEB_COMBINED_PERIOD_FLOW';parents=[cur['reference_period']]
    else:
        if prev is None:raise ValueError(f'missing persisted previous YTD parent for {parent} {cur["reference_period"]}')
        value=cur['value']-prev['value'];kind='INCREMENTAL_PERIOD_FLOW';parents=[cur['reference_period'],prev['reference_period']]
    return {'series_id':sid,'reference_period':cur['reference_period'],'period_end_month':cur['period_end_month'],'period_kind':kind,'value':value,'unit':'CNY_100M','provider':cur['provider'],'source_url':cur['source_url'],'source_sha256':cur['source_sha256'],'published_at':cur['published_at'],'available_at':cur['available_at'],'retrieved_at':cur['retrieved_at'],'collector_version':COLLECTOR_VERSION,'method_version':METHOD_VERSION,'parent_series_ids':[parent],'parent_periods':parents,'lineage_rule':'CURRENT_ELIGIBLE_YTD_MINUS_PREVIOUS_ELIGIBLE_YTD; JAN_FEB_COMBINED_REMAINS_COMBINED'}


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    store_file=Path(a.store_root)/'observations'/str(a.year)/f'{a.year}-mof-fiscal-ytd-v2.json';persisted=load_rows(store_file);stored_end=latest_end_month(persisted);prev=previous_by_series(persisted)
    roots=[];derived=[];gaps=[];release_ev=[];list_ev={};official_latest=0
    try:
        final,raw,html,lmeta=v2.fetch(v2.LIST_URL);lp=v2.LinkParser();lp.feed(html);seen=set();candidates=[]
        for href,title in lp.links:
            if not href or href in seen:continue
            p=v2.period(title,a.year)
            if p is None:continue
            seen.add(href);candidates.append((p,title,urljoin(final,href)))
        candidates.sort(key=lambda x:x[0]['period_end_month']);official_latest=max([x[0]['period_end_month'] for x in candidates],default=0)
        list_ev={'list_url':final,'list_sha256':hashlib.sha256(raw).hexdigest(),'list_fetch':lmeta,'candidate_count':len(candidates)}
        if official_latest<stored_end:raise ValueError(f'official list behind persisted Store: official={official_latest} stored={stored_end}')
        for p,title,url in [x for x in candidates if x[0]['period_end_month']>stored_end]:
            f,r,h,fmeta=v2.fetch(url);text=v2.clean(h);pd=v2.pubdate(text)
            if pd is None:raise ValueError(f'{title}: visible publication date missing')
            src={'url':f,'sha256':hashlib.sha256(r).hexdigest(),'published_at':pd,'retrieved_at':now}
            vals={sid:v2.total(text,label) for sid,label in v2.ROOTS.items()}
            new_roots=[]
            for sid,val in vals.items():
                cur=root_row(sid,p,val,src);new_roots.append(cur);derived.append(flow_row(sid,cur,prev.get(sid)));prev[sid]=cur
            roots.extend(new_roots)
            release_ev.append({'period_key':p['period_key'],'title':title,'url':f,'sha256':src['sha256'],'published_at':pd,'fetch':fmeta})
    except Exception as e:gaps.append({'family':'MOF_FISCAL_YTD','reason':'INCREMENTAL_RELEASE_CHECK_OR_PARSE_FAILURE','error':repr(e)})
    mode='NO_NEW_RELEASE_CARRY_STORE' if not gaps and official_latest<=stored_end else ('NEW_RELEASE_INCREMENT' if not gaps else 'INCOMPLETE')
    run={'module':'china_financial_mof_fiscal_ytd_incremental','collector_version':COLLECTOR_VERSION,'method_version':METHOD_VERSION,'year':a.year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','mode':mode,'stored_latest_period_end_month':stored_end,'official_latest_period_end_month':official_latest,'release_count':len(release_ev),'root_observation_count':len(roots),'derived_observation_count':len(derived),'gap_count':len(gaps),'list_evidence':list_ev,'release_evidence':release_ev,'semantic_rules':{'daily_list_check_only_when_unchanged':True,'only_new_release_details_fetched':True,'previous_ytd_parent_read_from_store':True,'historical_release_recrawl_default':False,'no_new_release_carries_store':True,'unknown_is_never_zero':True}}
    for n,v in [('observations.json',roots),('derived.json',derived),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if not gaps else 2
if __name__=='__main__':raise SystemExit(main())
