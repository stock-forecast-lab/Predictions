from pathlib import Path
import argparse, hashlib, json, os, uuid, warnings
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression, Ridge, Lasso
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error
try:
    from xgboost import XGBRegressor
except Exception:
    XGBRegressor = None
from config import *

FEATURES = ['ret1','ret5','ret20','ret60','mom20','mom60','vol20','vol60','vol_z20','hl20','ma20_gap','ma60_gap','mkt_ret1','rel_ret20']


def us_tickers():
    """Retrieve the S&P 500 universe from a CSV hosted on GitHub.

    Wikipedia rejects automated HTML scraping with HTTP 403 on some runners.
    Dataset source: https://github.com/datasets/s-and-p-500-companies
    Do not silently substitute a smaller sample if retrieval fails.
    """
    url = ('https://raw.githubusercontent.com/datasets/'
           's-and-p-500-companies/main/data/constituents.csv')
    try:
        constituents = pd.read_csv(url)
    except Exception as exc:
        raise RuntimeError(
            'Could not retrieve the S&P 500 universe CSV from GitHub. '
            'Please retry later and check network access to raw.githubusercontent.com.'
        ) from exc
    if 'Symbol' not in constituents.columns:
        raise RuntimeError('S&P 500 constituent CSV does not contain a Symbol column.')
    tickers = sorted(set(constituents['Symbol'].dropna().astype(str)
                         .str.strip().str.replace('.', '-', regex=False)))
    if not 450 <= len(tickers) <= 550:
        raise RuntimeError(f'Unexpected S&P 500 constituent count: {len(tickers)}')
    print(f'Loaded {len(tickers)} S&P 500 tickers from the GitHub CSV source')
    return tickers


def jp_tickers():
    # Use the verified 500-name JPX official membership snapshot; the previous
    # five-ticker sample data/jp_tickers.csv is intentionally NOT used.
    from topix500_universe import get_tickers
    tickers, snapshot = get_tickers()
    print(f'Loaded {len(tickers)} official TOPIX 500 tickers from {snapshot}')
    return tickers


def download_prices(tickers, start=START):
    frames=[]
    for i in range(0,len(tickers),100):
        batch=tickers[i:i+100]
        d=yf.download(batch,start=start,auto_adjust=True,repair=True,group_by='ticker',threads=True,progress=False)
        if len(batch)==1 and not isinstance(d.columns,pd.MultiIndex):
            d.columns=pd.MultiIndex.from_product([batch,d.columns])
        for tic in batch:
            try:
                x=d[tic][['Open','High','Low','Close','Volume']].dropna(how='all').copy()
                x['ticker']=tic; x['date']=pd.to_datetime(x.index).tz_localize(None)
                frames.append(x.reset_index(drop=True))
            except Exception:
                pass
    if not frames:
        raise RuntimeError('No market data downloaded')
    return pd.concat(frames,ignore_index=True)


def features(px):
    px=px.sort_values(['ticker','date']).copy()
    g=px.groupby('ticker',group_keys=False)
    px['ret1']=g['Close'].pct_change()
    for n in [5,20,60]:
        px[f'ret{n}']=g['Close'].pct_change(n)
    px['mom20']=px['ret20']; px['mom60']=px['ret60']
    px['vol20']=g['ret1'].rolling(20).std().reset_index(level=0,drop=True)
    px['vol60']=g['ret1'].rolling(60).std().reset_index(level=0,drop=True)
    lv=np.log1p(px['Volume'])
    m20=lv.groupby(px['ticker']).rolling(20).mean().reset_index(level=0,drop=True)
    s20=lv.groupby(px['ticker']).rolling(20).std().reset_index(level=0,drop=True)
    px['vol_z20']=(lv-m20)/s20
    px['hl20']=((px['High']-px['Low'])/px['Close']).groupby(px['ticker']).rolling(20).mean().reset_index(level=0,drop=True)
    for n in [20,60]:
        ma=g['Close'].rolling(n).mean().reset_index(level=0,drop=True)
        px[f'ma{n}_gap']=px['Close']/ma-1
    mkt=px.groupby('date')['ret1'].mean().rename('mkt_ret1')
    px=px.join(mkt,on='date')
    m20m=(1+mkt).rolling(20).apply(np.prod,raw=True)-1
    px=px.join(m20m.rename('mkt20'),on='date')
    px['rel_ret20']=px['ret20']-px['mkt20']
    # Shift within ticker only. This avoids leaking the first return of the next ticker
    # into the final observation of the previous ticker.
    px['target']=px.groupby('ticker')['ret1'].shift(-1)
    return px


