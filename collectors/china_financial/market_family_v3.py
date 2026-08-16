#!/usr/bin/env python3
"""V1.9 candidate market-family collector revision 3.

Extends revision 2 with the exact ChinaMoney historical broad pledged-repo
(R-family) route discovered from the official daily-bulletin page.

Official contract:
POST /ags/ms/cm-u-dlrp/PrDlyBltn
  lang=cn
  indexType=markVOList
  searchDate=<target YYYY-MM-DD>
  publishedTime=2200
Select exact instrmntCd R001/R007/R014 and field wghtdAvgRepoRate.
FR007 remains QC only and is never a substitute for R007.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from urllib.parse import urlencode

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from collectors.china_financial import market_family_v2 as v2  # patches v1 exact curves
from collectors.china_financial import market_family_v1 as v1

v1.COLLECTOR_VERSION='V1.9-CANDIDATE-MARKET-FAMILY-V3'
R_DAILY=v1.base.CHINAMONEY_ROOT+'/ags/ms/cm-u-dlrp/PrDlyBltn'
R_REFERER=v1.base.CHINAMONEY_ROOT+'/chinese/mtdexdaily/?tab=2'


def collect_r_family(date: str, raw_dir: Path):
    form={
        'lang':'cn',
        'indexType':'markVOList',
        'searchDate':date,
        'publishedTime':'2200'
    }
    body=urlencode(form).encode('ascii')
    result=v1.base.http(
        R_DAILY,
        method='POST',
        data=body,
        referer=R_REFERER,
        accept='application/json, text/javascript, */*; q=0.01',
        headers={
            'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin':v1.base.CHINAMONEY_ROOT,
            'X-Requested-With':'XMLHttpRequest'
        }
    )
    digest=v1.base.save_raw(raw_dir,'chinamoney_pr_daily_R_family.json',result)
    if not result.ok:
        raise RuntimeError(result.error or 'ChinaMoney PrDlyBltn request failed')
    payload=json.loads(v1.base.decode(result.raw,result.content_type).lstrip('\ufeff'))
    if not isinstance(payload,dict):
        raise RuntimeError('PrDlyBltn payload is not an object')
    data=payload.get('data') or {}
    returned_date=str(data.get('lastDate') or '')
    if returned_date != date:
        raise RuntimeError(f'PrDlyBltn returned date {returned_date!r}, expected {date!r}')
    records=payload.get('records') or []
    out=[]
    mapping={'R001':'FUND_R001','R007':'FUND_R007','R014':'FUND_R014'}
    for instrument,sid in mapping.items():
        exact=[x for x in records if str(x.get('instrmntCd','')).strip()==instrument]
        if len(exact)!=1:
            raise RuntimeError(f'{instrument}: expected exactly one official row, got {len(exact)}')
        row=exact[0]
        raw_value=row.get('wghtdAvgRepoRate')
        if raw_value in (None,'','---'):
            raise RuntimeError(f'{instrument}: wghtdAvgRepoRate missing')
        value=float(raw_value)
        out.append(v1.obs(
            series_id=sid,
            reference_date=date,
            value=value,
            unit='percent',
            provider='CFETS / ChinaMoney',
            semantic='ACTUAL_BROAD_PLEDGED_REPO_WEIGHTED_RATE',
            source_url=result.url,
            evidence_sha256=digest,
            collected_at=v1.base.now_iso(),
            extra={
                'instrument':instrument,
                'field':'wghtdAvgRepoRate',
                'returned_date':returned_date,
                'published_time':'2200',
                'open_repo_rate':row.get('openRepoRate'),
                'closing_repo_rate':row.get('clsngRepoRate'),
                'trade_volume_cny_100m':row.get('trdVol'),
                'deal_count':row.get('dlNo'),
                'avg_repo_tenor_days':row.get('avgPrd')
            }
        ))
    return out


def cli_args():
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument('--date',required=True)
    p.add_argument('--out',default='out/china_financial_market')
    return p.parse_known_args()[0]


def main():
    args=cli_args()
    # Run revision-2 pipeline first. Its non-zero return due only to missing R-family
    # is deliberately post-processed below; any other gaps remain visible.
    v1.main()
    root=Path(args.out)/args.date
    raw=root/'raw'
    observations=json.loads((root/'observations.json').read_text(encoding='utf-8'))
    derived=json.loads((root/'derived.json').read_text(encoding='utf-8'))
    gaps=json.loads((root/'gaps.json').read_text(encoding='utf-8'))
    run=json.loads((root/'run.json').read_text(encoding='utf-8'))

    r_ids={'FUND_R001','FUND_R007','FUND_R014'}
    try:
        r_obs=collect_r_family(args.date,raw)
        observations.extend(r_obs)
        gaps=[g for g in gaps if g.get('series_id') not in r_ids]
        run['attempts']=[a for a in run.get('attempts',[]) if a.get('family')!='R_ACTUAL_FAMILY']
        run['attempts'].append({'family':'R_ACTUAL_FAMILY','status':'SUCCESS','source':'ChinaMoney PrDlyBltn markVOList'})
    except Exception as exc:
        gaps=[g for g in gaps if g.get('series_id') not in r_ids]
        for sid in sorted(r_ids):
            gaps.append(v1.mk_gap(sid,args.date,'SOURCE_OR_PARSER_GAP',str(exc)))
        run['attempts']=[a for a in run.get('attempts',[]) if a.get('family')!='R_ACTUAL_FAMILY']
        run['attempts'].append({'family':'R_ACTUAL_FAMILY','status':'GAP','message':str(exc)})

    # Exact one canonical provider row per SeriesID is required for the production
    # market-family dataset. Alternate-provider evidence is not counted twice.
    by_series={}
    canonical=[]
    for row in observations:
        sid=row['series_id']
        if sid not in by_series:
            by_series[sid]=row
            canonical.append(row)
            continue
        old=by_series[sid]
        # Prefer CFETS/ChinaMoney primary over ChinaBond fallback when both exist.
        if old.get('provider')=='ChinaBond' and row.get('provider')=='CFETS / ChinaMoney':
            canonical.remove(old)
            by_series[sid]=row
            canonical.append(row)
        elif abs(float(old['value'])-float(row['value']))>1e-12 and old.get('provider')==row.get('provider'):
            gaps.append(v1.mk_gap(sid,args.date,'DUPLICATE_CONFLICT',f'conflicting same-provider duplicates: {old["value"]} vs {row["value"]}'))
    observations=canonical

    present={x['series_id'] for x in observations}|{x['series_id'] for x in derived}
    expected=set(v1.MARKET_EXPECTED)
    missing=sorted(expected-present)
    run.update({
        'collector_version':v1.COLLECTOR_VERSION,
        'present_series':sorted(expected & present),
        'missing_series':missing,
        'coverage_count':len(expected & present),
        'expected_count':len(expected),
        'status':'PASS' if not missing else 'INCOMPLETE',
        'completed_at':v1.base.now_iso()
    })
    for name,payload in [('observations.json',observations),('derived.json',derived),('gaps.json',gaps),('run.json',run)]:
        (root/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if not missing else 2

if __name__=='__main__':
    raise SystemExit(main())
