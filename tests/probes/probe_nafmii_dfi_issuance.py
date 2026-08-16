#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

URL='https://www.nafmii.org.cn/sjtj/fx/'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'

class P(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a': self.href=dict(attrs).get('href'); self.buf=[]
    def handle_data(self,d):
        if self.href is not None: self.buf.append(d)
    def handle_endtag(self,tag):
        if tag.lower()=='a' and self.href is not None:
            self.links.append((self.href,' '.join(''.join(self.buf).split()))); self.href=None; self.buf=[]

def get(url,attempts=4):
    last=None
    for i in range(attempts):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(req,timeout=45) as r:
                raw=r.read(); ctype=r.headers.get('Content-Type','')
                for enc in ('utf-8','gb18030'):
                    try: text=raw.decode(enc); break
                    except UnicodeDecodeError: pass
                else: text=raw.decode('utf-8','replace')
                return r.geturl(),raw,text,ctype,i+1
        except Exception as e:
            last=e
            if i+1<attempts: time.sleep(2*(i+1))
    raise last

final,raw,html,ctype,attempt=get(URL); p=P(); p.feed(html)
items=[]
for href,title in p.links:
    if not href or not re.search(r'2026年\d+月债务融资工具发行统计',title): continue
    full=urljoin(final,href)
    item={'title':title,'url':full}
    try:
        f,r,h,ct,a=get(full); q=P(); q.feed(h)
        item.update({'final_url':f,'sha256':hashlib.sha256(r).hexdigest(),'content_type':ct,'attempts':a,
                     'links':[{'title':t,'url':urljoin(f,u)} for u,t in q.links if u and (re.search(r'\.(?:xls|xlsx|csv|pdf)(?:\?|$)',u,re.I) or '下载' in t or '文件' in t)][:20],
                     'text_contexts':[h[max(0,m.start()-250):m.start()+800] for m in list(re.finditer(r'(?:发行规模|发行金额|亿元|合计|总计)',h))[:8]]})
    except Exception as e: item['error']=repr(e)
    items.append(item)
print(json.dumps({'list_url':final,'list_sha256':hashlib.sha256(raw).hexdigest(),'content_type':ctype,'attempts':attempt,'count':len(items),'items':items},ensure_ascii=False,indent=2))
