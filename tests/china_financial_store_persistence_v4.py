#!/usr/bin/env python3
"""Validate latest-first China Financial Store pipeline using V3 manifest."""
from __future__ import annotations
import argparse,json
from pathlib import Path


def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--expect-as-of');a=ap.parse_args();root=Path(a.store_root)
    latest=load(root/'manifests'/'latest.json')
    assert latest.get('schema_version')=='CF_STORE_MANIFEST_V3'
    assert latest.get('pipeline_version')=='CF_STORE_PERSISTENCE_V3'
    if a.expect_as_of:assert latest.get('as_of')==a.expect_as_of
    assert latest.get('fast_market_coverage')=='27/27'
    c=latest.get('consumer_contract') or {}
    assert c.get('standard_runtime_must_not_invoke_collectors') is True
    assert c.get('consumer_reads_only_manifest_selected_active_paths') is True
    p=latest.get('producer_contract') or {}
    assert p.get('low_frequency_default')=='CHECK_NEW_RELEASE_AND_CARRY_STORE'
    assert p.get('historical_backfill_default') is False
    required={'fast_market','pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance','rrr_event_incremental','policy_event_incremental','local_gov_cash_clock','central_gov_cash_clock'}
    status=latest.get('family_status') or {};assert required<=set(status)
    for name in required:assert status[name].get('status')=='PASS',(name,status[name])
    paths=latest.get('active_data_paths') or {};assert required<=set(paths)
    for name in required:
        for kind,path in (paths[name] or {}).items():assert Path(path).exists(),(name,kind,path)
    as_of=str(latest['as_of']);year=as_of[:4];daily=load(root/'runs'/year/f'{as_of}-daily-persist-v3.json');checks=daily.get('collection_checks') or {}
    allowed={'NO_NEW_RELEASE_CARRY_STORE','NEW_LATEST_DATA','NEW_RELEASE_INCREMENT','PARTIAL_OFFICIAL_RELEASE_DEFER_STORE','DAILY_INCREMENTAL'}
    for name in ('pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance'):
        run=checks.get(name) or {};assert run.get('status')=='PASS',(name,run);assert run.get('mode') in allowed,(name,run.get('mode'))
    print(json.dumps({'status':'PASS','as_of':as_of,'latest_valid_market_session':latest.get('latest_valid_market_session'),'consumer_entrypoint':c.get('entrypoint'),'low_frequency_modes':{n:(checks.get(n) or {}).get('mode') for n in ('pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance')}},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
