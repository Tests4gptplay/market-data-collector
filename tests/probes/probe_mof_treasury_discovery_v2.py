#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
BASE='https://search.mof.gov.cn/was5/web/search'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
def fetch(url,tries=5):
    last=None
    for i in range(tries):
        try:
            with urlopen(Request(url,headers={'User-Agent':UA,'Connection':'close'}),timeout=45) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as exc:last=exc;time.sleep(2*(i+1))
    raise last

def query(term, start=None,end=None):
    q={'andsen':term,'channelid':'295890','orderby':'-PUBORDER,-DOCRELTIME','outlinepage':'10','page':'1','perpage':'20','searchscope':'doctitle'}
    if start and end:q.update({'timescope':'customdate','sStartTime':start,'sEndTime':end})
    u=BASE+'?'+urlencode(q);f,h=fetch(u)
    plain=' '.join(re.sub(r'<[^>]+>',' ',h).split())
    count=re.search(r'找到相关结果约\s*(\d+)\s*条',plain)
    rows=[]
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
        href=m.group(1);title=' '.join(re.sub(r'<[^>]+>',' ',m.group(2)).split())
        if '国债' not in title:continue
        pos=m.end();ctx=' '.join(re.sub(r'<[^>]+>',' ',h[pos:pos+1800]).split())
        dm=re.search(r'(20\d{2})[.-](\d{1,2})[.-](\d{1,2})',ctx)
        rows.append({'title':title,'url':urljoin(f,href),'date':('%s-%02d-%02d'%(dm.group(1),int(dm.group(2)),int(dm.group(3)))) if dm else None})
    return {'term':term,'start':start,'end':end,'url':f,'count':int(count.group(1)) if count else None,'rows':rows[:30]}
for args in [
    ('国债业务公告2026年第',None,None),
    ('国债业务公告2026',None,None),
    ('国债业务公告', '2026-06-12','2026-06-12'),
    ('国债发行工作有关事宜','2026-08-14','2026-08-14'),
]:
    try:print(json.dumps(query(*args),ensure_ascii=False,indent=2))
    except Exception as exc:print('ERROR',args,repr(exc))
# Direct debt-management business-announcement directory candidates.
for u in ('https://zwgls.mof.gov.cn/ywgg/index.htm','https://zwgls.mof.gov.cn/ywgg/index_1.htm'):
    try:
        f,h=fetch(u);plain=' '.join(re.sub(r'<[^>]+>',' ',h).split())
        print('DIRECT',u,'=>',f,'len',len(h),'head',plain[:4000])
        links=[]
        for href,title in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
            t=' '.join(re.sub(r'<[^>]+>',' ',title).split())
            if '国债业务公告' in t:links.append({'title':t,'url':urljoin(f,href)})
        print('DIRECT_LINKS',json.dumps(links[:30],ensure_ascii=False,indent=2))
    except Exception as exc:print('DIRECT_ERR',u,repr(exc))
