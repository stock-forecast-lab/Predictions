import numpy as np
import pandas as pd
import pytest
from config import COMMON
from context_data import vintage_events, context_features, fetch_vintages
from features import make_panel, training_rows, MARKET_FEATURES
from main import forecast_origin, information_sets
from market_data import completed_schedule
from metrics import summarize
from storage import save_bundle, bundles, evaluate


def synthetic_prices(sessions,count=6,seed=42):
    rng=np.random.default_rng(seed)
    frames=[]
    common=rng.normal(0,.006,len(sessions))
    for i in range(count):
        close=(40+i*10)*np.exp(np.cumsum(common+rng.normal(.0002,.01,len(sessions))))
        frames.append(pd.DataFrame(dict(date=sessions,ticker=f'T{i:03d}',Open=close*.999,
            Close=close,High=close*1.01,Low=close*.99,Volume=rng.integers(10000,500000,len(sessions)))))
    return pd.concat(frames,ignore_index=True)


def vintage(period,released,value):
    return dict(date=period,realtime_start=released,realtime_end='9999-12-31',value=str(value))


def test_revision_replay_and_same_vintage_growth():
    rows=[vintage('2022-01-01','2022-02-01',100),vintage('2023-01-01','2023-02-01',110),
          vintage('2022-01-01','2023-03-01',105)]
    z=vintage_events(rows,growth=True)
    assert z.available_at.iloc[1]==pd.Timestamp('2023-02-03',tz='UTC')
    assert z.value.iloc[1]==pytest.approx(.10)
    assert z.value.iloc[2]==pytest.approx(110/105-1)
    assert z.observation_date.iloc[2]==pd.Timestamp('2023-01-01')
    pd.testing.assert_frame_equal(vintage_events(rows[:2],growth=True),z.iloc[:2].reset_index(drop=True))


def test_macro_future_release_unavailable_and_no_backfill():
    dates=pd.bdate_range('2023-02-01','2023-02-10')
    cutoffs=pd.Series(pd.to_datetime(dates,utc=True)+pd.Timedelta(hours=21),index=dates)
    supplied={s:[vintage('2023-01-31','2023-01-31',100),vintage('2023-02-03','2023-02-03',150)] for s in COMMON.values()}
    out,*_=context_features(cutoffs,'US',['M2'],'2020-01-01','2023-02-10','unused',supplied)
    out=out.set_index('date')
    assert pd.isna(out.loc['2023-02-01','fx'])
    assert out.loc['2023-02-03','fx']==100
    assert out.loc['2023-02-06','fx']==150


def test_old_revision_does_not_become_latest():
    z=vintage_events([vintage('2023-01-01','2023-02-01',1),vintage('2023-02-01','2023-03-01',2),
                      vintage('2023-01-01','2023-04-01',9)])
    assert z.value.iloc[-1]==2


def test_calendar_horizons_and_no_cross_ticker_shift():
    dates=pd.bdate_range('2023-01-02',periods=100)
    px=synthetic_prices(dates,2)
    px=px[~((px.ticker=='T000')&(px.date==dates[90]))]
    z=make_panel(px,dates).set_index(['ticker','date'])
    assert pd.isna(z.loc[('T000',dates[89]),'target_1'])
    assert z.loc[('T000',dates[89]),'label_end_1']==dates[90]
    assert pd.isna(z.loc[('T000',dates[-1]),'target_1'])
    q=px[px.ticker=='T001'].set_index('date')
    assert z.loc[('T001',dates[70]),'target_20']==pytest.approx(q.loc[dates[90],'Close']/q.loc[dates[70],'Close']-1)


def test_label_purge_and_future_feature_invariance():
    dates=pd.bdate_range('2020-01-01',periods=420)
    px=synthetic_prices(dates)
    z=make_panel(px,dates)
    cutoff=dates[360]
    tr=training_rows(z,cutoff,20,252,MARKET_FEATURES)
    assert (tr.label_end_20<cutoff).all()
    px.loc[px.date>cutoff,'Close']*=10
    z2=make_panel(px,dates)
    pd.testing.assert_frame_equal(z.loc[z.date<=cutoff,MARKET_FEATURES],z2.loc[z2.date<=cutoff,MARKET_FEATURES])
    np.testing.assert_allclose(tr.target_20,training_rows(z2,cutoff,20,252,MARKET_FEATURES).target_20)


