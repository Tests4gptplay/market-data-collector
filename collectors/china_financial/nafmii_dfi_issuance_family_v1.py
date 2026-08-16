#!/usr/bin/env python3
"""NAFMII monthly debt-financing-instrument gross issuance collector.

Root: CRD_DFI_GROSS_ISSUANCE_MONTHLY. Uses official NAFMII monthly issuance
statistics PDFs and pure-Python pypdf extraction. Each release contributes only
its own headline month, preventing later PDFs from silently revising prior months.
"""
from __future__ import annotations
import argparse,hashlib,json,re,time
from datetime import datetime,timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request,urlopen
from pypdf import PdfReader

COLLECTOR_VERSION='V1.9-CANDIDATE-NAFMII-DFI-ISSUANCE-V1'
LIST='https://www.nafmii.org.cn/sjtj/fx/'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'

class Links(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.links=[];self.href=None;self.buf=[]
    def handle_starttag(self,t,a):
        if t.lower()=='a':self.href=dict(a).get('href');self.buf=[]
    def handle_data(self,d):
        if self.href is not None:self.buf.append(d)
    def handle_endtag(self,t):
        if t.lower()=='a' and self.href is not None:self.links.append((self.href,' '.join(''.join(self.buf).split())));self.href=None;self.buf=[]

def get(url,attempts=4):
    last=None
    for i in range(attempts):
        try:
            q=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(q,timeout=60) as r:return r.geturl(),r.read(),i+1
        except Exception as e:
            last=e
            if i+1<attempts:time.sleep(2*(i+1))
    raise last

def discover(year:int):
    final,raw,attempts=get(LIST);p=Links();p.feed(raw.decode('utf-8','replace'));found={}
    pat=re.compile(rf'{year}年(\d{{1,2}})月债务融资工具发行统计')
    for href,title in p.links:
        m=pat.search(title or '')
        if not href or not m:continue
        month=int(m.group(1));found[month]={'month':month,'title':title,'url':urljoin(final,href)}
    return [found[k] for k in sorted(found)],{'list_url':final,'list_sha256':hashlib.sha256(raw).hexdigest(),'attempts':attempts}

def pdf_text(raw:bytes):
    reader=PdfReader(BytesIO(raw));return '\n'.join((p.extract_text(extraction_mode='layout') or '') for p in reader.pages),len(reader.pages)

def parse_release(year:int,month:int,title:str,url:str,retrieved_at:str):
    final,raw,attempts=get(url);text,pages=pdf_text(raw)
    title_month=re.search(rf'{year}\s*年\s*{month}\s*月债务融资工具发行统计',text)
    cutoff=re.search(rf'数据截至\s*{year}\s*年\s*{month}\s*月末',text)
    if not title_month:raise ValueError('PDF internal title month mismatch')
    if not cutoff:raise ValueError('PDF cutoff month mismatch')
    first=text.split('二、主要品种发行量',1)[0]
    # Parse the headline table; rows may show either 2026.1 or an indented month number.
    row_pat=re.compile(r'^\s*(?:(\d{4})\.(\d{1,2})|(\d{1,2}))\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$',re.M)
    rows={}
    for m in row_pat.finditer(first):
        ry=int(m.group(1)) if m.group(1) else year;rm=int(m.group(2) or m.group(3));families=int(m.group(4).replace(',',''));issues=int(m.group(5).replace(',',''));amount=float(m.group(6).replace(',',''))
        if ry==year:rows[rm]=(families,issues,amount,m.group(0).strip())
    if month not in rows:raise ValueError(f'headline row for month {month} not found; parsed={sorted(rows)}')
    families,issues,amount,row=rows[month]
    sha=hashlib.sha256(raw).hexdigest()
    obs={'series_id':'CRD_DFI_GROSS_ISSUANCE_MONTHLY','reference_period':f'{year}-{month:02d}','value':amount,'unit':'CNY_100M','provider':'NAFMII','source_url':final,'retrieved_at':retrieved_at,'collector_version':COLLECTOR_VERSION,'evidence_sha256':sha,'dimensions':{'source_semantic':'NAFMII_MONTHLY_DFI_GROSS_ISSUANCE','issuer_count':families,'issue_count':issues,'headline_row':row,'pdf_pages':pages,'title':title}}
    return obs,{'reference_period':obs['reference_period'],'url':final,'sha256':sha,'attempts':attempts,'pages':pages,'amount':amount}

def collect(year:int):
    now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');obs=[];gaps=[];ev=[];releases=[];list_ev={}
    try:
        releases,list_ev=discover(year)
        for x in releases:
            try:o,e=parse_release(year,x['month'],x['title'],x['url'],now);obs.append(o);ev.append(e)
            except Exception as exc:gaps.append({'family':'NAFMII_DFI_ISSUANCE','reference_period':f"{year}-{x['month']:02d}",'source_url':x['url'],'reason':'OFFICIAL_PDF_FETCH_OR_PARSE_FAILURE','error':repr(exc)})
    except Exception as exc:gaps.append({'family':'NAFMII_DFI_ISSUANCE','year':year,'source_url':LIST,'reason':'LIST_OR_DISCOVERY_FAILURE','error':repr(exc)})
    if releases and len(obs)!=len(releases):gaps.append({'family':'NAFMII_DFI_ISSUANCE','year':year,'reason':'COUNT_GATE_FAILED','expected':len(releases),'actual':len(obs)})
    run={'module':'china_financial_nafmii_dfi_issuance','collector_version':COLLECTOR_VERSION,'year':year,'completed_at':now,'status':'PASS' if not gaps else 'INCOMPLETE','release_count':len(releases),'observation_count':len(obs),'gap_count':len(gaps),'list_evidence':list_ev,'release_evidence':ev,'semantic_rules':{'own_release_month_only':True,'pdf_internal_title_must_match':True,'pdf_cutoff_month_must_match':True,'later_pdf_revisions_do_not_silently_overwrite_prior_vintage':True,'unknown_is_never_zero':True}}
    return obs,gaps,run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True);obs,gaps,run=collect(a.year)
    for n,v in [('observations.json',obs),('gaps.json',gaps),('run.json',run)]: (out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2));return 0 if run['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
