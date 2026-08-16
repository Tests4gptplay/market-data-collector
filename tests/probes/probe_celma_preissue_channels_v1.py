#!/usr/bin/env python3
from __future__ import annotations
import re,time,json
from urllib.parse import urljoin
from urllib.request import Request,urlopen
BASE='https://www.celma.org.cn/zqsc/index.jhtml'
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
for key in ('发行安排','发行前公告','发行结果','还本付息'):
    print('\nKEY',key)
    for m in re.finditer(key,h):
        print(h[max(0,m.start()-700):m.start()+900])
for m in re.finditer(r'channelId[^\n]{0,120}',h,re.I):
    print('CHANNEL_CTX',m.group(0))
# Test plausible channel ids around known 194 issuance result / 196 debt service.
for ch in range(188,198):
    u='https://www.celma.org.cn/zqsclb.jhtml?ad_code=87&channelId='+str(ch)
    try:
        ff,hh=get(u)
        title=re.search(r'<title>(.*?)</title>',hh,re.I|re.S)
        text=' '.join(re.sub(r'<[^>]+>',' ',hh).split())
        print('CHANNEL',ch,'len',len(hh),'title',title.group(1).strip() if title else None,'head',text[:600])
    except Exception as e:print('CHANNEL',ch,'ERR',repr(e))
