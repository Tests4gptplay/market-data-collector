#!/usr/bin/env python3
"""PBOC broad RRR state-change event collector V2.

V2 preserves V1 and fixes event dating: an article can generate a root only when
its official publication/announcement date equals the requested target date.
Announcement date is the root reference date; effective date stays in dimensions.
Targeted/special-institution RRR adjustments are metadata only.
"""
from __future__ import annotations
import argparse, hashlib, json, re
from datetime import date, datetime, timezone
from pathlib import Path
from collectors.china_financial import rrr_event_family_v1 as v1
from collectors.china_financial.policy_event_family_v1 import fetch, clean_article

COLLECTOR_VERSION="V1.9-CANDIDATE-RRR-EVENT-FAMILY-V2"

def parse_iso_or_cn_date(text:str|None):
    if not text:return None
    m=re.search(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})',text)
    if not m:return None
    try:return date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
    except ValueError:return None

def parse_rrr_article(url:str,target:date):
    final,raw,html=fetch(url); text,_,available=clean_article(html); sha=hashlib.sha256(raw).hexdigest()
    announcement=parse_iso_or_cn_date(available)
    if announcement is None:
        # Conservative fallback: only use an explicit article-level date near the header.
        announcement=parse_iso_or_cn_date(text[:1200])
    if announcement is None:
        raise ValueError('official announcement date could not be established')
    if announcement!=target:
        return None,{"article_url":final,"status":"NOT_TARGET_ANNOUNCEMENT_DATE","announcement_date":announcement.isoformat(),"sha256":sha}

    pats=[
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,140}?下调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点',-1),
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,140}?上调(?:金融机构|存款类金融机构)(?:人民币)?存款准备金率\s*(\d+(?:\.\d+)?)\s*个百分点',1),
      (r'(?:于|从)?\s*(20\d{2}年\d{1,2}月\d{1,2}日)?[^。]{0,180}?(?:金融机构|存款类金融机构)[^。]{0,100}?存款准备金率[^。]{0,50}?下调\s*(\d+(?:\.\d+)?)\s*个百分点',-1),
    ]
    hits=[]
    for pat,sign in pats:
        for m in re.finditer(pat,text):
            eff=v1.parse_cn_date(m.group(1) or '') if m.lastindex and m.group(1) else None
            amount=float(m.group(2) if m.lastindex and m.lastindex>=2 else m.group(1))
            hits.append((round(sign*amount*100.0,8),eff,m.group(0)))
    values=sorted({h[0] for h in hits})
    if not values:
        raise ValueError('target-date official article has no explicit broad RRR change')
    if len(values)!=1:
        raise ValueError(f'ambiguous broad RRR changes: {values}')
    h=next(x for x in hits if x[0]==values[0])
    targeted=[]
    for m in re.finditer(r'([^。]{0,80}(?:汽车金融公司|金融租赁公司|农村信用社|农村商业银行|村镇银行)[^。]{0,140}?存款准备金率[^。]{0,100})',text):
        targeted.append(m.group(1))
    obs={"series_id":"POL_RRR_CHANGE_BPS","reference_date":announcement.isoformat(),"value":values[0],"unit":"bp","provider":"People's Bank of China","source_url":final,"available_at":available,"collector_version":COLLECTOR_VERSION,"evidence_sha256":sha,"dimensions":{"source_semantic":"PBOC_BROAD_REQUIRED_RESERVE_RATIO_CHANGE","effective_date":h[1].isoformat() if h[1] else None,"matched_text":h[2],"targeted_adjustment_context":targeted[:10]}}
    return obs,{"article_url":final,"status":"PARSED","announcement_date":announcement.isoformat(),"effective_date":obs['dimensions']['effective_date'],"sha256":sha}

def collect(target:date):
    gaps=[];obs=[]
    try:
        final,raw,html=fetch(v1.NEWS);p=v1.LP();p.feed(html)
        candidates=[]
        for href,title in p.links:
            if not href or not title:continue
            if not any(k in title for k in ('存款准备金率','降准','准备金')):continue
            from urllib.parse import urljoin
            candidates.append((title,urljoin(final,href)))
        inspected=[]
        for title,url in candidates[:30]:
            try:
                row,ev=parse_rrr_article(url,target);inspected.append({"title":title,"url":url,**ev})
                if row:obs.append(row)
            except Exception as e:
                # A parse failure is a gap only when the article is actually target-dated.
                try:
                    _,_,h=fetch(url);_,_,avail=clean_article(h);ad=parse_iso_or_cn_date(avail)
                except Exception:ad=None
                if ad==target:
                    gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"source_url":url,"reason":"TARGET_ARTICLE_PARSE_FAILURE","error":repr(e)})
                inspected.append({"title":title,"url":url,"status":"PARSE_ERROR_NON_TARGET" if ad!=target else "PARSE_ERROR_TARGET","error":repr(e)})
        if len(obs)>1:
            vals=sorted({float(x['value']) for x in obs})
            if len(vals)>1:gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"reason":"MULTIPLE_CONFLICTING_BROAD_RRR_EVENTS","values":vals})
        run={"module":"china_financial_rrr_event_family","collector_version":COLLECTOR_VERSION,"target_date":target.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"PASS" if not gaps else "INCOMPLETE","observation_count":len(obs),"gap_count":len(gaps),"event_state":"EVENTS_FOUND" if obs else "NO_EVENT_CONFIRMED","list_url":final,"list_sha256":hashlib.sha256(raw).hexdigest(),"inspected":inspected,"semantic_rules":{"broad_rrr_only":True,"official_announcement_date_must_equal_target":True,"announcement_date_is_root":True,"effective_date_in_dimensions":True,"targeted_adjustments_do_not_replace_broad_rrr":True,"no_event_is_not_zero":True}}
    except Exception as e:
        gaps.append({"family":"RRR_EVENT","target_date":target.isoformat(),"source_url":v1.NEWS,"reason":"SOURCE_LIST_FAILURE","error":repr(e)})
        run={"module":"china_financial_rrr_event_family","collector_version":COLLECTOR_VERSION,"target_date":target.isoformat(),"completed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"status":"INCOMPLETE","observation_count":0,"gap_count":len(gaps)}
    return obs,gaps,run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    obs,gaps,run=collect(date.fromisoformat(a.date))
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if run['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
