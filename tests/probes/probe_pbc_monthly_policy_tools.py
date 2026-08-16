#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

URL='https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/5727710/index.html'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
class P(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
 def handle_starttag(self,t,a):
  if t.lower()=='a': self.href=dict(a).get('href'); self.buf=[]
 def handle_data(self,d):
  if self.href is not None: self.buf.append(d)
 def handle_endtag(self,t):
  if t.lower()=='a' and self.href is not None:
   self.links.append((self.href,' '.join(''.join(self.buf).split()))); self.href=None; self.buf=[]
class T(HTMLParser):
 def __init__(self): super().__init__(convert_charrefs=True); self.incell=False; self.cell=[]; self.row=[]; self.rows=[]; self.text=[]
 def handle_starttag(self,t,a):
  if t.lower() in ('td','th'): self.incell=True; self.cell=[]
  if t.lower() in ('br','p','div','tr'): self.text.append('\n')
 def handle_data(self,d):
  self.text.append(d)
  if self.incell: self.cell.append(d)
 def handle_endtag(self,t):
  if t.lower() in ('td','th') and self.incell:
   self.row.append(' '.join(''.join(self.cell).split())); self.incell=False
  elif t.lower()=='tr':
   if any(self.row): self.rows.append(self.row)
   self.row=[]
def get(url,n=5):
 last=None
 for i in range(n):
  try:
   q=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
   with urlopen(q,timeout=45) as r:
    raw=r.read()
    for e in ('utf-8','gb18030'):
     try: s=raw.decode(e); break
     except UnicodeDecodeError: pass
    else:s=raw.decode('utf-8','replace')
    return r.geturl(),raw,s,i+1
  except Exception as e:
   last=e
   if i+1<n: time.sleep(2*(i+1))
 raise last
f,raw,h,a=get(URL); p=P(); p.feed(h); out=[]
for href,title in p.links:
 if not href or not re.search(r'2026年\d+月中央银行各项工具流动性投放情况',title): continue
 u=urljoin(f,href)
 try:
  ff,rr,hh,aa=get(u); t=T(); t.feed(hh)
  out.append({'title':title,'url':ff,'sha256':hashlib.sha256(rr).hexdigest(),'attempts':aa,'rows':t.rows,'text':' '.join(''.join(t.text).split())[:3000]})
 except Exception as e: out.append({'title':title,'url':u,'error':repr(e)})
print(json.dumps({'list_url':f,'list_sha256':hashlib.sha256(raw).hexdigest(),'attempts':a,'count':len(out),'items':out},ensure_ascii=False,indent=2))
