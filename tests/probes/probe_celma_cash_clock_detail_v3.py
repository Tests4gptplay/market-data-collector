#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from html import unescape
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
URLS={
 'ISSUANCE':'https://www.celma.org.cn/fxjg/70041.jhtml',
 'DEBT_SERVICE':'https://www.celma.org.cn/fxdf/70048.jhtml',
}

def fetch(url,tries=4):
    last=None
    for i in range(tries):
        try:
            q=Request(url,headers={'User-Agent':UA,'Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
            with urlopen(q,timeout=45) as r:return r.geturl(),r.read(),dict(r.headers)
        except Exception as e:
            last=e
            if i+1<tries:time.sleep(2*(i+1))
    raise last

def clean(html):
    x=re.sub(r'(?is)<script.*?</script>|<style.*?</style>',' ',html)
    x=re.sub(r'(?i)<br\s*/?>|</(?:p|tr|li|div|h\d)>','\n',x)
    x=re.sub(r'(?s)<[^>]+>',' ',x)
    x=unescape(x).replace('\xa0',' ')
    return '\n'.join(' '.join(line.split()) for line in x.splitlines() if ' '.join(line.split()))

for family,u in URLS.items():
    try:
        f,b,h=fetch(u); html=b.decode('utf-8','replace'); txt=clean(html)
        print('\n###',family,'URL',f,'bytes',len(b),'sha256',hashlib.sha256(b).hexdigest())
        print('TEXT_START\n',txt[:12000])
        # expose attachment/download links (often xls/xlsx/pdf/doc)
        links=[]
        for href in re.findall(r'(?:href|src)=["\']([^"\']+)["\']',html,re.I):
            if any(k in href.lower() for k in ('.xls','.xlsx','.pdf','.doc','.docx','upload','download','attachment')):
                links.append(href)
        print('ATTACHMENTS',json.dumps(links,ensure_ascii=False,indent=2))
        for pat in (
            r'债券代码.{0,120}',r'债券名称.{0,120}',r'发行规模.{0,120}',r'发行额.{0,120}',
            r'票面利率.{0,120}',r'期限.{0,120}',r'起息.{0,120}',r'缴款.{0,120}',r'到期.{0,120}',
            r'还本.{0,180}',r'付息.{0,180}',r'本金.{0,180}',r'利息.{0,180}',r'兑付.{0,180}'
        ):
            ms=re.findall(pat,txt,re.S)
            if ms:print('PATTERN',pat,json.dumps(ms[:12],ensure_ascii=False,indent=2))
    except Exception as e:print('ERR',family,repr(e))
