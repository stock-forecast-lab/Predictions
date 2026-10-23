"""Run as: python -m models.v3.main --market JP|US. No V1/V2 import mutation."""
import argparse
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
from time import perf_counter
import numpy as np
import pandas as pd
from . import bridge,edinet,sec
from .config import ROOT,VERSION,PARSER_VERSION,FINANCIAL_FEATURES
from .financial_features import attach_financials
from .prices import prices
from .storage import load_facts,save_bundle,bundles,evaluate,forecast_tickers,now


def forecast_origin(panel,cutoff,horizons,base_columns,model_names=None,train_sessions=756,
                    min_history=252,profile='light',quick=False,base_set='M3'):
    finance_columns=FINANCIAL_FEATURES+[c+'_missing' for c in FINANCIAL_FEATURES]
    te=panel[(panel.date==cutoff)&panel.fin_available].dropna(subset=base_columns)
    if te.empty:raise RuntimeError('No forecast stocks with contemporaneous financial coverage')
    results=[]
    for h in horizons:
        tr=bridge.features.training_rows(panel,cutoff,h,min_history,base_columns)
        tr=tr[tr.fin_available].copy()
        if train_sessions:
            tr=tr[tr.date.isin(sorted(tr.date.unique())[-train_sessions:])]
        if tr.date.nunique()<min_history:
            raise RuntimeError(f'Financial history not ready: h={h}, matured sessions={tr.date.nunique()}/{min_history}')
        # Both estimations receive exactly the same rows and exactly the same
        # held-out tickers. Optional missing financial features are imputed using
        # training data only, with explicit missingness indicators.
        for label,columns in [(base_set+'_CONTROL',base_columns),
                              (base_set+'_FINANCIAL',base_columns+finance_columns)]:
            print(f'V3 h={h} {label}: {len(tr):,} matched rows, {tr.date.nunique()} sessions, {len(te)} stocks',flush=True)
            pred=bridge.estimators.predict(tr,te,columns,f'target_{h}',model_names,quick,profile)
            sample_hash=hashlib.sha256(tr[['date','ticker']].to_csv(index=False).encode()).hexdigest()
            for name,values in pred.items():
                z=te[['date','ticker','Close','fin_source_ids']].rename(columns={'date':'data_last_observation_date','Close':'close_at_forecast'}).copy()
                z['prediction']=values;z['horizon']=h;z['information_set']=label;z['model']=name
                z['train_rows']=len(tr);z['train_sessions']=tr.date.nunique()
                z['train_start']=tr.date.min();z['train_end']=tr.date.max()
                z['train_last_label_end']=tr[f'label_end_{h}'].max();z['training_sample_sha256']=sample_hash
                results.append(z)
    return pd.concat(results,ignore_index=True)


def status(text):
    print(text,flush=True)
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:f.write(text+'\n\n')


