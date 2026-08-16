#!/usr/bin/env python3
from __future__ import annotations
import io,json,re,time
from html import unescape
from urllib.parse import urljoin
from urllib.request import Request,urlopen
from pypdf import PdfReader
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
PAGES=[
 'https://www.celma.org.cn/fxqgg/68068.jhtml',
 'https://www.celma.org.cn/fxqgg/64601.jhtml',
]
def fetch(url,accept='*/*'):
    last=None
    for i in range(4):
        try:
            req=Request(url,headers={'User-Agent':UA,'Accept':accept,'Referer':'https://www.celma.org.cn/','Connection':'close'})
            with urlopen(req,timeout=45) as r:return r.geturl(),r.read()
        except Exception as exc:
            last=exc;time.sleep(2*(i+1))
    raise last

def norm(s):return '\n'.join(' '.join(x.split()) for x in s.replace('\u3000',' ').replace('\xa0',' ').splitlines() if ' '.join(x.split()))
for page in PAGES:
    print('\n### PAGE',page)
    final,raw=fetch(page);html=raw.decode('utf-8','replace')
    attachments=[]
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',html,re.I|re.S):
        href=m.group(1); label=' '.join(re.sub(r'<[^>]+>',' ',unescape(m.group(2))).split())
        if '.pdf' in href.lower() or 'attachFiles' in href:
            attachments.append((label,urljoin(final,href)))
    print('ATTACHMENTS',json.dumps(attachments,ensure_ascii=False,indent=2))
    for label,url in attachments:
        if not any(k in label for k in ('通知','发行公开','发行')):continue
        try:
            _,pdf=fetch(url,'application/pdf,*/*')
            if not pdf.startswith(b'%PDF'):continue
            txt=norm('\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(pdf)).pages))
            print('\nATTACHMENT',label,url,'chars',len(txt))
            for pat in (r'招标[^。\n]{0,220}',r'缴款[^。\n]{0,220}',r'发行[^。\n]{0,220}',r'起息[^。\n]{0,220}',r'上市[^。\n]{0,220}',r'202\d年\d{1,2}月\d{1,2}日[^。\n]{0,160}'):
                hits=re.findall(pat,txt,re.S)
                if hits:print('PAT',pat,json.dumps(hits[:30],ensure_ascii=False,indent=2))
        except Exception as exc:print('ATTACH_ERR',label,repr(exc))
