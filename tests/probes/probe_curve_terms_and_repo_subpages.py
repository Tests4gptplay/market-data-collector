#!/usr/bin/env python3
from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT='https://www.chinamoney.com.cn'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
CURVE=ROOT+'/ags/ms/cm-u-bk-currency/ClsYldCurvHis'
CURVE_PAGE=ROOT+'/chinese/bkcurvclosedy/'
CURVES={
 'CGB':('CYCC000',[1,2,3,5,10,30]),
 'CDB':('CYCC021',[10]),
 'MTN_AAA':('CYCC82B',[1,3]),
 'MTN_AAP':('CYCC82D',[3]),
 'MTN_AA':('CYCC82E',[3]),
 'NCD_AAA':('CYCC41B',[0.25,1]),
}
SUBPAGES=[
 ROOT+'/r/cms/chinese/chinamoney/html/currency/prr-cn.html',
 ROOT+'/r/cms/chinese/chinamoney/html/currency/prr-chart.html',
 ROOT+'/r/cms/chinese/chinamoney/html/currency/daily-pledged-repo.html',
]

def req(url,method='GET',data=None,referer=None,accept='*/*',headers=None):
    h={'User-Agent':UA,'Accept':accept,'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.5','Connection':'close'}
    if referer: h['Referer']=referer
    if headers: h.update(headers)
    r=Request(url,data=data,headers=h,method=method)
    try:
        with urlopen(r,timeout=40) as x: return {'ok':True,'status':getattr(x,'status',200),'url':x.geturl(),'text':x.read().decode('utf-8','replace')}
    except HTTPError as e:
        return {'ok':False,'status':e.code,'url':url,'text':e.read().decode('utf-8','replace')[:1000]}
    except Exception as e: return {'ok':False,'status':None,'url':url,'text':repr(e)}

def post_curve(bt,term):
    q={'lang':'CN','reference':'1','bondType':bt,'startDate':'2026-08-14','endDate':'2026-08-14','termId':str(term),'pageNum':'1','pageSize':'50'}
    url=CURVE+'?'+urlencode(q)
    r=req(url,'POST',b'',CURVE_PAGE,'application/json, text/javascript, */*; q=0.01',{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','Origin':ROOT,'X-Requested-With':'XMLHttpRequest'})
    out={'bondType':bt,'termId':term,'status':r['status'],'ok':r['ok'],'url':url}
    if r['ok']:
        try:
            p=json.loads(r['text']); rec=p.get('records',[]) if isinstance(p,dict) else []
            out['records']=[{k:x.get(k) for k in ['newDateValueCN','yearTermStr','maturityYieldStr','currentYieldStr']} for x in rec[:10]]
        except Exception as e: out['parse_error']=repr(e); out['body']=r['text'][:700]
    else: out['body']=r['text'][:700]
    return out

class SrcParser(HTMLParser):
    def __init__(self): super().__init__(); self.src=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        for key in ('src','href','data-url'):
            if d.get(key): self.src.append(d[key])

def hit_context(text):
    needles=['R001','R007','R014','DR001','DR007','weightedRate','avgRate','repo','prr','DURL','currency/','.json','.csv','ajax','url:']
    hits=[]
    for n in needles:
        for m in list(re.finditer(re.escape(n),text,re.I))[:12]:
            hits.append({'needle':n,'context':text[max(0,m.start()-320):min(len(text),m.end()+650)]})
    return hits

result={'curves':[],'subpages':[]}
for name,(bt,terms) in CURVES.items():
    for term in terms:
        x=post_curve(bt,term); x['curve']=name; result['curves'].append(x)
for u in SUBPAGES:
    r=req(u,referer=ROOT+'/chinese/mkdatapm/?tab=2',accept='text/html,*/*')
    item={'url':u,'status':r['status'],'ok':r['ok'],'len':len(r['text']),'hits':hit_context(r['text']),'children':[]}
    if r['ok']:
        p=SrcParser(); p.feed(r['text'])
        seen=set()
        for child in p.src:
            cu=urljoin(u,child)
            if cu in seen or not any(k in cu.lower() for k in ['.js','.html','.json','.csv']): continue
            seen.add(cu); cr=req(cu,referer=u)
            hh=hit_context(cr['text']) if cr['ok'] else []
            if hh: item['children'].append({'url':cu,'status':cr['status'],'len':len(cr['text']),'hits':hh})
    result['subpages'].append(item)
print(json.dumps(result,ensure_ascii=False,indent=2))
