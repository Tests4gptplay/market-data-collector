#!/usr/bin/env python3
from __future__ import annotations
import re,time
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
UA='Mozilla/5.0'
BASE='https://www.celma.org.cn/'
def get(u):
    last=None
    for i in range(3):
        try:
            with urlopen(Request(u,headers={'User-Agent':UA,'Connection':'close'}),timeout=30) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as e:last=e;time.sleep(i+1)
    raise last
for ch in ('193','194','196'):
    u=BASE+'zqsclb.jhtml?'+urlencode({'ad_code':'87','ad_name':'全国','channelId':ch})
    f,h=get(u)
    print('\nCHANNEL',ch,f)
    for href,label in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
        txt=' '.join(re.sub(r'<[^>]+>',' ',label).split())
        if txt in ('首页','上一页','下一页','尾页','1','2','3') or 'page' in href.lower() or 'zqsclb' in href:
            print('PAGELINK',repr(txt),urljoin(f,href))
    for m in re.finditer(r'(?:pageNo|pageSize|currentPage|totalPage|zqsclb)[^\n]{0,300}',h,re.I):
        print('CTX',m.group(0)[:600])
