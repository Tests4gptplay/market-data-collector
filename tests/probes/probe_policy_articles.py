#!/usr/bin/env python3
from __future__ import annotations
import json,re
from html.parser import HTMLParser
from urllib.request import Request,urlopen

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36'
ARTICLES={
 'OMO_2026_157':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026081409033914683/index.html',
 'OMO_2026_153':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026081008522023544/index.html',
 'BUYOUT_2026_16':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/2026081316435645255/index.html',
 'BUYOUT_2026_15':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/5492845/2026080416481662012/index.html',
 'MLF_2026_07':'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125437/125446/125873/2026072318022895816/index.html',
}

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.text=[]; self.in_cell=False; self.cell=[]; self.row=[]; self.rows=[]
    def handle_starttag(self,tag,attrs):
        tag=tag.lower()
        if tag in ('td','th'):
            self.in_cell=True; self.cell=[]
        elif tag in ('br','p','div','li','tr'):
            self.text.append('\n')
    def handle_data(self,data):
        self.text.append(data)
        if self.in_cell: self.cell.append(data)
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag in ('td','th') and self.in_cell:
            self.row.append(' '.join(''.join(self.cell).split())); self.in_cell=False; self.cell=[]
        elif tag=='tr':
            if any(x for x in self.row): self.rows.append(self.row)
            self.row=[]

def fetch(url):
    req=Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,*/*','Accept-Language':'zh-CN,zh;q=0.9','Connection':'close'})
    with urlopen(req,timeout=40) as r:
        raw=r.read()
        for enc in ('utf-8','gb18030'):
            try: text=raw.decode(enc); break
            except UnicodeDecodeError: pass
        else: text=raw.decode('utf-8','replace')
        return r.geturl(),text

out={}
for key,url in ARTICLES.items():
    try:
        final,html=fetch(url); p=ArticleParser(); p.feed(html)
        clean='\n'.join(x.strip() for x in ''.join(p.text).splitlines() if x.strip())
        meta=re.search(r'<meta\s+name=["\']createDate["\']\s+content=["\']([^"\']+)',html,re.I)
        title=re.search(r'<title>(.*?)</title>',html,re.I|re.S)
        snippets=[]
        for needle in ['逆回购','操作量','中标利率','期限','买断式','招标量','MLF','中期借贷便利','5000','180','1.40']:
            for m in list(re.finditer(re.escape(needle),clean,re.I))[:5]:
                snippets.append({'needle':needle,'context':clean[max(0,m.start()-350):m.end()+900]})
        out[key]={
            'url':final,
            'title':' '.join((title.group(1) if title else '').split()),
            'createDate':meta.group(1) if meta else None,
            'tables':p.rows,
            'text_excerpt':clean[:7000],
            'snippets':snippets,
        }
    except Exception as e:
        out[key]={'url':url,'error':repr(e)}
print(json.dumps(out,ensure_ascii=False,indent=2))
