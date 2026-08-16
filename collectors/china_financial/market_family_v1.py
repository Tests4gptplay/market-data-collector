#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,re,sys
from pathlib import Path
from urllib.parse import urlencode

REPO_ROOT=Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents)>=3 else Path.cwd()
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from collectors.china_financial import fast_market as base

COLLECTOR_VERSION='V1.9-CANDIDATE-MARKET-FAMILY-V1'
INTERFACE_VERSION='CF_INTERFACE_V1'
CURVE_HIS=base.CHINAMONEY_ROOT+'/ags/ms/cm-u-bk-currency/ClsYldCurvHis'
FRR_HIS=base.CHINAMONEY_ROOT+'/ags/ms/cm-u-bk-currency/FrrHis'
CURVE_REFERER=base.CHINAMONEY_ROOT+'/chinese/bkcurvclosedy/'
FDR_REFERER=base.CHINAMONEY_ROOT+'/chinese/bkfrr/'

CURVES={
 'CGB':('CYCC000',{1.0:'SOV_CGB_1Y',2.0:'SOV_CGB_2Y',3.0:'SOV_CGB_3Y',5.0:'SOV_CGB_5Y',10.0:'SOV_CGB_10Y',30.0:'SOV_CGB_30Y'},'CGB_YIELD_CURVE'),
 'CDB':('CYCC021',{10.0:'SOV_CDB_10Y'},'CDB_YIELD_CURVE'),
 'MTN_AAA':('CYCC82B',{1.0:'CRD_MTN_AAA_1Y',3.0:'CRD_MTN_AAA_3Y'},'MTN_AAA_YIELD_CURVE'),
 'MTN_AAP':('CYCC82D',{3.0:'CRD_MTN_AAP_3Y'},'MTN_AA_PLUS_YIELD_CURVE'),
 'MTN_AA':('CYCC82E',{3.0:'CRD_MTN_AA_3Y'},'MTN_AA_YIELD_CURVE'),
 'NCD_AAA':('CYCC41B',{0.25:'FUND_NCD_AAA_3M',1.0:'FUND_NCD_AAA_1Y'},'PURE_NCD_AAA_YIELD_CURVE'),
}

MARKET_EXPECTED={
 'FUND_DR001','FUND_DR007','FUND_DR014','FUND_R001','FUND_R007','FUND_R014',
 'FUND_GC001','FUND_GC007','FUND_NCD_AAA_3M','FUND_NCD_AAA_1Y','FUND_FDR007','FUND_FR007',
 'SOV_CGB_1Y','SOV_CGB_2Y','SOV_CGB_3Y','SOV_CGB_5Y','SOV_CGB_10Y','SOV_CGB_30Y','SOV_CDB_10Y',
 'CRD_MTN_AAA_1Y','CRD_MTN_AAA_3Y','CRD_MTN_AAP_3Y','CRD_MTN_AA_3Y',
 'CRD_SPREAD_AAA_1Y','CRD_SPREAD_AAA_3Y','CRD_SPREAD_AAP_3Y','CRD_SPREAD_AA_3Y'
}

def obs(**kw):
    row=base.observation(**kw)
    row['collector_version']=COLLECTOR_VERSION; row['interface_version']=INTERFACE_VERSION
    return row

def mk_gap(sid,date,cls,msg,stage='COLLECT'):
    g=base.gap(sid,date,cls,stage,msg); g['collector_version']=COLLECTOR_VERSION; g['interface_version']=INTERFACE_VERSION; return g

