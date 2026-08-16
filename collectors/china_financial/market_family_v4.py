#!/usr/bin/env python3
"""V1.9 candidate market-family collector revision 4.

Preserves V1/V2/V3. Adds only a bounded normal retry policy for the official SSE
GC date-query endpoint. This is resilience against transient HTTP failures, not
an access-control bypass. If all normal attempts fail, GC remains UNKNOWN/GAP.
"""
from __future__ import annotations
import json,re,sys,time
from pathlib import Path
from urllib.parse import urlencode

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from collectors.china_financial import market_family_v3 as v3
from collectors.china_financial import market_family_v1 as v1

v1.COLLECTOR_VERSION='V1.9-CANDIDATE-MARKET-FAMILY-V4'


def collect_gc_family_retry(date: str, raw_dir: Path):
    params={
        'jsonCallBack':'jsonpCallback',
        'isPagination':'false',
        'sqlId':'COMMON_SSEBOND_SCSJ_SCTJ_SCGL_ZQZYSHGSCGL_CX_L',
        'TRADE_DATE':date,
    }
    url=v1.base.SSE_QUERY+'?'+urlencode(params)
    last=None
    attempt_meta=[]
    for attempt in range(1,4):
        result=v1.base.http(
            url,
            referer=v1.base.SSE_REFERER,
            accept='application/javascript, application/json, text/javascript, */*; q=0.01',
            headers={
                'Origin':'https://bond.sse.com.cn',
                'X-Requested-With':'XMLHttpRequest',
                'Sec-Fetch-Site':'same-site',
                'Sec-Fetch-Mode':'cors',
                'Sec-Fetch-Dest':'empty',
            },
            timeout=40,
        )
        last=result
        attempt_meta.append({'attempt':attempt,'status':result.status,'ok':result.ok})
        if result.ok:
            break
        # Bounded ordinary retry only. No cookie/session forging, CAPTCHA bypass,
        # proxy rotation, or hidden endpoint substitution is permitted.
        if attempt < 3:
            time.sleep(2*attempt)
    assert last is not None
    digest=v1.base.save_raw(raw_dir,'sse_gc_family_v4.jsonp',last)
    if not last.ok:
        raise RuntimeError(f'SSE GC official query failed after bounded retry: {attempt_meta}')
    text=v1.base.decode(last.raw,last.content_type)
    match=re.match(r'^jsonpCallback\((.*)\)\s*;?\s*$',text,re.S)
    if not match:
        raise RuntimeError('unexpected SSE JSONP wrapper')
    payload=json.loads(match.group(1))
    records=payload.get('result',[]) if isinstance(payload,dict) else []
    out=[]
    for code,name,sid in [('204001','GC001','FUND_GC001'),('204007','GC007','FUND_GC007')]:
        exact=[x for x in records if x.get('BOND_CODE')==code and x.get('BOND_NAME')==name and x.get('TRADE_DATE')==date]
        if len(exact)!=1:
            raise RuntimeError(f'expected exactly one {name} row, got {len(exact)}')
        row=exact[0]
        value=float(row['WEIGHT_RATE'])
        out.append(v1.obs(
            series_id=sid,
            reference_date=date,
            value=value,
            unit='percent',
            provider='SSE',
            semantic='ACTUAL_EXCHANGE_REPO_WEIGHTED_AVERAGE_RATE',
            source_url=last.url,
            evidence_sha256=digest,
            collected_at=v1.base.now_iso(),
            extra={
                'bond_code':code,
                'bond_name':name,
                'field':'WEIGHT_RATE',
                'http_attempts':attempt_meta,
            }
        ))
    return out

v1.collect_gc_family=collect_gc_family_retry

if __name__=='__main__':
    raise SystemExit(v3.main())
