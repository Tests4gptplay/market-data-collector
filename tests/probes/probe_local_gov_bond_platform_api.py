#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

BASE='https://www.celma.org.cn/'
PAGES=['zqsc/index.jhtml','ydsj/index.jhtml']
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
class S(HTMLParser):
    def __init__(self):super().__init__();self.scripts=[]
    def handle_starttag(self,t,a):
        if t.lower()=='script':
            x=dict(a).get('src')
            if x:self.scripts.append(x)
def get(u,n=4):
    last=None
    for i in range(n):
        try:
            q=Request(u,headers={'User-Agent':UA,'Accept':'*/*','Connection':'close'})
            with urlopen(q,timeout=45) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as e:
            last=e
            if i+1<n:time.sleep(2*(i+1))
    raise last
out={'pages':[],'scripts':[],'candidates':[]}
for page in PAGES:
    f,h=get(urljoin(BASE,page));p=S();p.feed(h);out['pages'].append({'url':f,'len':len(h),'scripts':p.scripts})
    blobs=[('HTML',f,h)]
    for src in p.scripts:
        u=urljoin(f,src)
        try:ff,js=get(u);blobs.append(('JS',ff,js));out['scripts'].append({'url':ff,'len':len(js)})
        except Exception as e:out['scripts'].append({'url':u,'error':repr(e)})
    for kind,u,blob in blobs:
        for kw in ('ajax','DataTable','发行结果','还本付息','zqxx','fxjg','hbx','ydsj','export','query','pageSize','startDate','endDate'):
            for m in list(re.finditer(kw,blob,re.I))[:25]:
                out['candidates'].append({'kind':kind,'source':u,'keyword':kw,'context':blob[max(0,m.start()-350):m.start()+1000]})
        for m in re.finditer(r'["\']([^"\']*(?:\.jhtml|\.json|/api/|ajax|query|search|export)[^"\']*)["\']',blob,re.I):
            v=m.group(1)
            if len(v)<500:out['candidates'].append({'kind':kind,'source':u,'value':v})
seen=set();ded=[]
for x in out['candidates']:
    k=json.dumps(x,ensure_ascii=False,sort_keys=True)
    if k not in seen:seen.add(k);ded.append(x)
out['candidates']=ded[:250]
print(json.dumps(out,ensure_ascii=False,indent=2))
