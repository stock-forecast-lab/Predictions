import hashlib
import json
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.pipeline import Pipeline
import multihorizon as v1
from dashboard.data import load_forecasts, attach_actuals, build_data
from dashboard.normalize import run as normalize
from dashboard.exchange_calendar import schedule


@pytest.fixture
def market_data(tmp_path, monkeypatch):
    monkeypatch.setattr(v1, 'ROOT', tmp_path)
    monkeypatch.setattr(v1, 'PRED', tmp_path/'predictions/v1')
    monkeypatch.setattr(v1, 'EVAL', tmp_path/'evaluations/v1')
    monkeypatch.setattr(v1, 'MIN_HISTORY', 10)
    monkeypatch.setattr(v1.legacy, 'models', lambda: {'OLS': Pipeline([('m', DummyRegressor())])})
    sessions = schedule('US', '2024-01-02', '2024-08-30').index
    past = sessions[sessions <= '2024-06-28']
    rows = []
    for ticker, scale in [('AAA',1), ('BBB',2), ('CCC',3)]:
        for i, date in enumerate(past):
            rows.append(dict(ticker=ticker,date=date,Close=scale*(100+i),
                             **{feature: float(i % 7) for feature in v1.FEATURES}))
    return pd.DataFrame(rows), sessions


def test_exact_horizon_cumulative_returns_and_missing_quotes(market_data):
    frame, sessions = market_data
    origin = pd.Timestamp('2024-05-20')
    for h in [1,5,20]:
        result = v1.add_target(frame, sessions, h).set_index(['ticker','date'])
        end = v1.target_session(sessions, origin, h)
        prices = frame.set_index(['ticker','date']).Close
        expected = prices.loc[('AAA',end)]/prices.loc[('AAA',origin)]-1
        assert result.loc[('AAA',origin),'target'] == pytest.approx(expected)
        assert result.loc[('AAA',origin),'label_end_date'] == end
    end = v1.target_session(sessions, origin, 5)
    missing = frame[~((frame.ticker=='AAA') & (frame.date==end))]
    result = v1.add_target(missing,sessions,5).set_index(['ticker','date'])
    assert pd.isna(result.loc[('AAA',origin),'target'])


def test_holidays_and_weekends_are_not_counted():
    sessions = schedule('US','2024-07-01','2024-08-15').index
    assert v1.target_session(sessions,'2024-07-03',1) == pd.Timestamp('2024-07-05')
    assert v1.target_session(sessions,'2024-07-03',5) == pd.Timestamp('2024-07-11')


def test_training_excludes_labels_not_yet_realized_even_with_future_input(market_data):
    frame, sessions = market_data
    cutoff = pd.Timestamp('2024-05-31')
    labeled = v1.add_target(frame,sessions,20)
    train = v1.training_rows(labeled,cutoff)
    assert (train.label_end_date<=cutoff).all()
    assert (train.date<cutoff).all()
    assert train.date.max() == sessions[sessions.get_loc(cutoff)-20]
    altered = frame.copy()
    altered.loc[altered.date>cutoff,'Close'] *= 10
    pd.testing.assert_frame_equal(train,v1.training_rows(v1.add_target(altered,sessions,20),cutoff))


