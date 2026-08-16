#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import re
import time
from urllib.request import Request, urlopen

from pypdf import PdfReader

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
PDFS = {
    'ISSUANCE_SHAANXI_20260812': 'https://www.governbond.org.cn/uploadFiles/61/attachFiles/202608/f95711ea-0c63-4931-9da0-ea898535ef53.pdf',
    'DEBT_SERVICE_SHANDONG_202609': 'https://www.governbond.org.cn/uploadFiles/37/attachFiles/202608/22ef89b0-d0b1-4822-a7b1-024b75132a0b.pdf',
}


def fetch(url: str, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = Request(url, headers={
                'User-Agent': UA,
                'Accept': 'application/pdf,*/*',
                'Referer': 'https://www.celma.org.cn/',
                'Connection': 'close',
            })
            with urlopen(req, timeout=45) as r:
                raw = r.read()
            if not raw.startswith(b'%PDF'):
                raise ValueError(f'not a PDF: {raw[:40]!r}')
            return raw
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    raise last


def normalize(text: str) -> str:
    text = text.replace('\u3000', ' ').replace('\xa0', ' ')
    lines = [' '.join(x.split()) for x in text.splitlines()]
    return '\n'.join(x for x in lines if x)


for name, url in PDFS.items():
    print('\n###', name)
    try:
        raw = fetch(url)
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for idx, page in enumerate(reader.pages):
            txt = normalize(page.extract_text() or '')
            pages.append(txt)
            print(f'--- PAGE {idx+1} chars={len(txt)} ---')
            print(txt[:16000])
        all_text = '\n'.join(pages)
        print('SUMMARY', json.dumps({
            'pages': len(reader.pages),
            'bytes': len(raw),
            'chars': len(all_text),
            'has_text': bool(all_text.strip()),
        }, ensure_ascii=False))
        for pat in (
            r'债券代码.{0,180}', r'债券名称.{0,180}', r'发行规模.{0,180}', r'发行额.{0,180}',
            r'实际发行.{0,180}', r'票面利率.{0,180}', r'期限.{0,180}', r'起息.{0,180}',
            r'缴款.{0,180}', r'到期.{0,220}', r'还本.{0,220}', r'本金.{0,220}',
            r'付息.{0,220}', r'利息.{0,220}', r'兑付.{0,220}', r'支付.{0,220}'
        ):
            hits = re.findall(pat, all_text, re.S)
            if hits:
                print('PATTERN', pat, json.dumps(hits[:20], ensure_ascii=False, indent=2))
    except Exception as exc:
        print('ERROR', repr(exc))
