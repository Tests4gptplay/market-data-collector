#!/usr/bin/env python3
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
SOURCES={
 'OMO':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html',
 'RRR_NEWS':'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html',
}

class LP(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.links=[]; self.href=None; self.buf=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a': self.href=dict(attrs).get('href'); self.buf=[]
    def handle_data(self,data):
        if self.href is not None: self.buf.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=='a' and self.href is not None:
            self.links.append((self.href,' '.join(''.join(self.buf).split()))); self.href=None; self.buf=[]

def fetch(url):
    req=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
    with urlopen(req,timeout=30) as r:
        raw=r.read(); final=r.geturl()
    for enc in ('utf-8','gb18030'):
        try: return final,raw.decode(enc)
        except UnicodeDecodeError: pass
    return final,raw.decode('utf-8','replace')

def page_url(index_url,n):
    if n==0:return index_url
    return index_url.rsplit('/',1)[0]+f'/index{n}.html'

for family,base in SOURCES.items():
    print('\n###',family,base)
    for n in [0,1,2,3,5,10,20,30,40,50,80,100]:
        u=page_url(base,n)
        try:
            final,html=fetch(u); p=LP();p.feed(html)
            rows=[]
            for href,title in p.links:
                if not href or not title: continue
                if family=='OMO' and '公开市场业务交易公告' not in title: continue
                if family=='RRR_NEWS' and not any(k in title for k in ('存款准备金率','降准','准备金')): continue
                rows.append((title,urljoin(final,href)))
            years=sorted(set(re.findall(r'20\d{2}', ' '.join(t for t,_ in rows))))
            print('PAGE',n,'status=OK','final=',final,'bytes=',len(html),'matches=',len(rows),'years=',years)
            for row in rows[:4]:print(' ',row)
        except Exception as e:
            print('PAGE',n,'status=ERR',repr(e))
