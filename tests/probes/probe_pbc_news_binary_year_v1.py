#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from urllib.parse import urljoin
from urllib.request import Request,urlopen
BASE='https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/'
FIRST=urljoin(BASE,'index.html')
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
def fetch(u):
    last=None
    for i in range(4):
        try:
            with urlopen(Request(u,headers={'User-Agent':UA,'Connection':'close'}),timeout=35) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as exc:last=exc;time.sleep(i+1)
    raise last

def page(n):return FIRST if n<=1 else urljoin(BASE,f'11040-{n}.html')
def summary(n):
    f,h=fetch(page(n))
    m=re.search(r'name=["\']article_paging_list_hidden["\'][^>]*totalpage=["\'](\d+)',h,re.I)
    total=int(m.group(1)) if m else None
    plain=' '.join(re.sub(r'<[^>]+>',' ',h).split())
    dates=[]
    for y,mo,d in re.findall(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?',plain):
        try:dates.append((int(y),int(mo),int(d)))
        except:pass
    years=[x[0] for x in dates]
    return {'page':n,'url':f,'total':total,'min_year':min(years) if years else None,'max_year':max(years) if years else None,'dates':dates[:30],'html':h}
first=summary(1);print('FIRST',json.dumps({k:v for k,v in first.items() if k!='html'},ensure_ascii=False))
total=first['total'] or 450
target=2025
lo,hi=1,total;visited=[]
while lo<=hi:
    mid=(lo+hi)//2;s=summary(mid);visited.append({k:v for k,v in s.items() if k!='html'});print('MID',json.dumps(visited[-1],ensure_ascii=False))
    mn,mx=s['min_year'],s['max_year']
    if mn is None:break
    if mn>target:lo=mid+1
    elif mx<target:hi=mid-1
    else:
        for p in range(max(1,mid-2),min(total,mid+2)+1):
            q=summary(p);h=q.pop('html');print('AROUND',json.dumps(q,ensure_ascii=False))
            for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',h,re.I|re.S):
                title=' '.join(re.sub(r'<[^>]+>',' ',m.group(2)).split())
                if any(k in title for k in ('存款准备金率','降准','货币政策大事记')):
                    pos=m.start();ctx=' '.join(re.sub(r'<[^>]+>',' ',h[max(0,pos-300):m.end()+500]).split())
                    print('RRR_ROW',json.dumps({'page':p,'title':title,'url':urljoin(page(p),m.group(1)),'context':ctx},ensure_ascii=False))
        break
print('VISITED',json.dumps(visited,ensure_ascii=False))
