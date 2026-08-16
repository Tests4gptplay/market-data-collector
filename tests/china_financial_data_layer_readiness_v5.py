#!/usr/bin/env python3
"""China Financial Draft Data-Layer Readiness V5.

V4 is preserved. V5 keeps the complete V4 readiness result and advances only
the active daily workflow pointer to V5, whose supporting government-bond
publication checks use a short incremental window instead of re-crawling an
entire week on every daily gate.
"""
from __future__ import annotations
import argparse,json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--out'); args=ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'v4.json'
        rc=subprocess.run([sys.executable,str(ROOT/'tests/china_financial_data_layer_readiness_v4.py'),'--out',str(p)],cwd=ROOT,stdout=subprocess.DEVNULL).returncode
        base=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    workflow='.github/workflows/china-financial-daily-ready-v5.yml'
    ok=rc==0 and base.get('status')=='DATA_LAYER_READY' and (ROOT/workflow).exists()
    r=dict(base)
    r['audit']='CHINA_FINANCIAL_DRAFT_DATA_LAYER_READINESS_V5'
    r['status']='DATA_LAYER_READY' if ok else 'DATA_LAYER_NOT_READY'
    r['active_unified_ready_gate']={'workflow':workflow,'status':'PASS' if (ROOT/workflow).exists() else 'FAIL'}
    r['supporting_context_incremental_window_policy']={
        'government_bond_daily_publication_check':'RECENT_2_CALENDAR_DAYS',
        'older_collected_events':'CARRY_FORWARD_FROM_RUNTIME_STORE',
        'historical_recrawl_every_day':False,
        'reason':'Low-frequency supporting context checks only for newly published facts; previously collected state is retained.'
    }
    r['v5_versioning_note']='V1-V4 audits and prior daily READY workflows remain preserved for rollback.'
    text=json.dumps(r,ensure_ascii=False,indent=2)+'\n'
    if args.out:
        q=Path(args.out);q.parent.mkdir(parents=True,exist_ok=True);q.write_text(text,encoding='utf-8')
    print(text,end='')
    return 0 if ok else 2
if __name__=='__main__': raise SystemExit(main())
