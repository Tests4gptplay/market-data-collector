#!/usr/bin/env python3
from __future__ import annotations
import json
from collectors.china_financial.policy_event_family_v1 import fetch, clean_article
URL='https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026081409033914683/index.html'
final,raw,html=fetch(URL)
text,rows,available=clean_article(html)
print(json.dumps({'url':final,'available_at':available,'rows':[r for r in rows if any(('7天' in c or '逆回购' in c or '利率' in c or '中标' in c) for c in r)],'contexts':[text[max(0,i-120):i+500] for i in [text.find('7天'),text.find('隔夜')] if i>=0]},ensure_ascii=False,indent=2))
