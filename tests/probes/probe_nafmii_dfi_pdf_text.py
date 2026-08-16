#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,subprocess,tempfile
from pathlib import Path
from urllib.request import Request,urlopen

PDF='https://www.nafmii.org.cn/sjtj/fx/202607/P020260730398665984862.pdf'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
req=Request(PDF,headers={'User-Agent':UA,'Connection':'close'})
with urlopen(req,timeout=60) as r: raw=r.read(); final=r.geturl()
result={'url':final,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
with tempfile.TemporaryDirectory() as td:
    p=Path(td)/'x.pdf'; t=Path(td)/'x.txt'; p.write_bytes(raw)
    result['pdftotext_path']=subprocess.run(['bash','-lc','command -v pdftotext || true'],capture_output=True,text=True).stdout.strip()
    if result['pdftotext_path']:
        cp=subprocess.run(['pdftotext','-layout',str(p),str(t)],capture_output=True,text=True)
        result['pdftotext_rc']=cp.returncode; result['stderr']=cp.stderr[-1000:]
        if t.exists(): result['text']=t.read_text(encoding='utf-8',errors='replace')[:12000]
print(json.dumps(result,ensure_ascii=False,indent=2))
