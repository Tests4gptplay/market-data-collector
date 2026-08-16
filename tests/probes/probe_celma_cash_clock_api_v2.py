#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from html.parser import HTMLParser
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen

BASE='https://www.celma.org.cn/'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'

def get_bytes(url,tries=4):
    last=None
    for i in range(tries):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept':'*/*','Referer':BASE+'zqsc/index.jhtml','Connection':'close'})
            with urlopen(req,timeout=45) as r:return r.geturl(),r.read(),dict(r.headers)
        except Exception as e:
            last=e
            if i+1<tries:time.sleep(2*(i+1))
    raise last

def get_text(url):
    f,b,h=get_bytes(url)
    return f,b.decode('utf-8','replace'),h

main_url=BASE+'zqsc/index.jhtml'
final,html,_=get_text(main_url)
# dataServiceUrl is assigned in page JS; print nearby assignment, and test same-origin fallback.
assign=[]
for m in re.finditer(r'dataServiceUrl\s*=\s*["\']([^"\']+)',html):assign.append(m.group(1))
for m in re.finditer(r'(?:var|let|const)\s+dataServiceUrl\s*=\s*([^;\n]+)',html):assign.append(m.group(1).strip())
print('DATA_SERVICE_ASSIGNMENTS',json.dumps(assign,ensure_ascii=False))

# Most deployments use same-origin /sjcx or an absolute service URL. Derive any URL literals near dataServiceUrl too.
ctx=[]
for m in re.finditer('dataServiceUrl',html):ctx.append(html[max(0,m.start()-250):m.start()+500])
print('DATA_SERVICE_CONTEXTS',json.dumps(ctx[:8],ensure_ascii=False,indent=2))

bases=[]
for x in assign:
    if x.startswith('http'):bases.append(x.rstrip('/'))
    elif x.startswith('/'):bases.append(urljoin(BASE,x).rstrip('/'))
# same-origin fallback is worth testing if assignment is generated server-side.
bases += [BASE.rstrip('/')]
# preserve order
seen=set(); bases=[x for x in bases if not (x in seen or seen.add(x))]

params={
 'dataType':'ZQFXLISTBYAD','adList':'','adCode':'87','zqlx':'','year':'2026',
 'fxfs':'','qxr':'','fxqx':'','zqCode':'','zqName':'','page':'1','pageSize':'3'
}
api_results=[]
for b in bases:
    u=b+'/api/loadBondData.action?'+urlencode(params)
    try:
        f,raw,h=get_bytes(u)
        text=raw.decode('utf-8','replace')
        rec={'url':f,'status':'OK','content_type':h.get('Content-Type'),'bytes':len(raw),'head':text[:1000]}
        try:
            obj=json.loads(text);rec['json_keys']=list(obj) if isinstance(obj,dict) else None
            rec['json']=obj
        except Exception as e:rec['json_error']=repr(e)
        api_results.append(rec)
    except Exception as e:api_results.append({'url':u,'status':'ERR','error':repr(e)})
print('OVERVIEW_API_RESULTS',json.dumps(api_results,ensure_ascii=False,indent=2))

# Probe announcement list channels 194 issuance results and 196 debt service.
for ch in ('194','196'):
    u=BASE+'zqsclb.jhtml?'+urlencode({'ad_code':'87','channelId':ch})
    try:
        f,h,_=get_text(u)
        print('\nCHANNEL',ch,'URL',f,'LEN',len(h))
        for needle in ('api/','ajax','pageSize','channelId','contentId','发行','还本','付息','releaseDate'):
            hits=[]
            for m in list(re.finditer(needle,h,re.I))[:12]:hits.append(h[max(0,m.start()-220):m.start()+700])
            if hits:print('HITS',needle,json.dumps(hits,ensure_ascii=False,indent=2))
        links=re.findall(r'href=["\']([^"\']+)["\']',h,re.I)
        print('LINKS',json.dumps([urljoin(f,x) for x in links if x and not x.startswith('javascript')][:40],ensure_ascii=False,indent=2))
    except Exception as e:print('CHANNEL_ERR',ch,repr(e))

# Monthly page: expose API parameters / fields for principal and interest validation.
try:
    f,h,_=get_text(BASE+'ydsj/index.jhtml')
    print('\nMONTHLY_URL',f,'LEN',len(h))
    for needle in ('dataServiceUrl','loadBondData','本金','还本','利息','付息','dataType','month','year','api/'):
        hits=[]
        for m in list(re.finditer(needle,h,re.I))[:15]:hits.append(h[max(0,m.start()-240):m.start()+850])
        if hits:print('MONTHLY_HITS',needle,json.dumps(hits,ensure_ascii=False,indent=2))
except Exception as e:print('MONTHLY_ERR',repr(e))
