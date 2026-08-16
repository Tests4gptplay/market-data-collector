#!/usr/bin/env python3
"""Validate the model-facing China Financial Store V3 contract."""
from __future__ import annotations
import argparse,json
from pathlib import Path


def load(p:Path):return json.loads(p.read_text(encoding='utf-8'))

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--store-root',default='data/china_financial');ap.add_argument('--expect-as-of');a=ap.parse_args();root=Path(a.store_root)
    latest_path=root/'manifests'/'latest.json';assert latest_path.exists(),latest_path;latest=load(latest_path)
    assert latest.get('schema_version')=='CF_STORE_MANIFEST_V3',latest.get('schema_version')
    assert latest.get('pipeline_version')=='CF_STORE_PERSISTENCE_V3',latest.get('pipeline_version')
    if a.expect_as_of:assert latest.get('as_of')==a.expect_as_of,(latest.get('as_of'),a.expect_as_of)
    assert latest.get('fast_market_coverage')=='27/27',latest.get('fast_market_coverage')
    assert latest.get('latest_valid_market_session')
    c=latest.get('consumer_contract') or {}
    for k in ('standard_runtime_must_not_invoke_collectors','standard_runtime_must_not_scrape_sources','consumer_should_not_glob_all_historical_store_files','consumer_reads_only_manifest_selected_active_paths','collector_schedule_is_independent_of_model_invocation'):
        assert c.get(k) is True,(k,c)
    assert c.get('entrypoint')=='data/china_financial/manifests/latest.json',c
    p=latest.get('producer_contract') or {}
    assert p.get('workflow')=='.github/workflows/china-financial-daily-persist-v3.yml',p
    assert p.get('low_frequency_default')=='CHECK_NEW_RELEASE_AND_CARRY_STORE',p
    assert p.get('historical_backfill_default') is False,p
    assert p.get('same_key_conflicting_revision_fails_closed') is True,p
    assert p.get('schedule_only_activates_from_default_branch') is True,p

    required={'fast_market','pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance','rrr_event_incremental','policy_event_incremental','local_gov_cash_clock','central_gov_cash_clock'}
    status=latest.get('family_status') or {};assert required<=set(status),required-set(status)
    for name in required:assert status[name].get('status')=='PASS',(name,status[name])
    paths=latest.get('active_data_paths') or {};assert required<=set(paths),required-set(paths)
    for name in required:
        for kind,path in (paths[name] or {}).items():
            assert Path(path).exists(),(name,kind,path)
    for kind,path in (paths.get('registry') or {}).items():assert Path(path).exists(),('registry',kind,path)

    as_of=str(latest['as_of']);year=as_of[:4]
    dm=root/'manifests'/year/f'{as_of}-daily-persist-v3.json';dr=root/'runs'/year/f'{as_of}-daily-persist-v3.json'
    assert dm.exists(),dm;assert dr.exists(),dr
    daily=load(dr);checks=daily.get('collection_checks') or {}
    for name in ('pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance'):
        assert (checks.get(name) or {}).get('status')=='PASS',(name,checks.get(name))
        mode=(checks.get(name) or {}).get('mode')
        assert mode in {'NO_NEW_RELEASE_CARRY_STORE','NEW_COMPLETE_RELEASE_INCREMENT','NEW_RELEASE_INCREMENT','PARTIAL_OFFICIAL_RELEASE_DEFER_STORE'},(name,mode)
    assert ((latest.get('fiscal_context') or {}).get('local_government_bond') or {}).get('window_proof',{}).get('unknown_is_never_zero') is True
    assert ((latest.get('fiscal_context') or {}).get('central_government_bond') or {}).get('ready_adapter_semantics',{}).get('source_outage_becomes_unknown_not_zero') is True
    print(json.dumps({'status':'PASS','schema_version':latest['schema_version'],'pipeline_version':latest['pipeline_version'],'as_of':as_of,'latest_valid_market_session':latest['latest_valid_market_session'],'consumer_entrypoint':c['entrypoint'],'low_frequency_modes':{n:checks[n].get('mode') for n in ('pbc_monthly_credit','mof_fiscal_ytd','pbc_monthly_policy_tools','nafmii_dfi_issuance')}},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
