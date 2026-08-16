#!/usr/bin/env python3
from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
SOURCES={
 'OMO':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html',
 'BUYOUT':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/index.html',
 'MLF':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125437/125446/index.html',
}

class LinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]; self.cur=None; self.buf=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a':
            d=dict(attrs); self.cur=d.get('href'); self.buf=[]
    def handle_data(self,data):
        if self.cur is not None: self.buf.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=='a' and self.cur is not None:
            self.links.append((self.cur,' '.join(''.join(self.buf).split())))
            self.cur=None; self.buf=[]

def get(url):
    req=Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,*/*','Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
    with urlopen(req,timeout=40) as r:
        raw=r.read()
        for enc in ('utf-8','gb18030'):
            try: text=raw.decode(enc); break
            except UnicodeDecodeError: pass
        else: text=raw.decode('utf-8','replace')
        return r.geturl(),text

def contexts(text,needle):
    out=[]
    for m in list(re.finditer(needle,text,re.I))[:30]: out.append(text[max(0,m.start()-240):m.end()+420])
    return out

res={}
for name,url in SOURCES.items():
    try:
        final,html=get(url); p=LinkParser(); p.feed(html)
        links=[]
        for href,title in p.links:
            if not title: continue
            full=urljoin(final,href)
            if ('2026' in title or '公告' in title or '便利' in title or '逆回购' in title or '招标' in title):
                links.append({'title':title,'url':full})
        res[name]={
          'status':'OK','final_url':final,'html_len':len(html),
          'links':links[:80],
          'date_contexts':contexts(html,r'2026[-年/.]\s*0?8[-月/.]\s*(?:0?[1-9]|1[0-6])'),
          'pagination_contexts':contexts(html,r'下一页|page|分页|总记录|record')[:20]
        }
    except Exception as e:
        res[name]={'status':'ERROR','error':repr(e)}
print(json.dumps(res,ensure_ascii=False,indent=2))
