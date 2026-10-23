"""Offline integration demonstration. ALL observations and predictions are synthetic."""
import argparse
import numpy as np
import pandas as pd
from config import ROOT, COMMON, MACRO, YOY_FROM_LEVEL, PERCENT_TO_FRACTION
from market_data import schedule
from context_data import context_features
from features import make_panel
from main import forecast_origin, information_sets
from storage import save_bundle


def demo_inputs(market):
    cal=schedule(market,'2020-01-01','2022-03-31')
    dates=cal.index
    rng=np.random.default_rng(42)
    frames=[]
    shared=rng.normal(0,.006,len(dates))
    for i in range(12):
        close=(40+10*i)*np.exp(np.cumsum(shared+rng.normal(.0002,.01,len(dates))))
        frames.append(pd.DataFrame(dict(date=dates,ticker=f'DEMO{i:02d}',Open=close*.999,
            Close=close,High=close*1.01,Low=close*.99,Volume=rng.integers(10000,500000,len(dates)))))
    supplied={}
    for name,series in dict(COMMON,**MACRO[market]).items():
        daily=name in COMMON
        periods=pd.bdate_range('2017-01-01',dates[-1]) if daily else pd.date_range('2017-01-01',dates[-1],freq='MS')
        values=100*np.exp(np.arange(len(periods))*.002) if series in YOY_FROM_LEVEL else 2+rng.normal(0,.1,len(periods))
        if name=='fx': values=110*np.exp(np.cumsum(rng.normal(0,.002,len(periods))))
        if name=='vix': values=20+rng.normal(0,2,len(periods))
        supplied[series]=[dict(date=d.date().isoformat(),realtime_start=(d+pd.Timedelta(days=0 if daily else 40)).date().isoformat(),
            realtime_end='9999-12-31',value=str(v)) for d,v in zip(periods,values)]
    ctx,m2,m3,evidence=context_features(cal.market_close,market,['M1','M2','M3'],
        '2017-01-01','2022-03-31','unused',supplied)
    panel=make_panel(pd.concat(frames,ignore_index=True),dates).merge(ctx,on='date',validate='many_to_one')
    return panel,information_sets(m2,m3),dates


def run(market):
    panel,columns,dates=demo_inputs(market)
    cutoff=dates[-21]
    z=forecast_origin(panel,cutoff,[1,5,20],['M1','M2','M3'],columns,None,quick=True)
    z['market'],z['synthetic_data']=market,True
    z['forecast_for_session_date']=z.horizon.map({h:dates[dates.get_loc(cutoff)+h] for h in [1,5,20]})
    assert z.ticker.nunique()==12
    assert len(z)==12*3*3*8
    assert np.isfinite(z.prediction).all()
    assert (z.train_last_label_end<cutoff).all()
    path=save_bundle(z,ROOT/'output/demo',f'SYNTHETIC_{market}',{
        'synthetic_data':True,'market':market,'tree_estimators':10,
        'purpose':'offline integration check only; not evidence of predictive accuracy'})
    print(f'{market}: M1/M2/M3 x 1/5/20 sessions x 8 outputs x 12 synthetic stocks = {len(z)} rows: {path}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market',choices=['BOTH','US','JP'],default='BOTH')
    args=parser.parse_args()
    for m in ['US','JP'] if args.market=='BOTH' else [args.market]:
        run(m)
