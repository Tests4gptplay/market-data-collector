#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
BASES=[
 'https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/',
 'https://nanjing.pbc.gov.cn/goutongjiaoliu/113456/113469/',
]
RRR_CURRENT='https://www.pbc.gov.cn/rmyh/4027845/index.html'

def fetch(url,tries=4):
    last=None
    for i in range(tries):
        try:
            q=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(q,timeout=40) as r:
                raw=r.read(); final=r.geturl()
            for enc in ('utf-8','gb18030'):
                try:return final,raw.decode(enc)
                except UnicodeDecodeError:pass
            return final,raw.decode('utf-8','replace')
        except Exception as e:
            last=e
            if i+1<tries:time.sleep(i+1)
    raise last

class LP(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.href=None;self.buf=[];self.links=[]
    def handle_starttag(self,t,a):
        if t.lower()=='a':self.href=dict(a).get('href');self.buf=[]
    def handle_data(self,d):
        if self.href is not None:self.buf.append(d)
    def handle_endtag(self,t):
        if t.lower()=='a' and self.href is not None:
            self.links.append((self.href,' '.join(''.join(self.buf).split())));self.href=None;self.buf=[]

def relevant(html,final):
    p=LP();p.feed(html);out=[]
    for h,t in p.links:
        if not h or not t:continue
        if any(k in t for k in ('货币政策大事记','存款准备金率','降准','准备金')):
            out.append({'title':t,'url':urljoin(final,h)})
    return out

for base in BASES:
    print('\nBASE',base)
    candidates=['index.html']+[f'11040-{n}.html' for n in (1,2,5,10,19,30,50,100,200,300,396)]
    for rel in candidates:
        u=urljoin(base,rel)
        try:
            f,h=fetch(u);rows=relevant(h,f)
            pageinfo=re.findall(r'(?:当前页|总记录数)[^<]{0,100}',h)
            print('PAGE',rel,'OK','final',f,'len',len(h),'hits',len(rows),'pageinfo',pageinfo[:3])
            for x in rows[:10]:print(' ',json.dumps(x,ensure_ascii=False))
        except Exception as e:print('PAGE',rel,'ERR',repr(e))

try:
    f,h=fetch(RRR_CURRENT)
    clean=' '.join(re.sub(r'<[^>]+>',' ',h).split())
    print('\nRRR_CURRENT',f,'len',len(h))
    for pat in (r'大型银行.{0,30}?([0-9.]+)%',r'中型银行.{0,30}?([0-9.]+)%',r'小型银行.{0,30}?([0-9.]+)%'):
        m=re.search(pat,clean);print('RRR_PATTERN',pat,'=>',m.group(1) if m else None)
except Exception as e:print('RRR_CURRENT_ERR',repr(e))
