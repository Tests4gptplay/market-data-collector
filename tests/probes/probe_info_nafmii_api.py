#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

BASE='https://info.nafmii.org.cn/'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
class S(HTMLParser):
 def __init__(self): super().__init__(); self.scripts=[]
 def handle_starttag(self,t,a):
  if t.lower()=='script':
   src=dict(a).get('src')
   if src:self.scripts.append(src)
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
f,h=get(BASE); p=S(); p.feed(h)
out={'page':f,'scripts':[],'candidates':[]}
for src in p.scripts:
 u=urljoin(f,src)
 try:
  ff,js=get(u); out['scripts'].append({'url':ff,'len':len(js)})
  # collect likely API paths and contexts around issuance/trend keywords
  for pat in (r'https?://[^"\' ]+',r'/(?:api|gateway|bond|issue|statistics|statistic|data)[A-Za-z0-9_./?=&${}-]*'):
   for m in re.finditer(pat,js,re.I):
    s=m.group(0)
    if any(k in s.lower() for k in ('issue','issu','bond','stat','trend','data')):
     out['candidates'].append({'script':ff,'value':s[:500]})
  for kw in ('总发行量','发行情况','totalIssue','issueTrend','issuance','发行量趋势'):
   for m in list(re.finditer(kw,js,re.I))[:10]:
    out['candidates'].append({'script':ff,'keyword':kw,'context':js[max(0,m.start()-500):m.start()+900]})
 except Exception as e: out['scripts'].append({'url':u,'error':repr(e)})
# de-dupe exact candidate values/contexts
seen=set(); ded=[]
for x in out['candidates']:
 key=json.dumps(x,ensure_ascii=False,sort_keys=True)
 if key in seen:continue
 seen.add(key); ded.append(x)
out['candidates']=ded[:150]
print(json.dumps(out,ensure_ascii=False,indent=2))
