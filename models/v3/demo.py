"""Offline synthetic V3 integration demo; never writes live predictions/."""
import argparse
import numpy as np
import pandas as pd
from . import bridge
from .financial_features import attach_financials
from .main import forecast_origin
from .config import ROOT
from .storage import save_bundle


def synthetic_inputs(market):
    calendar=bridge.market.schedule(market,'2020-01-01','2023-04-30')
    dates=calendar.index;rng=np.random.default_rng(42);px=[];facts=[]
    for i in range(12):
        ticker=f'SYNTHETIC{i:02d}'
        close=(20+i)*np.exp(np.cumsum(rng.normal(.0002,.01,len(dates))))
        px.append(pd.DataFrame(dict(date=dates,ticker=ticker,Close=close,RawClose=close,
            Open=close,High=close*1.01,Low=close*.99,Volume=rng.integers(10000,100000,len(dates)),Split=0.)))
        for year in range(2018,2023):
            for metric,value,start in [('assets',1000+i*10,''),('equity',600+i*10,''),
                ('income',100+i,f'{year}-01-01'),('revenue',500+i*10+year,f'{year}-01-01'),
                ('cfo',120+i,f'{year}-01-01'),('shares',100,'')]:
                available=f'{year+1}-02-02T00:00:00+00:00'
                facts.append(dict(ticker=ticker,metric=metric,start=start,end=f'{year}-12-31',value=float(value),
                    unit='shares' if metric=='shares' else 'JPY' if market=='JP' else 'USD',scope='consolidated',
                    tag='SYNTHETIC',priority=0,filing_id=f'SYNTHETIC-{ticker}-{year}',provider='SYNTHETIC',
                    published_at_utc=available,available_at_utc=available,
                    availability_basis='SYNTHETIC',valuation_allowed=True))
    panel=bridge.features.make_panel(pd.concat(px,ignore_index=True),dates)
    return attach_financials(panel,pd.DataFrame(facts),calendar.market_close),dates


def run(market):
    panel,dates=synthetic_inputs(market);cutoff=dates[-21]
    result=forecast_origin(panel,cutoff,[1,5,20],bridge.features.MARKET_FEATURES,
                           quick=True,profile='light',base_set='M1')
    assert len(result)==12*3*2*8 and np.isfinite(result.prediction).all()
    result['market']=market;result['synthetic_data']=True
    result['forecast_for_session_date']=result.horizon.map({h:dates[dates.get_loc(cutoff)+h].date().isoformat() for h in [1,5,20]})
    path=save_bundle(result,ROOT/'output/demo/v3',f'SYNTHETIC_{market}',
        {'synthetic_data':True,'purpose':'offline connection check, not predictive accuracy evidence','quick':True})
    print(f'SYNTHETIC V3 {market}: {len(result)} rows, CONTROL/FINANCIAL x 3 horizons x 8 outputs x 12 stocks: {path}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market',choices=['JP','US','BOTH'],default='BOTH')
    args=parser.parse_args()
    for market in ['JP','US'] if args.market=='BOTH' else [args.market]:run(market)
