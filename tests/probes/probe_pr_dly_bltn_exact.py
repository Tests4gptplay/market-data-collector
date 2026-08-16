#!/usr/bin/env python3
from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT='https://www.chinamoney.com.cn'
PAGE=ROOT+'/chinese/mtdexdaily/?tab=2'
SUB=ROOT+'/r/cms/chinese/chinamoney/html/currency/daily-pledged-repo.html'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
TARGET='2026-08-14'

class Assets(HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        for k in ('src','href'):
            if d.get(k): self.urls.append(d[k])

def request(url,method='GET',data=None,referer=None,ctype=None):
    h={'User-Agent':UA,'Accept':'*/*','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.5','Connection':'close'}
    if referer: h['Referer']=referer
    if ctype: h['Content-Type']=ctype
    req=Request(url,data=data,headers=h,method=method)
    try:
        with urlopen(req,timeout=40) as r:
            raw=r.read(); return {'ok':True,'status':getattr(r,'status',200),'url':r.geturl(),'text':raw.decode('utf-8','replace')}
    except HTTPError as e:
        return {'ok':False,'status':e.code,'url':url,'text':e.read().decode('utf-8','replace')[:4000]}
    except Exception as e:
        return {'ok':False,'status':None,'url':url,'text':repr(e)}

def constants(text):
    pats=[r'\bBK_URL\s*=\s*([^;\n]+)',r'\bDURL\s*=\s*([^;\n]+)',r'\bDQSURL\s*=\s*([^;\n]+)',r'\bPR_PUBLISHED_TIME\s*=\s*([^;\n]+)']
    out=[]
    for p in pats:
        for m in re.finditer(p,text): out.append({'pattern':p,'value':m.group(1).strip(),'context':text[max(0,m.start()-120):m.end()+180]})
    return out

result={'page':{},'assets':[],'static':{},'historical_attempts':[]}
for label,url in [('parent',PAGE),('subpage',SUB)]:
    r=request(url,referer=PAGE); result['page'][label]={'status':r['status'],'url':r['url'],'constants':constants(r['text']),'len':len(r['text'])}
    a=Assets(); a.feed(r['text'])
    for u in a.urls:
        full=urljoin(r['url'],u)
        if not full.endswith('.js'): continue
        if any(x['url']==full for x in result['assets']): continue
        jr=request(full,referer=r['url']); cs=constants(jr['text'])
        if cs or 'PrDlyBltn' in jr['text'] or 'PR_PUBLISHED_TIME' in jr['text'] or 'BK_URL' in jr['text']:
            result['assets'].append({'url':full,'status':jr['status'],'len':len(jr['text']),'constants':cs,'hits':[jr['text'][max(0,m.start()-180):m.end()+350] for m in list(re.finditer(r'PrDlyBltn|PR_PUBLISHED_TIME|BK_URL',jr['text']))[:20]]})

for name in ['pr-dly-bltn-mark.json','pr-dly-bltn-interbank-mark.json']:
    u=ROOT+'/r/cms/www/chinamoney/data/currency/'+name
    r=request(u,'POST',b'',SUB,'application/x-www-form-urlencoded; charset=UTF-8')
    item={'url':u,'status':r['status'],'ok':r['ok']}
    if r['ok']:
        try:
            p=json.loads(r['text']); item['data']=p.get('data'); item['records']=(p.get('records') or [])[:30]
        except Exception as e: item['parse_error']=repr(e); item['body']=r['text'][:2000]
    else: item['body']=r['text'][:2000]
    result['static'][name]=item

endpoint_candidates=[ROOT+'/ags/ms/cm-u-dlrp/PrDlyBltn',ROOT+'/ags/ms/cm-u-bk-currency/cm-u-dlrp/PrDlyBltn']
pub_candidates=['','1','2','3','4','5','11','16','17','18','20','21','22','23','24','25']
for ep in endpoint_candidates:
    for index_type,lang in [('markInterBankVOList','en'),('markVOList','cn')]:
        for pub in pub_candidates:
            form={'lang':lang,'indexType':index_type,'searchDate':TARGET}
            if pub!='': form['publishedTime']=pub
            body=urlencode(form).encode()
            r=request(ep,'POST',body,SUB,'application/x-www-form-urlencoded; charset=UTF-8')
            item={'endpoint':ep,'indexType':index_type,'publishedTime':pub,'status':r['status'],'ok':r['ok']}
            if r['ok']:
                try:
                    p=json.loads(r['text']); rec=p.get('records') if isinstance(p,dict) else None
                    item['data']=p.get('data') if isinstance(p,dict) else None
                    item['record_count']=len(rec or [])
                    item['records']=(rec or [])[:25]
                except Exception as e: item['parse_error']=repr(e); item['body']=r['text'][:1500]
            else: item['body']=r['text'][:500]
            result['historical_attempts'].append(item)
            if item.get('record_count',0)>0:
                break
print(json.dumps(result,ensure_ascii=False,indent=2))