def models():
    scale=lambda m: Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('m',m)])
    out={'OLS':scale(LinearRegression()),'Ridge':scale(Ridge(alpha=10.0)),'Lasso':scale(Lasso(alpha=1e-4,max_iter=5000)),
         'RF':Pipeline([('imp',SimpleImputer(strategy='median')),('m',RandomForestRegressor(n_estimators=250,min_samples_leaf=20,n_jobs=-1,random_state=RANDOM_STATE,max_features='sqrt'))])}
    if XGBRegressor:
        out['XGB']=Pipeline([('imp',SimpleImputer(strategy='median')),('m',XGBRegressor(n_estimators=300,max_depth=4,learning_rate=.03,subsample=.8,colsample_bytree=.8,objective='reg:squarederror',n_jobs=-1,random_state=RANDOM_STATE))])
    return out


def next_session_date(market, last_date):
    cal=mcal.get_calendar('NYSE' if market=='US' else 'JPX')
    start=pd.Timestamp(last_date)+pd.Timedelta(days=1)
    valid=cal.valid_days(start_date=start, end_date=start+pd.Timedelta(days=14))
    if len(valid)==0:
        raise RuntimeError('Could not determine next exchange session')
    return pd.Timestamp(valid[0]).tz_localize(None).date().isoformat()


def unique_path(folder, stem, suffix):
    folder.mkdir(parents=True,exist_ok=True)
    run_id=os.getenv('GITHUB_RUN_ID','local')
    run_attempt=os.getenv('GITHUB_RUN_ATTEMPT','1')
    token=f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{run_id}_{run_attempt}_{uuid.uuid4().hex[:8]}'
    return folder/f'{stem}_{token}{suffix}'


def exclusive_csv(df, path):
    # mode='x' is an OS-level create-only guard: an existing file is never replaced.
    df.to_csv(path,index=False,mode='x')


