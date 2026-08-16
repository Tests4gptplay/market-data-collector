#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
LIST='https://gks.mof.gov.cn/tongjishuju/'

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a': self.href=dict(attrs).get('href'); self.buf=[]
    def handle_data(self,data):
        if self.href is not None: self.buf.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=='a' and self.href is not None:
            self.links.append((self.href,' '.join(''.join(self.buf).split()))); self.href=None; self.buf=[]
class TextParser(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower() in ('p','div','li','br','tr','td','h1','h2','h3'): self.parts.append('\n')
    def handle_data(self,data): self.parts.append(data)

def get(url,attempts=4):
    last=None
    for i in range(attempts):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,*/*','Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(req,timeout=45) as r:
                raw=r.read()
                for enc in ('utf-8','gb18030'):
                    try: text=raw.decode(enc); break
                    except UnicodeDecodeError: pass
                else: text=raw.decode('utf-8','replace')
                return r.geturl(),raw,text,{'attempts_used':i+1,'status':getattr(r,'status',200)}
        except Exception as e:
            last=e
            if i+1<attempts: time.sleep(2*(i+1))
    raise last

def clean(html):
    p=TextParser(); p.feed(html)
    return '\n'.join(x.strip() for x in ''.join(p.parts).splitlines() if x.strip())

final,raw,html,list_fetch=get(LIST); p=LinkParser(); p.feed(html)
items=[]
for href,title in p.links:
    if not href or not title: continue
    if '2026' not in title or '财政收支情况' not in title: continue
    url=urljoin(final,href)
    try:
        f,r,h,fetch_meta=get(url); text=clean(h)
        contexts=[]
        for needle in ('全国一般公共预算收入','全国一般公共预算支出','全国政府性基金预算收入','全国政府性基金预算支出'):
            m=text.find(needle)
            contexts.append({'needle':needle,'context':text[max(0,m-120):m+420] if m>=0 else None})
        meta=re.search(r'<meta\s+name=["\']createDate["\']\s+content=["\']([^"\']+)',h,re.I)
        items.append({'title':title,'url':f,'sha256':hashlib.sha256(r).hexdigest(),'createDate':meta.group(1) if meta else None,'fetch':fetch_meta,'contexts':contexts})
    except Exception as e:
        items.append({'title':title,'url':url,'error':repr(e)})
print(json.dumps({'list_url':final,'list_sha256':hashlib.sha256(raw).hexdigest(),'list_fetch':list_fetch,'count':len(items),'items':items},ensure_ascii=False,indent=2))