def post_json(url,date,raw_dir,name,params,referer):
    full=url+'?'+urlencode(params)
    r=base.http(full,method='POST',data=b'',referer=referer,accept='application/json, text/javascript, */*; q=0.01',headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','Origin':base.CHINAMONEY_ROOT,'X-Requested-With':'XMLHttpRequest'})
    digest=base.save_raw(raw_dir,name,r)
    if not r.ok: raise RuntimeError(r.error or f'{name} request failed')
    return json.loads(base.decode(r.raw,r.content_type)),digest,r.url

def collect_dr_family(date,raw_dir):
    r=base.http(base.DR_CHART,method='POST',data=b'',referer=base.CHINAMONEY_ROOT+'/chinese/mkdatapm/')
    digest=base.save_raw(raw_dir,'chinamoney_prr_chart.csv',r)
    if not r.ok: raise RuntimeError(r.error or 'DR chart request failed')
    target=base.normalize_date_text(date); matches=[]
    for row in csv.reader(base.decode(r.raw,r.content_type).splitlines()):
        if row and base.normalize_date_text(row[0])==target: matches.append([x.strip() for x in row])
    if not matches: raise RuntimeError('target date not found in official prr-chrt.csv')
    row=matches[-1]; mapping={'FUND_DR001':6,'FUND_DR007':7,'FUND_DR014':8}; out=[]
    for sid,idx in mapping.items():
        if len(row)<=idx or not row[idx]: raise RuntimeError(f'{sid} missing at official chart column {idx}')
        out.append(obs(series_id=sid,reference_date=date,value=float(row[idx]),unit='percent',provider='ChinaMoney',semantic='ACTUAL_DEPOSITORY_INSTITUTION_PLEDGED_REPO_WEIGHTED_RATE',source_url=r.url,evidence_sha256=digest,collected_at=base.now_iso(),extra={'instrument':sid.replace('FUND_',''),'selection_rule':f'official prr-chart column {idx}'}))
    return out

def collect_fixing_family(date,raw_dir):
    payload,digest,url=post_json(FRR_HIS,date,raw_dir,'chinamoney_frr_history.json',{'lang':'CN','startDate':date,'endDate':date},FDR_REFERER)
    recs=payload.get('records',[]) if isinstance(payload,dict) else []
    exact=[r for r in recs if str(r.get('lfiProducDate',''))==date or (isinstance(r.get('frValueMap'),dict) and str(r['frValueMap'].get('date',''))==date)]
    if len(exact)!=1: raise RuntimeError(f'expected one FrrHis target-date row; got {len(exact)}')
    m=exact[0].get('frValueMap') or {}
    out=[]
    for field,sid,parent in [('FDR007','FUND_FDR007','FUND_DR007'),('FR007','FUND_FR007','FUND_R007')]:
        if field not in m or m[field] in (None,''): raise RuntimeError(f'{field} missing in official FrrHis frValueMap')
        out.append(obs(series_id=sid,reference_date=date,value=float(m[field]),unit='percent',provider='ChinaMoney',semantic=f'{field}_FIXING_QC_ONLY',source_url=url,evidence_sha256=digest,collected_at=base.now_iso(),role='QC_DIAGNOSTIC',extra={'selected_field':field,'substitution_prohibited_for':parent}))
    return out

def collect_gc_family(date,raw_dir):
    params={'jsonCallBack':'jsonpCallback','isPagination':'false','sqlId':'COMMON_SSEBOND_SCSJ_SCTJ_SCGL_ZQZYSHGSCGL_CX_L','TRADE_DATE':date}
    url=base.SSE_QUERY+'?'+urlencode(params); r=base.http(url,referer=base.SSE_REFERER); digest=base.save_raw(raw_dir,'sse_gc_family.jsonp',r)
    if not r.ok: raise RuntimeError(r.error or 'SSE GC request failed')
    text=base.decode(r.raw,r.content_type); m=re.match(r'^jsonpCallback\((.*)\)\s*;?\s*$',text,re.S)
    if not m: raise RuntimeError('unexpected SSE JSONP wrapper')
    recs=json.loads(m.group(1)).get('result',[]); out=[]
    for code,name,sid in [('204001','GC001','FUND_GC001'),('204007','GC007','FUND_GC007')]:
        exact=[x for x in recs if x.get('BOND_CODE')==code and x.get('BOND_NAME')==name and x.get('TRADE_DATE')==date]
        if len(exact)!=1: raise RuntimeError(f'expected one {name} row, got {len(exact)}')
        out.append(obs(series_id=sid,reference_date=date,value=float(exact[0]['WEIGHT_RATE']),unit='percent',provider='SSE',semantic='ACTUAL_EXCHANGE_REPO_WEIGHTED_AVERAGE_RATE',source_url=r.url,evidence_sha256=digest,collected_at=base.now_iso(),extra={'bond_code':code,'bond_name':name,'field':'WEIGHT_RATE'}))
    return out

def collect_curve(date,raw_dir,key):
    bond_type,tenors,semantic=CURVES[key]
    params={'lang':'CN','reference':'1','bondType':bond_type,'startDate':date,'endDate':date,'termId':'','pageNum':'1','pageSize':'100'}
    payload,digest,url=post_json(CURVE_HIS,date,raw_dir,f'chinamoney_curve_{bond_type}.json',params,CURVE_REFERER)
    recs=payload.get('records',[]) if isinstance(payload,dict) else []
    parsed={}
    for r in recs:
        try:
            if str(r.get('newDateValueCN',''))!=date: continue
            term=float(r.get('yearTermStr')); value=float(r.get('maturityYieldStr'))
            parsed[round(term,8)]=value
        except Exception: continue
    out=[]
    for term,sid in tenors.items():
        hits=[v for t,v in parsed.items() if abs(t-term)<1e-8]
        if len(hits)!=1: raise RuntimeError(f'{key} exact tenor {term}Y missing/ambiguous; parsed_terms={sorted(parsed)}')
        role='QC_DIAGNOSTIC' if sid=='SOV_CDB_10Y' else 'CORE'
        out.append(obs(series_id=sid,reference_date=date,value=hits[0],unit='percent',provider='CFETS / ChinaMoney',semantic=semantic,source_url=url,evidence_sha256=digest,collected_at=base.now_iso(),role=role,extra={'bond_type':bond_type,'tenor_years':term,'curve_key':key}))
    return out

def collect_chinabond_fallback(date,raw_dir):
    params={'startDate':date,'endDate':date,'gjqx':'0','qxId':'ycqx','locale':'cn_ZH','mark':'1'}; url=base.CHINABOND_HISTORY+'?'+urlencode(params)
    r=base.http(url,accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'); digest=base.save_raw(raw_dir,'chinabond_fallback_full.html',r)
    if not r.ok: raise RuntimeError(r.error or 'ChinaBond fallback request failed')
    p=base.ChinaBondTableParser(); p.feed(base.decode(r.raw,r.content_type)); wanted={base.norm_html(base.CGB_NAME):'CGB',base.norm_html(base.AAA_MTN_NAME):'AAA'}; rows={}; target=base.norm_html(date)
    for rr in p.rows:
        cells=[c.strip() for c in rr]; nc=[base.norm_html(c) for c in cells]; ci=next((i for i,c in enumerate(nc) if c in wanted),None); di=next((i for i,c in enumerate(nc) if c==target),None)
        if ci is None or di is None: continue
        vals=cells[di+1:di+1+len(base.CHINABOND_TENORS)]+['']*len(base.CHINABOND_TENORS)
        rows[wanted[nc[ci]]]={ten:float(v.replace('%','')) if v.strip() else None for ten,v in zip(base.CHINABOND_TENORS,vals)}
    if 'CGB' not in rows or 'AAA' not in rows: raise RuntimeError('ChinaBond fallback curves missing')
    return rows,digest,r.url

def derive_spreads(date,observations,raw_dir,gaps):
    idx={x['series_id']:x for x in observations}; derived=[]
    for tenor,credit_sid,cgb_sid,spread_sid in [(1,'CRD_MTN_AAA_1Y','SOV_CGB_1Y','CRD_SPREAD_AAA_1Y'),(3,'CRD_MTN_AAA_3Y','SOV_CGB_3Y','CRD_SPREAD_AAA_3Y')]:
        c=idx.get(credit_sid); g=idx.get(cgb_sid)
        if c and g and c['provider']==g['provider']:
            cv,gv=c['value'],g['value']; provider=c['provider']; pe=[c['evidence_sha256'],g['evidence_sha256']]
        else:
            try:
                rows,digest,url=collect_chinabond_fallback(date,raw_dir); key=f'{tenor}年'; cv=rows['AAA'].get(key); gv=rows['CGB'].get(key)
                if cv is None or gv is None: raise RuntimeError(f'ChinaBond fallback {key} missing')
                provider='ChinaBond'; pe=[digest]
                if not c:
                    observations.append(obs(series_id=credit_sid,reference_date=date,value=cv,unit='percent',provider='ChinaBond',semantic='CP_NOTE_AAA_YIELD_CURVE_SAME_PROVIDER_FALLBACK',source_url=url,evidence_sha256=digest,collected_at=base.now_iso(),extra={'tenor':f'{tenor}Y','fallback':True}))
                if not g:
                    observations.append(obs(series_id=cgb_sid,reference_date=date,value=gv,unit='percent',provider='ChinaBond',semantic='CGB_YIELD_CURVE_SAME_PROVIDER_FALLBACK',source_url=url,evidence_sha256=digest,collected_at=base.now_iso(),extra={'tenor':f'{tenor}Y','fallback':True}))
            except Exception as e:
                gaps.append(mk_gap(spread_sid,date,'FALLBACK_SOURCE_GAP',str(e),'DERIVE')); continue
        derived.append({'series_id':spread_sid,'reference_date':date,'value':round((float(cv)-float(gv))*100,6),'unit':'bp','method_id':'CREDIT_SPREAD_PROXY_V1.1','provider_consistency':provider+'+'+provider,'parent_series':[credit_sid,cgb_sid],'parent_evidence':pe,'formula':f'({credit_sid} - {cgb_sid}) * 100','collector_version':COLLECTOR_VERSION,'interface_version':INTERFACE_VERSION})
    for credit_sid,spread_sid in [('CRD_MTN_AAP_3Y','CRD_SPREAD_AAP_3Y'),('CRD_MTN_AA_3Y','CRD_SPREAD_AA_3Y')]:
        c=idx.get(credit_sid); g=idx.get('SOV_CGB_3Y')
        if not c or not g or c['provider']!=g['provider']:
            gaps.append(mk_gap(spread_sid,date,'SOURCE_SPEC_GAP','AA+/AA spread requires same-provider CFETS parents; no ChinaBond fallback authorized','DERIVE')); continue
        derived.append({'series_id':spread_sid,'reference_date':date,'value':round((float(c['value'])-float(g['value']))*100,6),'unit':'bp','method_id':'CREDIT_SPREAD_PROXY_V1.1','provider_consistency':c['provider']+'+'+g['provider'],'parent_series':[credit_sid,'SOV_CGB_3Y'],'parent_evidence':[c['evidence_sha256'],g['evidence_sha256']],'formula':f'({credit_sid} - SOV_CGB_3Y) * 100','collector_version':COLLECTOR_VERSION,'interface_version':INTERFACE_VERSION})
    return derived

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--out',default='out/china_financial_market'); args=ap.parse_args()
    root=Path(args.out)/args.date; raw=root/'raw'; root.mkdir(parents=True,exist_ok=True)
    observations=[]; derived=[]; gaps=[]; attempts=[]
    jobs=[('DR_FAMILY',collect_dr_family),('FIXING_FAMILY',collect_fixing_family),('GC_FAMILY',collect_gc_family)]
    for key,fn in jobs:
        try: observations.extend(fn(args.date,raw)); attempts.append({'family':key,'status':'SUCCESS'})
        except Exception as e: attempts.append({'family':key,'status':'GAP','message':str(e)}); gaps.append(mk_gap(key,args.date,'SOURCE_OR_PARSER_GAP',str(e)))
    for key in CURVES:
        try: observations.extend(collect_curve(args.date,raw,key)); attempts.append({'family':'CURVE_'+key,'status':'SUCCESS'})
        except Exception as e:
            attempts.append({'family':'CURVE_'+key,'status':'GAP','message':str(e)})
            for sid in CURVES[key][1].values(): gaps.append(mk_gap(sid,args.date,'SOURCE_OR_PARSER_GAP',str(e)))
    for sid in ['FUND_R001','FUND_R007','FUND_R014']:
        gaps.append(mk_gap(sid,args.date,'ADAPTER_RUNTIME_CAPABILITY_GAP','Exact public ChinaMoney R-family actual-rate route not yet promoted; FR fixing substitution forbidden.'))
    attempts.append({'family':'R_ACTUAL_FAMILY','status':'PENDING_EXACT_ROUTE'})
    derived.extend(derive_spreads(args.date,observations,raw,gaps))
    uniq={}; final=[]
    for x in observations:
        k=(x['series_id'],x.get('provider'))
        if k in uniq and abs(float(uniq[k]['value'])-float(x['value']))>1e-12:
            gaps.append(mk_gap(x['series_id'],args.date,'DUPLICATE_CONFLICT',f'conflicting duplicate provider values: {uniq[k]["value"]} vs {x["value"]}')); continue
        if k not in uniq: uniq[k]=x; final.append(x)
    observations=final
    present={x['series_id'] for x in observations}|{x['series_id'] for x in derived}; missing=sorted(MARKET_EXPECTED-present)
    run={'module':'china_financial_market_family','collector_version':COLLECTOR_VERSION,'interface_version':INTERFACE_VERSION,'target_date':args.date,'expected_series':sorted(MARKET_EXPECTED),'present_series':sorted(MARKET_EXPECTED & present),'missing_series':missing,'coverage_count':len(MARKET_EXPECTED & present),'expected_count':len(MARKET_EXPECTED),'status':'PASS' if not missing else 'INCOMPLETE','attempts':attempts,'completed_at':base.now_iso()}
    for n,p in [('observations.json',observations),('derived.json',derived),('gaps.json',gaps),('run.json',run)]: (root/n).write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not missing else 2
if __name__=='__main__': raise SystemExit(main())