def already_forecast(market, observation_date):
    """Detect an existing forecast without changing any prior forecast file."""
    pred_dir=PRED/market
    if not pred_dir.exists():
        return False
    for manifest in pred_dir.glob('*.json'):
        try:
            with open(manifest, encoding='utf-8') as fh:
                obj=json.load(fh)
            if (obj.get('model_version') == MODEL_VERSION
                and obj.get('data_last_observation_date') == observation_date):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def live_forecast(df, market):
    usable=df.dropna(subset=['target']).copy()
    last=pd.Timestamp(df['date'].max())
    today=df[df.date==last].copy()
    if market == 'JP' and len(today) < 475:
        raise RuntimeError(
            f'TOPIX500 data coverage too low: {len(today)}/500 stocks '
            f'on {last.date()}. No misleading incomplete forecast will be saved.'
        )
    train=usable[usable.date<last]
    if train.date.nunique()<MIN_HISTORY:
        raise RuntimeError('Insufficient history')
    X=train[FEATURES]; y=train.target; Xt=today[FEATURES]
    preds={}
    for name,m in models().items():
        m.fit(X,y); preds[name]=m.predict(Xt)
    res=today[['date','ticker','Close']].copy()
    for k,v in preds.items():
        res[k]=v
    res['Ensemble']=res[list(preds)].mean(axis=1)
    created=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    target_date=next_session_date(market,last)
    res=res.rename(columns={'date':'data_last_observation_date','Close':'close_at_forecast'})
    res.insert(0,'market',market)
    res.insert(1,'model_version',MODEL_VERSION)
    res.insert(2,'forecast_created_at_utc',created)
    res.insert(3,'forecast_for_session_date',target_date)
    if market == 'JP':
        from topix500_universe import snapshot_path
        snapshot = snapshot_path(last.date())
        universe_ref = str(snapshot.relative_to(ROOT))
        snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        res['universe_name'] = 'TOPIX500'
        res['universe_snapshot'] = universe_ref
        res['universe_snapshot_sha256'] = snapshot_hash
        res['universe_expected_count'] = 500
    else:
        universe_ref = 'GitHub datasets S&P500 constituents (live)'
        snapshot_hash = ''
        res['universe_name'] = 'SP500'
        res['universe_snapshot'] = universe_ref
        res['universe_snapshot_sha256'] = snapshot_hash
        res['universe_expected_count'] = len(res)

    res['data_last_observation_date']=pd.to_datetime(res['data_last_observation_date']).dt.date.astype(str)
    res=res.sort_values('Ensemble',ascending=False)
    folder=PRED/market
    path=unique_path(folder,f'{last:%Y-%m-%d}_for_{target_date}_{MODEL_VERSION}','.csv')
    exclusive_csv(res,path)
    manifest={
        'market':market,'model_version':MODEL_VERSION,'forecast_created_at_utc':created,
        'data_last_observation_date':last.date().isoformat(),'forecast_for_session_date':target_date,
        'forecast_file':str(path.relative_to(ROOT)),'rows':int(len(res)),
        'github_run_id':os.getenv('GITHUB_RUN_ID'),'github_sha':os.getenv('GITHUB_SHA'),
        'universe_name':'TOPIX500' if market=='JP' else 'SP500',
        'universe_expected_count':500 if market=='JP' else int(len(res)),
        'universe_observed_count':int(len(res)),
        'universe_snapshot':universe_ref,
        'universe_snapshot_sha256':snapshot_hash
    }
    mpath=path.with_suffix('.json')
    with open(mpath,'x',encoding='utf-8') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
    return res,path


def evaluated_keys(market):
    keys=set()
    for p in (EVAL/market).glob('*.csv') if (EVAL/market).exists() else []:
        try:
            z=pd.read_csv(p,usecols=['forecast_file','ticker'])
            keys.update(zip(z['forecast_file'].astype(str),z['ticker'].astype(str)))
        except Exception:
            continue
    return keys


