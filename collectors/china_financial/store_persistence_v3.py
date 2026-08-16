#!/usr/bin/env python3
"""China Financial Git-backed Store persistence V3.

V1/V2 remain preserved. V3 is the clean daily-incremental persistence writer:
* no synchronous collector dependency for model consumers;
* Store-aware low-frequency producers may emit zero new rows on unchanged days;
* canonical Store files are merge/dedup append targets;
* daily manifest/run filenames match V3;
* latest.json is the sole model-facing pointer to active Store files.
"""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from typing import Any

import store_persistence_v1 as v1
import store_persistence_v2 as v2

PIPELINE_VERSION='CF_STORE_PERSISTENCE_V3'
SCHEMA_VERSION='CF_STORE_MANIFEST_V3'


def staged_run(path:Path)->dict[str,Any]:
    return v1.load_json(path/'run.json')


def active_paths(as_of:str,market_date:str)->dict[str,Any]:
    y=as_of[:4];my=market_date[:4]
    return {
      'registry':{'series':'registry/china_financial/series.json','sources':'registry/china_financial/sources.json','methods':'registry/china_financial/methods.json'},
      'fast_market':{'observations':f'data/china_financial/observations/{my}/{market_date}-market-family-v4.json','derived':f'data/china_financial/derived/{my}/{market_date}-market-family-v4.json','run':f'data/china_financial/runs/{my}/{market_date}-market-family-v4.json'},
      'pbc_monthly_credit':{'observations':f'data/china_financial/observations/{y}/{y}-pbc-monthly-credit-v1.json','derived':f'data/china_financial/derived/{y}/{y}-pbc-monthly-credit-v1.json'},
      'mof_fiscal_ytd':{'observations':f'data/china_financial/observations/{y}/{y}-mof-fiscal-ytd-v2.json','derived':f'data/china_financial/derived/{y}/{y}-mof-fiscal-ytd-v2.json'},
      'pbc_monthly_policy_tools':{'observations':f'data/china_financial/observations/{y}/{y}-pbc-monthly-policy-tools-v2.json'},
      'nafmii_dfi_issuance':{'observations':f'data/china_financial/observations/{y}/{y}-nafmii-dfi-issuance-v1.json'},
      'rrr_event_incremental':{'observations':f'data/china_financial/observations/{y}/{y}-rrr-event-v5-incremental.json'},
      'policy_event_incremental':{'observations':f'data/china_financial/observations/{y}/{y}-policy-event-v6-incremental.json'},
      'local_gov_cash_clock':{'observations':f'data/china_financial/observations/{y}/{y}-local-gov-cash-clock-v3.json','context':f'data/china_financial/contexts/{y}/{as_of}-local-gov-cash-clock-v3.json','diagnostics':f'data/china_financial/diagnostics/{y}/{as_of}-local-gov-cash-clock-v3.json'},
      'central_gov_cash_clock':{'observations':f'data/china_financial/observations/{y}/{y}-central-gov-cash-clock-v5.json','context':f'data/china_financial/contexts/{y}/{as_of}-central-gov-cash-clock-v5.json','diagnostics':f'data/china_financial/diagnostics/{y}/{as_of}-central-gov-cash-clock-v5.json'},
    }


