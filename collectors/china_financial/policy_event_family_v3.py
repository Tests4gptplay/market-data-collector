#!/usr/bin/env python3
"""PBOC policy-event collector V3.

Preserves V1/V2 and fixes OMO7D discovery dating: PBC list titles may expose only
an annual sequence label such as [2026], so the target operation date is taken
from the official article body, never inferred solely from the list title.
POL_OMO7D_RATE remains a state-change root. A zero-volume 7D operation contains
no fresh policy-rate quote and therefore emits no rate root.
"""
from __future__ import annotations
import argparse, hashlib, json, re
from datetime import date, datetime, timezone
from pathlib import Path
from collectors.china_financial import policy_event_family_v1 as v1
from collectors.china_financial import policy_event_family_v2 as v2

COLLECTOR_VERSION='V1.9-CANDIDATE-POLICY-EVENT-FAMILY-V3'

def article_operation_date(url:str):
    final,raw,html=v1.fetch(url);text,_,available=v1.clean_article(html)
    # Prefer explicit Gregorian date in operation sentence; fall back to first visible date.
    pats=[
      r'(20\d{2})年(\d{1,2})月(\d{1,2})日[^。\n]{0,120}?(?:逆回购|公开市场)',
      r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})',
    ]
    for pat in pats:
        m=re.search(pat,text)
        if m:
            try:return date(int(m.group(1)),int(m.group(2)),int(m.group(3))),final,hashlib.sha256(raw).hexdigest(),available
            except ValueError:pass
    return None,final,hashlib.sha256(raw).hexdigest(),available

def collect_omo7d_rate_change(target:date):
    observations=[];gaps=[]
    try:links,list_ev=v1.article_links('OMO',v1.SOURCES['OMO'])
    except Exception as e:
        return [],[{'family':'OMO7D_RATE','target_date':target.isoformat(),'reason':'SOURCE_LIST_FAILURE','error':repr(e)}],{'status':'SOURCE_FAILURE'}
    parsed=[];target_articles=[];inspected=0
    for item in links[:60]:
        try:
            op_date,final,sha,available=article_operation_date(item['url']);inspected+=1
            if op_date is None:continue
            if op_date==target:target_articles.append(final)
            if op_date>target or (target-op_date).days>45:continue
            rate=v2.parse_7d_rate_from_article(item['url'])
            if rate:parsed.append({'date':op_date,'title':item['title'],**rate})
        except Exception as e:
            # A target-dated article parse failure is a real gap; unrelated history is diagnostic only.
            try:op_date,_,_,_=article_operation_date(item['url'])
            except Exception:op_date=None
            if op_date==target:gaps.append({'family':'OMO7D_RATE','target_date':target.isoformat(),'source_url':item['url'],'reason':'TARGET_ARTICLE_RATE_PARSE_FAILURE','error':repr(e)})
    parsed.sort(key=lambda x:x['date'],reverse=True)
    cur=[x for x in parsed if x['date']==target];prev=[x for x in parsed if x['date']<target]
    if not cur:
        return observations,gaps,{'status':'NO_7D_RATE_EVENT_CONFIRMED' if target_articles else 'NO_TARGET_OMO_NOTICE','target_date':target.isoformat(),'list_evidence':list_ev,'target_article_count':len(target_articles),'inspected_articles':inspected}
    rates={x['rate'] for x in cur}
    if len(rates)!=1:
        gaps.append({'family':'OMO7D_RATE','target_date':target.isoformat(),'reason':'TARGET_RATE_CONFLICT','rates':sorted(rates)})
        return observations,gaps,{'status':'INCOMPLETE','target_date':target.isoformat()}
    if not prev:
        gaps.append({'family':'OMO7D_RATE','target_date':target.isoformat(),'reason':'PREVIOUS_STATE_NOT_ESTABLISHED'})
        return observations,gaps,{'status':'INCOMPLETE','target_date':target.isoformat()}
    c=cur[0];p=prev[0];change=round((c['rate']-p['rate'])*100,8)
    if abs(change)>1e-9:
        observations.append({'series_id':'POL_OMO7D_RATE','reference_date':target.isoformat(),'value':c['rate'],'unit':'percent','provider':"People's Bank of China",'source_url':c['source_url'],'available_at':c['available_at'],'collector_version':COLLECTOR_VERSION,'evidence_sha256':c['sha256'],'dimensions':{'source_semantic':'PBOC_7D_REVERSE_REPO_POLICY_RATE_STATE_CHANGE','previous_rate':p['rate'],'previous_reference_date':p['date'].isoformat(),'change_bps':change,'selection_rule':c['selection']}})
        status='RATE_CHANGE_FOUND'
    else:status='UNCHANGED_QUOTE_CONFIRMED'
    return observations,gaps,{'status':status,'target_date':target.isoformat(),'current_rate':c['rate'],'previous_rate':p['rate'],'previous_reference_date':p['date'].isoformat(),'change_bps':change,'list_evidence':list_ev,'target_article_count':len(target_articles),'inspected_articles':inspected}

def collect(target:date):
    observations,gaps,run=v1.collect(target);ro,rg,rr=collect_omo7d_rate_change(target);observations.extend(ro);gaps.extend(rg)
    out=dict(run);out.update({'collector_version':COLLECTOR_VERSION,'completed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'status':'PASS' if not gaps else 'INCOMPLETE','observation_count':len(observations),'gap_count':len(gaps),'omo7d_rate_state':rr})
    return observations,gaps,out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);obs,gaps,run=collect(date.fromisoformat(a.date))
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if run['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