def test_future_prices_cannot_change_any_model_forecast():
    dates=pd.bdate_range('2020-01-01',periods=420)
    px=synthetic_prices(dates)
    cutoff=dates[380]
    cols=information_sets([],[])
    before=forecast_origin(make_panel(px,dates),cutoff,[1,5,20],['M1'],cols,None,quick=True)
    px.loc[px.date>cutoff,'Close']*=4
    after=forecast_origin(make_panel(px,dates),cutoff,[1,5,20],['M1'],cols,None,quick=True)
    assert set(before.model)=={'HistoricalMean','OLS','Ridge','Lasso','ElasticNet','RF','XGB','Ensemble'}
    np.testing.assert_allclose(before.prediction,after.prediction,rtol=1e-12,atol=1e-14)
    assert (before.train_last_label_end<cutoff).all()


def test_partial_sessions_are_excluded():
    complete,_=completed_schedule('US','2024-07-01',now='2024-07-05T15:00:00Z')
    assert complete.index[-1]==pd.Timestamp('2024-07-03')
    complete,_=completed_schedule('JP','2024-07-01',now='2024-07-05T03:00:00Z')
    assert complete.index[-1]==pd.Timestamp('2024-07-04')


def test_evaluation_exact_session_adjusted_rebase_idempotence(tmp_path):
    f=pd.DataFrame([dict(ticker='A',horizon=5,information_set='M2',model='Ridge',model_version='v2.0.0',
        data_last_observation_date='2024-01-02',forecast_for_session_date='2024-01-09',close_at_forecast=100,prediction=.1)])
    p=save_bundle(f,tmp_path/'predictions/v2/US','forecast',{})
    original=p.read_bytes()
    px=pd.DataFrame(dict(ticker=['A','A'],date=pd.to_datetime(['2024-01-02','2024-01-10']),Close=[50.,60.]))
    assert evaluate(px,'US',tmp_path) is None
    px.loc[1,['date','Close']]=[pd.Timestamp('2024-01-09'),55.]
    out=evaluate(px,'US',tmp_path)
    assert pd.read_csv(out).actual.iloc[0]==pytest.approx(.1)
    assert evaluate(px,'US',tmp_path) is None
    assert p.read_bytes()==original
    p.write_bytes(original+b'\n')
    with pytest.raises(RuntimeError,match='changed'):
        list(bundles(tmp_path/'predictions/v2/US'))


def test_metrics_baseline_ranking_and_costs():
    z=pd.DataFrame(dict(date=np.repeat(pd.bdate_range('2024-01-01',periods=15),10),
        ticker=list('ABCDEFGHIJ')*15,prediction=list(np.linspace(-.05,.05,10))*15))
    z['actual']=z.prediction*.5
    z['market'],z['horizon'],z['information_set'],z['model'],z['historical_mean']='US',1,'M1','Ridge',0.
    metrics,portfolio=summarize(z,cost_bps=10)
    assert metrics.RankIC.iloc[0]==pytest.approx(1)
    assert metrics.OOS_R2.iloc[0]==pytest.approx(0)
    assert portfolio.net_return.sum()<portfolio.gross_return.sum()
    assert portfolio.turnover.iloc[0]>=1
    assert portfolio.turnover.iloc[-1]>.9
    assert metrics.MaxDrawdown.iloc[0]<=0


def test_fred_errors_never_expose_key(monkeypatch,tmp_path):
    import context_data
    monkeypatch.setenv('FRED_API_KEY','TOP_SECRET')
    class Bad: status_code=403
    monkeypatch.setattr(context_data.requests,'get',lambda *a,**k:Bad())
    with pytest.raises(RuntimeError) as e:
        fetch_vintages('DGS2','2020-01-01','2024-01-01',tmp_path)
    assert 'TOP_SECRET' not in str(e.value)


def test_missing_api_key_actionable(monkeypatch,tmp_path):
    monkeypatch.delenv('FRED_API_KEY',raising=False)
    with pytest.raises(RuntimeError,match='FRED_API_KEY'):
        fetch_vintages('DGS2','2020-01-01','2024-01-01',tmp_path)