def latest_ref(path:str)->str|None:
    return v1.latest_period(path)


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--as-of',required=True);ap.add_argument('--year',required=True);ap.add_argument('--store-root',default='data/china_financial')
    for flag in ('fast','credit','fiscal','policy-tools','nafmii','rrr','omo','local','central'):ap.add_argument(f'--{flag}-dir',required=True)
    ap.add_argument('--summary-out');a=ap.parse_args();store=Path(a.store_root);year=str(a.year);prev=v1.previous_manifest(store)
    dirs={k:Path(getattr(a,k.replace('-','_')+'_dir')) for k in ('fast','credit','fiscal','policy-tools','nafmii','rrr','omo','local','central')}

    # Keep helper-emitted persisted run metadata on V3 for rows written today.
    v1.PIPELINE_VERSION=PIPELINE_VERSION
    families:dict[str,Any]={}
    families['fast_market']=v1.persist_fast(store,dirs['fast'])
    families['pbc_monthly_credit']=v1.persist_family_snapshot(store,year,'pbc-monthly-credit-v1',dirs['credit'],has_derived=True)
    families['mof_fiscal_ytd']=v1.persist_family_snapshot(store,year,'mof-fiscal-ytd-v2',dirs['fiscal'],has_derived=True)
    families['pbc_monthly_policy_tools']=v1.persist_family_snapshot(store,year,'pbc-monthly-policy-tools-v2',dirs['policy-tools'],has_derived=False)
    families['nafmii_dfi_issuance']=v1.persist_family_snapshot(store,year,'nafmii-dfi-issuance-v1',dirs['nafmii'],has_derived=False)
    families['rrr_event_incremental']=v1.persist_event_family(store,year,'rrr-event-v5-incremental',dirs['rrr'])
    families['policy_event_incremental']=v1.persist_event_family(store,year,'policy-event-v6-incremental',dirs['omo'])
    families['local_gov_cash_clock']=v2.persist_government_context_v2(store,year,'local-gov-cash-clock-v3',dirs['local'],central=False)
    families['central_gov_cash_clock']=v2.persist_government_context_v2(store,year,'central-gov-cash-clock-v5',dirs['central'],central=True)

    market_date=str(families['fast_market']['market_date']);paths=active_paths(a.as_of,market_date)
    for family,entry in paths.items():
        if family=='registry':continue
        for kind,p in entry.items():
            if kind in ('run',) and family not in ('fast_market',):continue
            if not Path(p).exists():raise v1.StoreConflict(f'active Store path missing: {family}.{kind}={p}')

    policy_state={};omo_state=v1.resolve_omo7d_state(prev,dirs['omo'])
    if omo_state is not None:policy_state['omo7d_rate']=omo_state

    checks={
      'pbc_monthly_credit':staged_run(dirs['credit']),
      'mof_fiscal_ytd':staged_run(dirs['fiscal']),
      'pbc_monthly_policy_tools':staged_run(dirs['policy-tools']),
      'nafmii_dfi_issuance':staged_run(dirs['nafmii']),
      'rrr_event_incremental':staged_run(dirs['rrr']),
      'policy_event_incremental':staged_run(dirs['omo']),
      'local_gov_cash_clock':staged_run(dirs['local']),
      'central_gov_cash_clock':staged_run(dirs['central']),
    }
    for name,run in checks.items():
        if run.get('status')!='PASS':raise v1.StoreConflict(f'{name}: staged run not PASS')

    family_status={}
    for name,payload in families.items():
        path=(paths.get(name) or {}).get('observations')
        family_status[name]={'status':payload.get('status'),'new_store_rows':int(payload.get('new_store_rows',0) or 0),'latest_reference':latest_ref(path),'collection_mode':(checks.get(name) or {}).get('mode')}

    latest={
      'schema_version':SCHEMA_VERSION,'pipeline_version':PIPELINE_VERSION,'as_of':a.as_of,'generated_at':v1.now_iso(),
      'github_run_id':os.environ.get('GITHUB_RUN_ID'),'github_repository':os.environ.get('GITHUB_REPOSITORY'),'github_source_commit':os.environ.get('GITHUB_SHA'),
      'latest_valid_market_session':market_date,'fast_market_coverage':f"{families['fast_market'].get('coverage_count')}/{families['fast_market'].get('expected_count')}",
      'policy_state':policy_state,'family_status':family_status,'active_data_paths':paths,
      'fiscal_context':{'local_government_bond':families['local_gov_cash_clock']['context'],'central_government_bond':families['central_gov_cash_clock']['context']},
      'consumer_contract':{
        'entrypoint':'data/china_financial/manifests/latest.json',
        'standard_runtime_must_not_invoke_collectors':True,
        'store_first_not_store_only':True,
        'consumer_prefers_manifest_selected_active_paths':True,
        'consumer_should_not_glob_all_historical_store_files':True,
        'targeted_git_history_lookup_allowed_when_required':True,
        'runtime_direct_source_fallback_allowed_when_store_insufficient':True,
        'fallback_success_may_continue_without_store_repair':True,
        'fail_closed_only_after_store_and_fallback_insufficient':True,
        'run_local_fallback_must_not_silently_mutate_store':True,
        'collector_schedule_is_independent_of_model_invocation':True,
      },
      'producer_contract':{
        'workflow':'.github/workflows/china-financial-daily-persist-v3.yml','schedule_utc':'30 11 * * *','schedule_china_time':'19:30 Asia/Shanghai',
        'schedule_only_activates_from_default_branch':True,'low_frequency_default':'CHECK_NEW_RELEASE_AND_CARRY_STORE','historical_backfill_default':False,
        'same_key_conflicting_revision_fails_closed':True,'identical_observation_deduplicates_without_vintage_refresh':True,
      },
      'store_layers':{'observations':'normalized root/event observations','derived':'deterministic derived observations','gaps':'blocking data gaps only','contexts':'accepted supporting context snapshots','diagnostics':'non-blocking/source-health diagnostics','runs':'collector/persistence execution manifests','manifests':'model-facing Store pointers and daily Store state'},
    }
    daily_manifest=store/'manifests'/year/f'{a.as_of}-daily-persist-v3.json';latest_manifest=store/'manifests'/'latest.json'
    v1.write_json(daily_manifest,latest);v1.write_json(latest_manifest,latest)
    daily_run={'schema_version':'CF_STORE_DAILY_RUN_V3','pipeline_version':PIPELINE_VERSION,'as_of':a.as_of,'generated_at':latest['generated_at'],'collection_checks':checks,'persistence':families,'manifest':str(daily_manifest)}
    daily_run_path=store/'runs'/year/f'{a.as_of}-daily-persist-v3.json';v1.write_json(daily_run_path,daily_run)
    total=sum(int(x.get('new_store_rows',0) or 0) for x in families.values())
    result={'status':'PASS','pipeline_version':PIPELINE_VERSION,'schema_version':SCHEMA_VERSION,'as_of':a.as_of,'latest_valid_market_session':market_date,'new_store_rows':total,'latest_manifest':str(latest_manifest),'daily_manifest':str(daily_manifest),'daily_run':str(daily_run_path),'consumer_entrypoint':'data/china_financial/manifests/latest.json'}
    if a.summary_out:v1.write_json(Path(a.summary_out),result)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())