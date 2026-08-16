#!/usr/bin/env python3
from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
PAGES=['https://www.chinamoney.com.cn/chinese/mtdexdaily/?tab=2','https://www.chinamoney.com.cn/chinese/mkdatapm/?tab=2']

class Scripts(HTMLParser):
    def __init__(self): super().__init__(); self.src=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='script':
            d=dict(attrs); s=d.get('src')
            if s: self.src.append(s)

def get(url):
    req=Request(url,headers={'User-Agent':UA,'Accept':'text/html,*/*','Accept-Language':'zh-CN,zh;q=0.9'})
    with urlopen(req,timeout=35) as r: return r.geturl(),r.read().decode('utf-8','replace')

def snippets(text):
    pats=['R001','R007','R014','weightedRate','WEIGHT_RATE','currency/','DURL','mtdex','prr-','.json','.csv']
    out=[]
    for p in pats:
        for m in list(re.finditer(re.escape(p),text,re.I))[:8]:
            a=max(0,m.start()-260); b=min(len(text),m.end()+500)
            out.append({'needle':p,'context':text[a:b]})
    return out

result=[]
for page in PAGES:
    try:
        final,html=get(page)
        ps=Scripts(); ps.feed(html)
        item={'page':page,'final_url':final,'html_len':len(html),'page_hits':snippets(html),'scripts':[]}
        for src in ps.src[:60]:
            u=urljoin(final,src)
            try:
                _,js=get(u); hits=snippets(js)
                if hits: item['scripts'].append({'url':u,'len':len(js),'hits':hits})
            except Exception as e:
                item['scripts'].append({'url':u,'error':repr(e)})
        result.append(item)
    except Exception as e:
        result.append({'page':page,'error':repr(e)})
print(json.dumps(result,ensure_ascii=False,indent=2))