def test_jp_ip_growth_is_not_differenced_twice():
    from demo import demo_inputs
    panel,columns,dates=demo_inputs('JP')
    assert 'jp_ip_yoy' in columns['M3']
    assert 'us_cpi_yoy' in columns['M3']
    value=panel.loc[panel.date==dates[-1],'jp_ip_yoy'].iloc[0]
    assert .01<value<.03


@pytest.mark.parametrize('market',['US','JP'])
def test_live_and_backtest_entrypoints_preserve_history(monkeypatch,tmp_path,market):
    import main
    from types import SimpleNamespace
    from market_data import schedule
    calendar=schedule(market,'2020-01-01','2022-06-30')
    complete=calendar.iloc[:470]
    px=synthetic_prices(complete.index,6)
    ctx=pd.DataFrame(dict(date=complete.index,fx_signal=np.linspace(0,.01,len(complete)),
                         macro_signal=np.sin(np.arange(len(complete))/100)))
    monkeypatch.setenv('FRED_API_KEY','MOCK_ONLY')
    monkeypatch.setattr(main,'ROOT',tmp_path)
    monkeypatch.setattr(main,'completed_schedule',lambda *a:(complete,calendar))
    monkeypatch.setattr(main,'universe',lambda *a:(px.ticker.unique().tolist(),{'name':'SYNTHETIC','count':6}))
    monkeypatch.setattr(main,'prices',lambda *a:px)
    monkeypatch.setattr(main,'context_features',lambda *a:(ctx,['fx_signal'],['macro_signal'],{}))
    args=SimpleNamespace(market=market,sets=['M1','M2','M3'],horizons=[1,5,20],models=['HistoricalMean','Ridge'],
        train_sessions=756,backtest=False,backtest_start=complete.index[-41].date().isoformat(),
        max_origins=2,cost_bps=10.)
    main.run(args)
    folder=tmp_path/'predictions/v2'/market
    files=list(folder.glob('*.csv'))
    assert len(files)==1
    original=files[0].read_bytes()
    z=pd.read_csv(files[0])
    assert len(z)==6*3*3*3
    assert z.forecast_for_session_date.notna().all()
    assert (pd.to_datetime(z.train_last_label_end)<complete.index[-1]).all()
    main.run(args)
    assert len(list(folder.glob('*.csv')))==1
    assert files[0].read_bytes()==original
    args.backtest=True
    main.run(args)
    assert len(list((tmp_path/'output/v2/backtests'/market).glob('metrics*.csv')))==1
    assert files[0].read_bytes()==original


def test_stale_macro_stops_instead_of_silent_filling():
    dates=pd.bdate_range('2024-01-01','2024-01-05')
    cutoffs=pd.Series(pd.to_datetime(dates,utc=True),index=dates)
    supplied={s:[vintage('2020-01-01','2020-01-02',100)] for s in COMMON.values()}
    with pytest.raises(RuntimeError,match='stale'):
        context_features(cutoffs,'US',['M2'],'2013-01-01','2024-01-05','unused',supplied)


def test_flat_forecast_has_no_arbitrary_long_short_trades():
    z=pd.DataFrame(dict(date=[pd.Timestamp('2024-01-01')]*10,ticker=list('ABCDEFGHIJ'),
                        prediction=[.01]*10,actual=np.linspace(-.1,.1,10),
                        historical_mean=[.01]*10,market=['US']*10,horizon=[1]*10,
                        information_set=['M1']*10,model=['HistoricalMean']*10))
    metrics,port=summarize(z)
    assert port.turnover.iloc[0]==0
    assert port.net_return.iloc[0]==0


def test_legacy_v2_evaluation_remains_a_done_key_after_reorganization(tmp_path):
    f=pd.DataFrame([dict(ticker='A',horizon=1,information_set='M1',model='Ridge',model_version='v2.0.0',
        data_last_observation_date='2024-01-02',forecast_for_session_date='2024-01-03',prediction=.01)])
    save_bundle(f,tmp_path/'predictions/v2/US','forecast',{})
    px=pd.DataFrame(dict(ticker=['A','A'],date=pd.to_datetime(['2024-01-02','2024-01-03']),Close=[100.,101.]))
    saved=evaluate(px,'US',tmp_path)
    assert saved is not None
    legacy=tmp_path/'output/v2/evaluations/US';legacy.mkdir(parents=True)
    for path in [saved,saved.with_suffix('.json')]: path.rename(legacy/path.name)
    assert evaluate(px,'US',tmp_path) is None