def evaluate_previous_forecasts(px, market):
    """Compare immutable prior forecasts with the first realized close after their data cutoff.
    Old forecast files are read-only; evaluation is written as a new immutable file.
    """
    pred_dir=PRED/market
    if not pred_dir.exists():
        return None,None
    done=evaluated_keys(market)
    rows=[]
    px=px.sort_values(['ticker','date']).copy()
    px['date']=pd.to_datetime(px['date']).dt.tz_localize(None)
    for fp in sorted(pred_dir.glob('*.csv')):
        rel=str(fp.relative_to(ROOT))
        try:
            f=pd.read_csv(fp)
        except Exception:
            continue
        required={'ticker','data_last_observation_date','close_at_forecast','Ensemble','model_version'}
        if not required.issubset(f.columns):
            continue
        for _,r in f.iterrows():
            key=(rel,str(r['ticker']))
            if key in done:
                continue
            cutoff=pd.Timestamp(r['data_last_observation_date'])
            target=r.get('forecast_for_session_date')
            if isinstance(target, str) and target:
                # Score ONLY the session that was actually forecast. Missing
                # quote/suspended tickers remain pending rather than comparing
                # against a different trading session.
                q=px[(px.ticker==r['ticker']) & (px.date==pd.Timestamp(target))]
            else:
                # Compatibility with legacy forecasts without target date.
                q=px[(px.ticker==r['ticker']) & (px.date>cutoff)]
            if q.empty:
                continue
            realized=q.iloc[0]
            actual=float(realized.Close/float(r['close_at_forecast'])-1.0)
            rows.append({
                'market':market,'forecast_file':rel,'ticker':r['ticker'],
                'model_version':r['model_version'],
                'forecast_created_at_utc':r.get('forecast_created_at_utc',''),
                'data_last_observation_date':cutoff.date().isoformat(),
                'forecast_for_session_date':r.get('forecast_for_session_date',''),
                'realized_session_date':realized.date.date().isoformat(),
                'close_at_forecast':float(r['close_at_forecast']),
                'realized_close':float(realized.Close),'predicted_return':float(r['Ensemble']),
                'actual_return':actual,'error':float(r['Ensemble'])-actual,
                'direction_correct':int(np.sign(float(r['Ensemble']))==np.sign(actual)),
                'evaluated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            })
    if not rows:
        return None,None
    out=pd.DataFrame(rows)
    path=unique_path(EVAL/market,f'evaluation_{market}_{MODEL_VERSION}','.csv')
    exclusive_csv(out,path)
    return out,path


def pseudo_oos(df, market, start_test='2024-01-01', rebalance='W-FRI'):
    dates=pd.Series(sorted(df.loc[df.date>=pd.Timestamp(start_test),'date'].unique()))
    test_dates=pd.DataFrame({'date':pd.to_datetime(dates)}).set_index('date').resample(rebalance).last().dropna()['date'].tolist()
    rows=[]
    for dt in test_dates:
        tr=df[(df.date<dt)&df.target.notna()]; te=df[df.date==dt]
        if tr.date.nunique()<MIN_HISTORY or te.empty:
            continue
        for name,m in models().items():
            m.fit(tr[FEATURES],tr.target); p=m.predict(te[FEATURES])
            for tic,actual,pred in zip(te.ticker,te.target,p):
                rows.append((dt,tic,name,actual,pred))
    r=pd.DataFrame(rows,columns=['date','ticker','model','actual','pred']).dropna()
    if r.empty:
        return r,None
    met=r.groupby('model').apply(lambda z: pd.Series({'RMSE':mean_squared_error(z.actual,z.pred)**.5,'MAE':mean_absolute_error(z.actual,z.pred),'DirectionAcc':np.mean(np.sign(z.actual)==np.sign(z.pred))}),include_groups=False)
    # Backtest outputs are replaceable research artifacts; live forecast/evaluation logs are immutable.
    met.to_csv(OUT/f'{market}_pseudo_oos_metrics.csv')
    r.to_parquet(OUT/f'{market}_pseudo_oos_predictions.parquet',index=False)
    return r,met


def run(market, backtest=False):
    tics=us_tickers() if market=='US' else jp_tickers()
    px=download_prices(tics)
    if os.getenv('GITHUB_ACTIONS') != 'true':
        px.to_parquet(RAW/f'{market}_prices.parquet',index=False)
    ev,evpath=evaluate_previous_forecasts(px,market)
    if evpath is not None:
        print(f'Previous forecasts evaluated: {evpath} ({len(ev)} ticker forecasts)')
    # Make a genuine new forecast only when the data cutoff or model version
    # changes. This prevents duplicate 'live' forecasts on non-trading days
    # and on repeated manual workflow runs.
    last_data_day = pd.Timestamp(px['date'].max()).date().isoformat()
    if already_forecast(market, last_data_day):
        print(f'Already forecast with model {MODEL_VERSION} and observation '
              f'date {last_data_day}; no duplicate forecast generated.')
        return
    df=features(px)
    if os.getenv('GITHUB_ACTIONS') != 'true':
        df.to_parquet(RAW/f'{market}_features.parquet',index=False)
    res,path=live_forecast(df,market)
    print(f'Forecast saved immutably: {path}')
    print(res[['ticker','Ensemble']].head(TOP_N).to_string(index=False))
    if backtest:
        _,met=pseudo_oos(df,market)
        if met is not None:
            print('\nPseudo-OOS metrics\n',met)


if __name__=='__main__':
    warnings.filterwarnings('ignore')
    ap=argparse.ArgumentParser()
    ap.add_argument('--market',choices=['US','JP'],required=True)
    ap.add_argument('--backtest',action='store_true')
    a=ap.parse_args()
    run(a.market,a.backtest)