def run(args,root=ROOT):
    root=Path(root)
    complete,calendar=bridge.market.completed_schedule(args.market,bridge.config.START)
    cutoff=complete.index[-1]
    tickers,membership=bridge.universe(args.market,root,cutoff.date().isoformat())
    if args.sync:
        if args.market=='JP':
            sync=edinet.synchronize(root,tickers,args.financial_start,
                    pd.Timestamp.now(tz='Asia/Tokyo').date().isoformat(),args.max_scan_days)
        else:
            sync=sec.synchronize(root,tickers,args.financial_start)
        status('V3 financial synchronization: '+json.dumps(sync,ensure_ascii=False))
    else:
        sync={'provider':'local_archive','complete':None}
    if args.sync_only:
        status('Financial synchronization only; no forecasts generated.');return None
    facts=load_facts(root,args.market,set(tickers))
    if facts.empty:
        status('V3 BOOTSTRAP: no usable financial facts yet; no forecasts generated. Continue synchronization.')
        return None
    if args.base_set!='M1' and not os.getenv('FRED_API_KEY'):
        raise RuntimeError('V3 M2/M3 baseline requires FRED_API_KEY; --base-set M1 is available without it')
    started=perf_counter()
    ctx,m2,m3,evidence=bridge.context.context_features(complete.market_close,args.market,[args.base_set],
        (pd.Timestamp(bridge.config.START)-pd.DateOffset(years=2)).date().isoformat(),
        pd.Timestamp.now(tz='UTC').date().isoformat(),root/'data/context_cache')
    base_columns=bridge.features.MARKET_FEATURES+(m2 if args.base_set in {'M2','M3'} else [])+(m3 if args.base_set=='M3' else [])
    quotes=prices(sorted(set(tickers)|forecast_tickers(root,args.market)),bridge.config.START,cutoff)
    quotes=quotes[quotes.date.isin(complete.index)]
    print(f'Context and price data: {perf_counter()-started:.1f}s',flush=True)
    evaluation=evaluate(quotes,args.market,root)
    if evaluation:print('V3 matured forecasts evaluated: '+str(evaluation),flush=True)
    panel=bridge.features.make_panel(quotes[quotes.ticker.isin(tickers)],complete.index,args.horizons)
    panel=panel.merge(ctx,on='date',how='left',validate='many_to_one')
    panel=attach_financials(panel,facts,complete.market_close,args.max_age_days)
    n=panel.loc[(panel.date==cutoff)&panel.fin_available].dropna(subset=base_columns).ticker.nunique()
    coverage=n/len(tickers)
    status(f'V3 {args.market} financial forecast coverage: {n}/{len(tickers)} ({coverage:.1%}).')
    if coverage<args.min_coverage:
        message=f'Financial coverage below configured minimum {args.min_coverage:.0%}; no forecasts generated.'
        if sync.get('complete') is False:
            status('V3 BOOTSTRAP: '+message+' Continue synchronization.');return None
        raise RuntimeError(message+' Check filing mappings and the archived financial data.')
    settings=dict(base_set=args.base_set,horizons=args.horizons,models=args.models,
        train_sessions=args.train_sessions,profile=args.profile,estimator_parameters=bridge.estimators.profile_settings(args.profile),
        max_age_days=args.max_age_days,min_coverage=args.min_coverage,parser_version=PARSER_VERSION,
        financial_features=FINANCIAL_FEATURES,availability_policy='timestamp+1_calendar_day; SEC date-only+2_days')
    signature=hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
    folder=root/'predictions/v3'/args.market
    for _,meta in bundles(folder):
        if meta.get('data_last_observation_date')==cutoff.date().isoformat() and meta.get('settings_sha256')==signature:
            status('V3 already forecast for this observation date and settings; existing results retained.');return None
    try:
        result=forecast_origin(panel,cutoff,args.horizons,base_columns,args.models,args.train_sessions,
                               profile=args.profile,base_set=args.base_set)
    except RuntimeError as exc:
        if str(exc).startswith('Financial history not ready') and sync.get('complete') is False:
            status('V3 BOOTSTRAP: '+str(exc)+'; continue synchronization. No forecasts generated.');return None
        raise
    origin=calendar.index.get_loc(cutoff)
    targets={h:calendar.index[origin+h].date().isoformat() for h in args.horizons}
    result['data_last_observation_date']=pd.to_datetime(result.data_last_observation_date).dt.strftime('%Y-%m-%d')
    result['forecast_for_session_date']=result.horizon.map(targets)
    result['market']=args.market;result['model_version']=VERSION
    result['system_version']='V3';result['schema_version']=1;result['return_basis']='adjusted_close_ratio'
    result['forecast_created_at_utc']=now()
    used=sorted(result.ticker.unique().tolist())
    financial_snapshot=panel[(panel.date==cutoff)&panel.ticker.isin(used)][
        ['date','ticker','RawClose','fin_source_ids','fin_scope','fin_currency','fin_assets_end','fin_income_end',*FINANCIAL_FEATURES]]
    audit=save_bundle(financial_snapshot,root/'data/fundamentals/v3'/args.market/'feature_snapshots',
                      f'{cutoff:%Y-%m-%d}_{signature[:12]}',{'settings_sha256':signature,'kind':'financial_feature_audit'})
    metadata=dict(financial_snapshot_file=audit.relative_to(root).as_posix(),
        financial_snapshot_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),market=args.market,data_last_observation_date=cutoff.date().isoformat(),settings=settings,
        settings_sha256=signature,features={'control':base_columns,'financial':base_columns+FINANCIAL_FEATURES+[c+'_missing' for c in FINANCIAL_FEATURES]},
        universe=membership,financial_coverage=coverage,used_tickers=used,synchronization=sync,
        macro_sources=evidence,financial_facts_sha256=hashlib.sha256(facts.sort_values(['ticker','available_at_utc','metric','filing_id']).to_csv(index=False).encode()).hexdigest(),
        corporate_data_sources={'JP':'EDINET annual/interim XBRL','US':'SEC companyfacts + submissions'},
        valuation_basis='last disclosed net shares, split-adjusted through feature date; estimated, not observed capitalization',
        survivorship_bias_in_historical_training=True,training='daily refit from scratch; matched CONTROL/FINANCIAL rows',rows=len(result))
    path=save_bundle(result,folder,f'{cutoff:%Y-%m-%d}_{VERSION}',metadata)
    status(f'V3 forecast saved: {path.relative_to(root)} ({len(result):,} rows).')
    return path


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--market',choices=['JP','US'],required=True)
    p.add_argument('--base-set',choices=['M1','M2','M3'],default='M3')
    p.add_argument('--profile',choices=['light','standard'],default='light')
    p.add_argument('--horizons',nargs='+',type=int,choices=[1,5,20],default=[1,5,20])
    p.add_argument('--models',nargs='+',choices=['HistoricalMean','OLS','Ridge','Lasso','ElasticNet','RF','XGB'])
    p.add_argument('--train-sessions',type=int,default=756)
    p.add_argument('--financial-start',default='2018-01-01')
    p.add_argument('--max-scan-days',type=int,default=730,help='EDINET calendar days processed per run; progress persists')
    p.add_argument('--max-age-days',type=int,default=550)
    p.add_argument('--min-coverage',type=float,default=.8)
    p.add_argument('--sync',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--sync-only',action='store_true')
    a=p.parse_args()
    if a.train_sessions<252:p.error('--train-sessions must be >=252')
    if a.max_scan_days<1 or a.max_age_days<1:p.error('scan/age limits must be positive')
    if not 0<a.min_coverage<=1:p.error('--min-coverage must be in (0,1]')
    try:pd.Timestamp(a.financial_start)
    except ValueError:p.error('invalid --financial-start')
    if pd.Timestamp(a.financial_start)>pd.Timestamp.now().normalize():p.error('--financial-start must not be in the future')
    a.horizons=list(dict.fromkeys(a.horizons))
    return a

if __name__=='__main__':run(arguments())
