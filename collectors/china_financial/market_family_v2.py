#!/usr/bin/env python3
"""V1.9 candidate market-family collector revision 2.

Preserves market_family_v1.py and corrects CFETS/ChinaMoney curve retrieval to the
V1.8-proven exact-term POST pattern. Each required tenor is explicitly queried
and validated by target date + exact yearTermStr + maturityYieldStr.

R001/R007/R014 remain fail-closed until the exact daily-bulletin history route is
promoted by a separate source probe; FR007 is never substituted for R007.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from collectors.china_financial import market_family_v1 as v1

v1.COLLECTOR_VERSION='V1.9-CANDIDATE-MARKET-FAMILY-V2'


def collect_curve_exact(date: str, raw_dir: Path, key: str):
    bond_type, tenors, semantic = v1.CURVES[key]
    out=[]
    for term, sid in tenors.items():
        term_text=str(int(term)) if float(term).is_integer() else str(term)
        params={
            'lang':'CN','reference':'1','bondType':bond_type,
            'startDate':date,'endDate':date,'termId':term_text,
            'pageNum':'1','pageSize':'50'
        }
        safe_term=term_text.replace('.','p')
        payload,digest,url=v1.post_json(
            v1.CURVE_HIS,date,raw_dir,
            f'chinamoney_curve_{bond_type}_{safe_term}Y.json',
            params,v1.CURVE_REFERER
        )
        recs=payload.get('records',[]) if isinstance(payload,dict) else []
        exact=[]
        for row in recs:
            try:
                if str(row.get('newDateValueCN','')) != date:
                    continue
                if abs(float(row.get('yearTermStr'))-float(term)) > 1e-9:
                    continue
                value=float(row.get('maturityYieldStr'))
                exact.append((value,row))
            except Exception:
                continue
        if len(exact) != 1:
            raise RuntimeError(
                f'{key} {term_text}Y expected exactly one target-date exact-tenor row; '
                f'got {len(exact)}'
            )
        value,row=exact[0]
        role='QC_DIAGNOSTIC' if sid=='SOV_CDB_10Y' else 'CORE'
        out.append(v1.obs(
            series_id=sid,
            reference_date=date,
            value=value,
            unit='percent',
            provider='CFETS / ChinaMoney',
            semantic=semantic,
            source_url=url,
            evidence_sha256=digest,
            collected_at=v1.base.now_iso(),
            role=role,
            extra={
                'bond_type':bond_type,
                'tenor_years':term,
                'curve_key':key,
                'selection_contract':'exact target date + exact yearTermStr + maturityYieldStr',
                'current_yield_if_published':row.get('currentYieldStr')
            }
        ))
    return out

v1.collect_curve=collect_curve_exact

if __name__=='__main__':
    raise SystemExit(v1.main())
