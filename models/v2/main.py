"""V2 entry point. Existing V1 predictions are never modified."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
from config import ROOT, START, VERSION, HORIZONS, MIN_HISTORY
from context_data import context_features
from features import make_panel, training_rows, MARKET_FEATURES
from market_data import completed_schedule, universe, prices
from models import predict
from storage import save_bundle, bundles, evaluate, now_utc, forecast_tickers
from metrics import summarize


def information_sets(m2, m3):
    return {'M1':MARKET_FEATURES, 'M2':MARKET_FEATURES+m2, 'M3':MARKET_FEATURES+m2+m3}


def forecast_origin(panel, cutoff, horizons, sets, columns, names, train_sessions=756,
                    min_history=MIN_HISTORY, quick=False):
    # Matched samples ensure the M1/M2/M3 comparison measures the extra information,
    # not a different set of stocks or a different training period.
    all_cols = list(dict.fromkeys(c for s in sets for c in columns[s]))
    te = panel[panel.date == cutoff].dropna(subset=all_cols)
    if te.empty:
        raise RuntimeError(f'No complete features on {cutoff}')
    result = []
    for h in horizons:
        tr = training_rows(panel,cutoff,h,min_history,all_cols)
        if train_sessions:
            dates = sorted(tr.date.unique())[-train_sessions:]
            tr = tr[tr.date.isin(dates)]
        for s in sets:
            predictions = predict(tr,te,columns[s],f'target_{h}',names,quick)
            for model, values in predictions.items():
                z = te[['date','ticker','Close',f'target_{h}']].copy()
                z = z.rename(columns={'Close':'close_at_forecast',f'target_{h}':'actual'})
                z['horizon'],z['information_set'],z['model'] = h,s,model
                z['prediction'] = values
                z['historical_mean'] = tr[f'target_{h}'].mean()
                z['train_last_label_end'] = tr[f'label_end_{h}'].max()
                z['train_rows'],z['train_sessions'] = len(tr),tr.date.nunique()
                result.append(z)
    return pd.concat(result,ignore_index=True)


def backtest(panel, market, sets, columns, args, root):
    dates = pd.DatetimeIndex(sorted(panel.date.unique()))
    outputs = []
    for h in args.horizons:
        origins = dates[dates>=pd.Timestamp(args.backtest_start)][::h]
        # At the latest origins the h-day return is not observed yet.
        origins = [d for d in origins if panel.loc[panel.date==d,f'label_end_{h}'].notna().any()]
        if args.max_origins:
            origins = origins[:args.max_origins]
        for number,day in enumerate(origins,1):
            print(f'Backtest {market} h={h}: {number}/{len(origins)} {day.date()}',flush=True)
            z = forecast_origin(panel,day,[h],sets,columns,args.models,args.train_sessions)
            outputs.append(z.dropna(subset=['actual']))
    if not outputs:
        raise RuntimeError('No mature backtest observations; change --backtest-start')
    z = pd.concat(outputs,ignore_index=True)
    z['market'] = market
    folder = root/'output/v2/backtests'/market
    metadata = {'market':market,'kind':'pseudo_oos','survivorship_bias':True,
                'current_adjusted_prices':True,'transaction_cost_bps':args.cost_bps,
                'settings':settings(args),'features':columns,
                'execution':'same_close_diagnostic_not_executable_strategy'}
    path = save_bundle(z,folder,'predictions',metadata)
    metrics, portfolio = summarize(z,args.cost_bps)
    metric_path = save_bundle(metrics,folder,'metrics',dict(metadata,predictions_file=path.name))
    if not portfolio.empty:
        save_bundle(portfolio,folder,'portfolios',dict(metadata,predictions_file=path.name))
    print(f'Backtest metrics: {metric_path}\n{metrics.to_string(index=False)}')


def settings(args):
    return {k:getattr(args,k) for k in ['sets','horizons','models','train_sessions']}


def run(args):
    root = ROOT
    if args.sets != ['M1'] and not os.getenv('FRED_API_KEY'):
        raise RuntimeError('Add FRED_API_KEY to repository Settings > Secrets and variables > Actions. '
                           'Without a key only --sets M1 is available.')
    complete, calendar = completed_schedule(args.market,START)
    cutoff = complete.index[-1]
    tickers, membership = universe(args.market,root,cutoff.date().isoformat())
    # Delisted/unavailable quotes remain pending, but an ordinary index removal
    # must not prevent an already-created forecast from reaching evaluation.
    quote_tickers = sorted(set(tickers) | forecast_tickers(root,args.market))
    px = prices(quote_tickers,START,cutoff)
    px = px[px.date.isin(complete.index)]
    observed = set(px.loc[px.date==cutoff,'ticker']) & set(tickers)
    if len(observed)<np.ceil(.95*len(tickers)):
        raise RuntimeError(f'Latest completed session {cutoff.date()}: coverage {len(observed)}/{len(tickers)} below 95%')
    cache = root/'data/context_cache'
    ctx,m2,m3,evidence = context_features(complete.market_close,args.market,args.sets,
        (pd.Timestamp(START)-pd.DateOffset(years=2)).date().isoformat(),
        pd.Timestamp.now(tz='UTC').date().isoformat(),cache)
    panel = make_panel(px[px.ticker.isin(tickers)],complete.index,args.horizons).merge(ctx,on='date',how='left',validate='many_to_one')
    columns = information_sets(m2,m3)
    ev = evaluate(px,args.market,root)
    if ev:
        print(f'Previous forecasts evaluated: {ev}')
    if args.backtest:
        backtest(panel,args.market,args.sets,columns,args,root)
        return
    signature = hashlib.sha256(json.dumps(settings(args),sort_keys=True).encode()).hexdigest()
    folder = root/'predictions/v2'/args.market
    for _,manifest in bundles(folder):
        if (manifest.get('data_last_observation_date')==cutoff.date().isoformat() and
            manifest.get('settings_sha256')==signature and manifest.get('model_version')==VERSION):
            print(f'Already forecast: {args.market} {cutoff.date()} {VERSION}; no duplicate generated.')
            return
    result = forecast_origin(panel,cutoff,args.horizons,args.sets,columns,args.models,args.train_sessions)
    if result.ticker.nunique()<np.ceil(.95*len(tickers)):
        raise RuntimeError('Complete-feature forecast coverage below 95%; no forecast saved')
    targets = {}
    position = calendar.index.get_loc(cutoff)
    for h in args.horizons:
        targets[h] = calendar.index[position+h].date().isoformat()
    result = result.drop(columns=['actual']).rename(columns={'date':'data_last_observation_date'})
    result['data_last_observation_date'] = pd.to_datetime(result.data_last_observation_date).dt.strftime('%Y-%m-%d')
    result['forecast_for_session_date'] = result.horizon.map(targets)
    result['market'],result['model_version'],result['forecast_created_at_utc'] = args.market,VERSION,now_utc()
    result['schema_version'],result['system_version'],result['return_basis'] = 1,'V2','adjusted_close_ratio'
    path = save_bundle(result,folder,f'{cutoff:%Y-%m-%d}_{VERSION}',{
        'market':args.market,'data_last_observation_date':cutoff.date().isoformat(),
        'forecast_cutoff_at_utc':str(complete.market_close.iloc[-1]),
        'settings':settings(args),'settings_sha256':signature,'features':columns,
        'universe':membership,'context_sources':evidence,
        'vintage_delay_days':2,'rows':len(result),'survivorship_bias_in_historical_training':True})
    print(f'Forecast saved: {path}')
    print(result[result.model=='Ensemble'].sort_values('prediction',ascending=False)
          [['ticker','horizon','information_set','prediction']].head(20).to_string(index=False))


def arguments():
    ap = argparse.ArgumentParser(description='V2 point-in-time market + FX/rates + macro forecast')
    ap.add_argument('--market',choices=['JP','US'],required=True)
    ap.add_argument('--sets',nargs='+',choices=['M1','M2','M3'],default=['M1','M2','M3'])
    ap.add_argument('--horizons',nargs='+',type=int,choices=HORIZONS,default=list(HORIZONS))
    ap.add_argument('--models',nargs='+',choices=['HistoricalMean','OLS','Ridge','Lasso','ElasticNet','RF','XGB'])
    ap.add_argument('--train-sessions',type=int,default=756,help='Trailing matured training sessions; 0=expanding')
    ap.add_argument('--backtest',action='store_true')
    ap.add_argument('--backtest-start',default='2024-01-01')
    ap.add_argument('--max-origins',type=int,help='Optional cap per horizon, for a short validation run')
    ap.add_argument('--cost-bps',type=float,default=10.,help='Cost per unit traded, charged at entry/rebalance/exit')
    args = ap.parse_args()
    if 0<args.train_sessions<MIN_HISTORY or args.train_sessions<0:
        ap.error('--train-sessions must be 0 or >=252')
    if args.max_origins is not None and args.max_origins<1:
        ap.error('--max-origins must be positive')
    if args.cost_bps<0:
        ap.error('--cost-bps must be nonnegative')
    args.sets = list(dict.fromkeys(args.sets))
    args.horizons = list(dict.fromkeys(args.horizons))
    return args


if __name__=='__main__':
    run(arguments())
