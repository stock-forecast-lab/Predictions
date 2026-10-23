"""Generate one self-contained, offline-capable dashboard from immutable forecast logs."""
import argparse
import os
from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np
import pandas as pd
from dashboard.data import load_forecasts,attach_actuals,load_benchmarks,load_weights,build_data,BASIS
ROOT = Path(__file__).resolve().parents[1]


def save_quotes(frame,folder):
    import hashlib,uuid
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'quotes_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}.csv'
    frame.to_csv(path,index=False,mode='x')
    meta={'kind':'benchmark_quotes','csv_file':path.name,
          'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'retrieved_at_utc':datetime.now(timezone.utc).isoformat()}
    with path.with_suffix('.json').open('x') as f:
        json.dump(meta,f,ensure_ascii=False,indent=2)


def refresh_benchmarks(root,forecasts):
    """Only actual cash-index levels; no ETF or TOPIX-wide proxy for TOPIX500."""
    if forecasts.empty:
        return []
    import yfinance as yf
    from dashboard.exchange_calendar import completed_schedule
    notes=[]
    sources={'JP':('NIKKEI225','^N225'),'US':('SP500','^GSPC')}
    for market,z in forecasts.groupby('market'):
        if market not in sources:
            continue
        benchmark,symbol=sources[market]
        complete,_=completed_schedule(market,z.origin.min())
        last=complete.index[-1]
        try:
            raw=yf.download(symbol,start=z.origin.min(),end=(last+pd.Timedelta(days=1)).date().isoformat(),
                            auto_adjust=False,repair=False,progress=False,threads=False)
            if raw.empty:
                raise ValueError('empty response')
            close=raw['Close']
            if isinstance(close,pd.DataFrame): close=close.iloc[:,0]
            q=pd.DataFrame({'date':pd.to_datetime(close.index).tz_localize(None).normalize(),'close':close.to_numpy()})
            q=q[q.date.isin(complete.index)].dropna(subset=['close'])
            q['market'],q['benchmark'],q['return_basis'],q['source']=market,benchmark,'price_index',f'Yahoo Finance {symbol}'
            if q.empty:
                raise ValueError('no complete sessions')
            existing=load_benchmarks(root)
            seen=set(existing.loc[(existing.market==market)&(existing.benchmark==benchmark),'date'])
            q=q[~q.date.dt.strftime('%Y-%m-%d').isin(seen)]
            if len(q):
                save_quotes(q,Path(root)/'data/benchmarks/quotes'/market)
        except Exception as exc:
            # Forecast generation remains independent of this supplemental feed.
            notes.append(f'{benchmark}: 指数取得失敗（{type(exc).__name__}）。保存済みの指数データのみ表示。')
    if 'JP' in forecasts.market.values:
        notes.extend(refresh_jquants(root,forecasts[forecasts.market=='JP']))
    return notes


def refresh_jquants(root,forecasts):
    import requests
    from dashboard.exchange_calendar import completed_schedule
    key=os.getenv('JQUANTS_API_KEY')
    if not key:
        return ['TOPIX / TOPIX500: JQUANTS_API_KEYまたは指数CSVが未設定。取得できた指数のみ表示します。']
    complete,_=completed_schedule('JP',forecasts.origin.min())
    notes=[]
    for benchmark,endpoint,code in [('TOPIX','/indices/bars/daily/topix',None),('TOPIX500','/indices/bars/daily','002C')]:
        params={'from':forecasts.origin.min(),'to':complete.index[-1].date().isoformat()}
        if code: params['code']=code
        records=[];seen_pages=set()
        try:
            while True:
                r=requests.get('https://api.jquants.com/v2'+endpoint,headers={'x-api-key':key},params=params,timeout=90)
                if r.status_code!=200: raise RuntimeError(f'HTTP {r.status_code}')
                body=r.json();records.extend(body.get('data',[]))
                token=body.get('pagination_key')
                if not token: break
                if token in seen_pages: raise RuntimeError('Repeated pagination token')
                seen_pages.add(token);params['pagination_key']=token
            if not records: raise RuntimeError('No index observations')
            q=pd.DataFrame(records).rename(columns={'Date':'date','C':'close'})
            q['date']=pd.to_datetime(q.date)
            q=q[q.date.isin(complete.index)].dropna(subset=['close'])
            q['market'],q['benchmark'],q['return_basis'],q['source']='JP',benchmark,'price_index','J-Quants API V2'
            existing=load_benchmarks(root)
            seen=set(existing.loc[(existing.market=='JP')&(existing.benchmark==benchmark),'date'])
            q=q[~q.date.dt.strftime('%Y-%m-%d').isin(seen)]
            if len(q): save_quotes(q[['market','benchmark','date','close','return_basis','source']],Path(root)/'data/benchmarks/quotes/JP')
        except Exception as exc:
            notes.append(f'{benchmark}: J-Quants取得失敗（{type(exc).__name__}）。契約範囲・APIキー・通信を確認してください。')
    return notes


def demo_data():
    """Simulated history for UI review only; never written into live forecast paths."""
    from dashboard.exchange_calendar import schedule
    rng=np.random.default_rng(71)
    rows=[]
    quotes=[]
    for market in ['JP','US']:
        dates=schedule(market,'2025-11-01','2026-05-29').index
        stocks=[f'{7200+i}.T' if market=='JP' else f'DEMO{i:02d}' for i in range(20)]
        daily=rng.normal(.0003,.008,len(dates))
        levels=10000*np.cumprod(1+daily)
        for d,value in zip(dates,levels):
            quotes.append(dict(market=market,benchmark='NIKKEI225' if market=='JP' else 'SP500',date=d.date().isoformat(),
                               close=value,return_basis='price_index',source='SYNTHETIC'))
        for h in [1,5,20]:
            for i in range(65,95):
                origin,target=dates[i],dates[i+h]
                market_actual=levels[i+h]/levels[i]-1
                actuals=market_actual+rng.normal(0,.012*np.sqrt(h),len(stocks))
                for version in ['DEMO V1','DEMO V2']:
                    if version=='DEMO V1' and h!=1: continue
                    for info in (['M1'] if version=='DEMO V1' else ['M1','M2','M3']):
                        for j,model in enumerate(['OLS','Ridge','Lasso','RF','XGB','Ensemble']):
                            pred=.25*market_actual+rng.normal(0,(.006 if version=='DEMO V2' else .009)*np.sqrt(h),len(stocks))
                            for ticker,a,p in zip(stocks,actuals,pred):
                                rows.append(dict(market=market,version=version,spec='demo',information_set=info,horizon=h,
                                    origin=origin.date().isoformat(),target=target.date().isoformat(),model=model,ticker=ticker,
                                    prediction=p,actual=a,return_basis=BASIS))
    return build_data(pd.DataFrame(rows),pd.DataFrame(quotes),synthetic=True)


def write_html(data,path):
    from plotly.offline import get_plotlyjs
    template=(Path(__file__).resolve().parent/'template.html').read_text(encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c')
    page=template.replace('__PLOTLY_JS__',get_plotlyjs()).replace('__DASHBOARD_DATA__',payload)
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(page,encoding='utf-8')
    tmp.replace(path)
    return path


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=ROOT)
    ap.add_argument('--output',type=Path,default=ROOT/'docs/index.html')
    ap.add_argument('--refresh-benchmarks',action='store_true')
    ap.add_argument('--demo',action='store_true')
    args=ap.parse_args()
    if args.demo:
        if args.output.resolve()==(args.root/'docs/index.html').resolve():
            ap.error('Demo must use a separate --output path, e.g. dashboard_demo.html')
        data=demo_data()
    else:
        f=load_forecasts(args.root)
        notes=refresh_benchmarks(args.root,f) if args.refresh_benchmarks else []
        data=build_data(attach_actuals(f,args.root),load_benchmarks(args.root),load_weights(args.root))
        data['notes'].extend(notes)
    path=write_html(data,args.output)
    json_path=args.output.with_suffix('.json') if args.demo else args.root/'dashboard/data.json'
    json_path.parent.mkdir(parents=True,exist_ok=True)
    json_path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')),encoding='utf-8')
    print(f'Dashboard: {path} ({len(data["series"])} series, synthetic={data["synthetic"]})')


if __name__=='__main__':
    main()