def test_three_horizons_save_independently_resume_and_reach_dashboard(market_data):
    frame, sessions = market_data
    paths = []
    for h in [1,5,20]:
        result, path = v1.live_forecast(frame,'US',sessions,h,{})
        paths.append(path)
        assert set(result.horizon_sessions)=={h}
        meta=json.loads(path.with_suffix('.json').read_text())
        assert meta['latest_training_label_end'] <= '2024-06-28'
        assert meta['csv_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(set(paths))==3
    # Short and long targets fit different actual cumulative-return labels.
    assert pd.read_csv(paths[0]).OLS.iloc[0] < pd.read_csv(paths[2]).OLS.iloc[0]
    original={p:p.read_bytes() for p in paths}
    assert v1.live_forecast(frame,'US',sessions,5,{})==(None,None)
    assert all(p.read_bytes()==content for p,content in original.items())
    root=v1.ROOT
    f=load_forecasts(root)
    assert set(f.horizon)=={1,5,20}
    data=build_data(attach_actuals(f,root))
    assert {s['horizon'] for s in data['series']}=={1,5,20}
    normalize(root)
    records=pd.concat(pd.read_csv(p) for p in (root/'records/predictions').glob('*.csv'))
    assert set(records.horizon_sessions)=={1,5,20}
    paths[1].write_bytes(original[paths[1]]+b'\n')
    with pytest.raises(ValueError,match='changed forecast'):
        v1.live_forecast(frame,'US',sessions,5,{})


def test_evaluation_waits_for_exact_completed_session_and_uses_one_price_vintage(market_data):
    frame, sessions = market_data
    for h in [1,5,20]:
        v1.live_forecast(frame,'US',sessions,h,{})
    # New vintage halves past prices (e.g. a split adjustment).
    px=frame[['date','ticker','Close']].copy()
    px['Close'] *= .5
    next_date=pd.Timestamp('2024-07-01')
    endpoints=pd.DataFrame(dict(date=[next_date]*3,ticker=['AAA','BBB','CCC'],Close=[125,250,375]))
    px=pd.concat([px,endpoints],ignore_index=True)
    assert v1.evaluate_previous_forecasts(px,'US',now='2024-07-01T15:00:00Z')==(None,None)
    result,path=v1.evaluate_previous_forecasts(px,'US',now='2024-07-01T21:00:00Z')
    assert set(result.horizon)=={1}  # h=5/20 remain pending
    last_price=px[(px.ticker=='AAA') & (px.date=='2024-06-28')].Close.iloc[0]
    assert result.iloc[0].actual_return==pytest.approx(125/last_price-1)
    assert v1.evaluate_previous_forecasts(px,'US',now='2024-07-02T21:00:00Z')==(None,None)
    # Remove exact target; a later quote must not substitute for it.
    other=px[px.date!=next_date].copy()
    other=pd.concat([other,endpoints.assign(date=pd.Timestamp('2024-07-02'))],ignore_index=True)
    v1.EVAL=v1.ROOT/'other-evaluations'
    assert v1.evaluate_previous_forecasts(other,'US',now='2024-07-02T21:00:00Z')==(None,None)


def test_settings_json_handles_real_xgboost_missing_sentinel(monkeypatch):
    # Undo fixture's cheap estimator for actual model configuration serialization.
    spec=v1.settings(20)
    json.dumps(spec,allow_nan=False)
    assert len(v1.settings_hash(spec))==64


def test_long_horizons_evaluate_and_archive_separately(market_data):
    frame,sessions=market_data
    origin=frame.date.max()
    future=[]
    for h in [1,5,20]:
        v1.live_forecast(frame,'US',sessions,h,{})
        target=v1.target_session(sessions,origin,h)
        for row in frame[frame.date==origin].itertuples():
            future.append(dict(date=target,ticker=row.ticker,Close=row.Close*(1+.01*h)))
    px=pd.concat([frame[['date','ticker','Close']],pd.DataFrame(future)],ignore_index=True)
    evaluation,path=v1.evaluate_previous_forecasts(px,'US',now='2024-08-02T21:00:00Z')
    assert len(evaluation)==9
    assert set(evaluation.horizon)=={1,5,20}
    for row in evaluation.itertuples():
        assert row.actual_return==pytest.approx(row.horizon*.01)
    f=attach_actuals(load_forecasts(v1.ROOT),v1.ROOT,now='2024-08-02T21:00:00Z')
    assert f.actual.notna().all()
    normalize(v1.ROOT)
    records=pd.concat(pd.read_csv(p) for p in (v1.ROOT/'records/evaluations').glob('*.csv'))
    assert set(records.horizon_sessions)=={1,5,20}


def test_live_run_downloads_once_and_refuses_stale_cutoff(market_data,monkeypatch):
    frame,sessions=market_data
    calls=[]
    monkeypatch.setattr(v1.legacy,'us_tickers',lambda:['AAA','BBB','CCC'])
    monkeypatch.setattr(v1.legacy,'download_prices',lambda t:(calls.append(t) or frame.copy()))
    monkeypatch.setattr(v1.legacy,'features',lambda px:px)
    actual_completed=v1.completed_schedule
    monkeypatch.setattr(v1,'completed_schedule',lambda market,start,now=None:
                        actual_completed(market,start,now or '2024-06-28T21:00:00Z'))
    v1.run('US')
    assert len(calls)==1
    assert len(list((v1.PRED/'US').glob('*.csv')))==3
    stale=frame[frame.date<frame.date.max()].copy()
    monkeypatch.setattr(v1.legacy,'download_prices',lambda t:stale)
    with pytest.raises(RuntimeError,match='stale live forecast'):
        v1.run('US')


def test_retired_v11_is_excluded_without_deleting_original(tmp_path):
    directory=tmp_path/'predictions/JP';directory.mkdir(parents=True)
    old=directory/'old.csv'
    pd.DataFrame([dict(ticker='AAA',market='JP',model_version='v1.1-actions',
        data_last_observation_date='2024-01-04',forecast_for_session_date='2024-01-05',
        Ensemble=.01)]).to_csv(old,index=False)
    content=old.read_bytes()
    assert load_forecasts(tmp_path).empty
    assert old.read_bytes()==content
