#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from html import unescape
from urllib.parse import urlencode,urljoin
from urllib.request import Request,urlopen
BASE='https://www.celma.org.cn/'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
def fetch(url):
    last=None
    for i in range(4):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(req,timeout=40) as r:return r.geturl(),r.read().decode('utf-8','replace')
        except Exception as exc:
            last=exc;time.sleep(i+1)
    raise last

def clean(x):
    x=re.sub(r'(?is)<script.*?</script>|<style.*?</style>',' ',x)
    x=re.sub(r'(?i)<br\s*/?>|</(?:p|li|tr|div|h\d)>','\n',x)
    x=re.sub(r'(?s)<[^>]+>',' ',x);x=unescape(x)
    return '\n'.join(' '.join(y.split()) for y in x.splitlines() if ' '.join(y.split()))

for ch in ('192','193','194','196'):
    params={'ad_code':'87','ad_name':'全国','channelId':ch}
    url=BASE+'zqsclb.jhtml?'+urlencode(params)
    try:
        final,html=fetch(url);text=clean(html)
        print('\n### CHANNEL',ch,'URL',final,'LEN',len(html))
        print(text[:5000])
        links=[]
        for href,label in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',html,re.I|re.S):
            label=' '.join(re.sub(r'<[^>]+>',' ',label).split())
            full=urljoin(final,href)
            if label and ('jhtml' in href or '.pdf' in href.lower()):links.append({'label':label,'url':full})
        print('LINKS',json.dumps(links[:30],ensure_ascii=False,indent=2))
        for pat in (r'缴款.{0,180}',r'招标.{0,180}',r'发行.{0,180}',r'起息.{0,180}',r'2026[-年]\d{1,2}[-月]\d{1,2}日?'):
            hits=re.findall(pat,text,re.S)
            if hits:print('PAT',pat,json.dumps(hits[:20],ensure_ascii=False,indent=2))
    except Exception as exc:
        print('ERR',ch,repr(exc))
