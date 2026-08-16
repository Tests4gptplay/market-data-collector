#!/usr/bin/env python3
from __future__ import annotations
import re,time,json
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
BASE='https://search.mof.gov.cn/was5/web/search'
def get(u):
    last=None
    for i in range(4):
        try:
            with urlopen(Request(u,headers={'User-Agent':UA,'Connection':'close'}),timeout=40) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as e:last=e;time.sleep(i+1)
    raise last
for term in ('国债发行工作有关事宜','国债业务公告'):
    q={'andsen':term,'channelid':'295890','orderby':'-PUBORDER,-DOCRELTIME','outlinepage':'10','page':'1','perpage':'10','searchscope':'doctitle','was_custom_expr':f'doctitle=(like({term})/sen)'}
    u=BASE+'?'+urlencode(q)
    try:
        f,h=get(u);plain=' '.join(re.sub(r'<[^>]+>',' ',h).split())
        print('\nTERM',term,'URL',f,'LEN',len(h));print(plain[:8000])
        links=[]
        for href,title in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
            t=' '.join(re.sub(r'<[^>]+>',' ',title).split())
            if '国债' in t:links.append({'title':t,'url':urljoin(f,href)})
        print('LINKS',json.dumps(links[:30],ensure_ascii=False,indent=2))
        for m in re.finditer(r'(?:total|page|记录|结果)[^\n]{0,300}',h,re.I):
            s=m.group(0)
            if any(c.isdigit() for c in s):print('PCTX',s[:500])
    except Exception as e:print('ERR',term,repr(e))
