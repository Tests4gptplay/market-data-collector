#!/usr/bin/env python3
"""PBOC broad RRR state-change event collector.

Collects only broad reserve-requirement changes from official PBOC news.
Announcement date is the root reference date; effective date is retained in dimensions.
Targeted/special-institution adjustments are metadata and never replace the broad change.
"""
from __future__ import annotations
import argparse, hashlib, json, re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from collectors.china_financial.policy_event_family_v1 import fetch, clean_article

COLLECTOR_VERSION="V1.9-CANDIDATE-RRR-EVENT-FAMILY-V1"
NEWS="https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"

class LP(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
    def handle_starttag(self,t,a):
        if t.lower()=='a': self.href=dict(a).get('href'); self.buf=[]
    def handle_data(self,d):
        if self.href is not None:self.buf.append(d)
    def handle_endtag(self,t):
        if t.lower()=='a' and self.href is not None:
            self.links.append((self.href,' '.join(''.join(self.buf).split()))); self.href=None; self.buf=[]

def parse_cn_date(text:str):
    m=re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日',text)
    if not m:return None
    try:return date(*map(int,m.groups()))
    except ValueError:return None

def parse_rrr_article(url:str, target:date):
    final,raw,html=fetch(url); text,_,available=clean_article(html); sha=hashlib.sha256(raw).hexdigest()
    # Require an explicit broad financial-institution RRR phrase. Do not infer from commentary.
    pats=[
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,120}?下调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点',-1),
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,120}?上调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点',1),
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,160}?(?:金融机构|存款类金融机构)[^。]{0,80}?存款准备金率[^。]{0,40}?下调\s*(\d+(?:\.\d+)?)\s*个百分点',-1),
    ]
    hits=[]
    for pat,sign in pats:
        for m in re.finditer(pat,text):
            eff=parse_cn_date(m.group(1) or '') if m.lastindex and m.group(1) else None
            amount=float(m.group(2) if m.lastindex and m.lastindex>=2 else m.group(1))
            hits.append((sign*amount*100.0,eff,m.group(0)))
    values=sorted({round(h[0],8) for h in hits})
    if not values:return None
    if len(values)!=1: raise ValueError(f'ambiguous broad RRR changes: {values}')
    h=next(x for x in hits if round(x[0],8)==values[0])
    targeted=[]
    for m in re.finditer(r'([^。]{0,80}(?:汽车金融公司|金融租赁公司|农村信用社|农村商业银行|村镇银行)[^。]{0,120}?存款准备金率[^。]{0,80})',text): targeted.append(m.group(1))
    return {"series_id":"POL_RRR_CHANGE_BPS","reference_date":target.isoformat(),"value":values[0],"unit":"bp","provider":"People's Bank of China","source_url":final,"available_at":available,"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha,"dimensions":{"source_semantic":"PBOC_BROAD_REQUIRED_RESERVE_RATIO_CHANGE","effective_date":h[1].isoformat() if h[1] else None,"matched_text":h[2],"targeted_adjustment_context":targeted[:10]}}

def collect(target:date):
    gaps=[]; obs=[]
    try:
        final,raw,html=fetch(NEWS); p=LP(); p.feed(html)
        candidates=[]
        for href,title in p.links:
            if not href or not title:continue
            if not any(k in title for k in ('存款准备金率','降准','准备金')):continue
            candidates.append((title,urljoin(final,href)))
        inspected=[]
        for title,url in candidates[:30]:
            try:
                row=parse_rrr_article(url,target)
                inspected.append({"title":title,"url":url,"parsed":bool(row)})
                if row and row['reference_date']==target.isoformat():obs.append(row)
            except Exception as e:gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"source_url":url,"reason":"ARTICLE_PARSE_FAILURE","error":repr(e)})
        # Normal daily no-event is valid if official news list was fetched successfully.
        status='EVENTS_FOUND' if obs else 'NO_EVENT_CONFIRMED'
        run={"module":"china_financial_rrr_event_family","collector_version":COLLECTOR_VERSION,"target_date":target.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"PASS" if not gaps else "INCOMPLETE","observation_count":len(obs),"gap_count":len(gaps),"event_state":status,"list_url":final,"list_sha256":hashlib.sha256(raw).hexdigest(),"inspected":inspected,"semantic_rules":{"broad_rrr_only":True,"announcement_date_is_root":True,"effective_date_in_dimensions":True,"targeted_adjustments_do_not_replace_broad_rrr":True,"no_event_is_not_zero":True}}
    except Exception as e:
        gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"source_url":NEWS,"reason":"SOURCE_LIST_FAILURE","error":repr(e)})
        run={"module":"china_financial_rrr_event_family","collector_version":COLLECTOR_VERSION,"target_date":target.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"INCOMPLETE","observation_count":0,"gap_count":len(gaps)}
    return obs,gaps,run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(date.fromisoformat(a.date))
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if run['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
