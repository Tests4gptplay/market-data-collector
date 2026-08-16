#!/usr/bin/env python3
from __future__ import annotations
import re,time
from urllib.parse import urljoin
from urllib.request import Request,urlopen
BASE='https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
def get(u):
    last=None
    for i in range(4):
        try:
            with urlopen(Request(u,headers={'User-Agent':UA,'Connection':'close'}),timeout=40) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as e:last=e;time.sleep(i+1)
    raise last
f,h=get(BASE)
print('BASE',f,'LEN',len(h))
for m in re.finditer(r'(?:下一页|尾页|总记录数|当前页)',h):print('CTX',h[max(0,m.start()-500):m.start()+800])
# all hrefs likely pagination
for href,text in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
    t=' '.join(re.sub(r'<[^>]+>',' ',text).split())
    if t in ('首页','上一页','下一页','尾页') or re.search(r'125475[-_]?\d',href):print('LINK',t,urljoin(f,href))
# test href-like filename forms if visible pattern extraction fails
for rel in ('125475-2.html','125475_2.html','index_2.html','index-2.html'):
    u=urljoin(f,rel)
    try:
        ff,hh=get(u);print('TEST',rel,'OK',ff,'LEN',len(hh),'HAS_2026_134',('[2026]第134号' in hh),'HAS_2026_114',('[2026]第114号' in hh))
    except Exception as e:print('TEST',rel,'ERR',repr(e))
