#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re
from urllib.request import Request,urlopen
from pypdf import PdfReader
from io import BytesIO

PDFS={
 '2026-06':'https://www.nafmii.org.cn/sjtj/fx/202607/P020260730398665984862.pdf',
 '2026-05':'https://www.nafmii.org.cn/sjtj/fx/202606/P020260623666241012952.pdf',
 '2026-04':'https://www.nafmii.org.cn/sjtj/fx/202606/P020260604637550872655.pdf',
}
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
out={}
for period,url in PDFS.items():
    try:
        req=Request(url,headers={'User-Agent':UA,'Connection':'close'})
        with urlopen(req,timeout=60) as r: raw=r.read(); final=r.geturl()
        reader=PdfReader(BytesIO(raw))
        pages=[]
        for p in reader.pages:
            pages.append(p.extract_text(extraction_mode='layout') or '')
        text='\n'.join(pages)
        contexts=[]
        for pat in [r'发行(?:规模|金额|量)',r'合计',r'总计',r'亿元',r'债务融资工具']:
            for m in list(re.finditer(pat,text))[:12]: contexts.append(text[max(0,m.start()-220):m.start()+900])
        out[period]={'url':final,'sha256':hashlib.sha256(raw).hexdigest(),'pages':len(reader.pages),'text_len':len(text),'text':text[:18000],'contexts':contexts[:30]}
    except Exception as e: out[period]={'url':url,'error':repr(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
