#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from urllib.parse import urljoin
from urllib.request import Request,urlopen
BASE='https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/'
UA='Mozilla/5.0'
def fetch(url):
    last=None
    for i in range(3):
        try:
            req=Request(url,headers={'User-Agent':UA,'Connection':'close'})
            with urlopen(req,timeout=30) as r:
                return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as exc:
            last=exc; time.sleep(i+1)
    raise last

def page_url(n):
    return urljoin(BASE,'index.html' if n==1 else '11040-%d.html'%n)

for n in range(1,70):
    try:
        final,html=fetch(page_url(n))
    except Exception as exc:
        print('PAGE_ERROR',n,repr(exc)); break
    anchors=re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',html,re.I|re.S)
    for href,body in anchors:
        title=' '.join(re.sub(r'<[^>]+>',' ',body).split())
        if not any(k in title for k in ('存款准备金率','降准','货币政策大事记')):
            continue
        pos=html.find(href)
        nearby=html[max(0,pos-300):pos+1200] if pos>=0 else ''
        plain=' '.join(re.sub(r'<[^>]+>',' ',nearby).split())
        dates=re.findall(r'20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?',plain)
        print(json.dumps({'page':n,'title':title,'url':urljoin(final,href),'dates':dates,'context':plain[:600]},ensure_ascii=False))
