"""FRED's vintage-count bound is separate from observation pagination."""
from datetime import date
from types import SimpleNamespace
import pandas as pd
import pytest
import context_data


def test_long_history_is_split_and_each_window_is_paginated(monkeypatch,tmp_path):
    monkeypatch.setenv('FRED_API_KEY','TEST_ONLY_NOT_A_REAL_KEY')
    requests=[]
    def get(url,params,timeout):
        p=dict(params);requests.append(p)
        days=(date.fromisoformat(p['realtime_end'])-date.fromisoformat(p['realtime_start'])).days+1
        assert days<=2000
        offset=p['offset']
        row={'date':'2013-01-01','realtime_start':p['realtime_start'],
             'realtime_end':p['realtime_end'],'value':str(offset+1)}
        return SimpleNamespace(status_code=200,json=lambda:{'count':2,'observations':[row]})
    monkeypatch.setattr(context_data.requests,'get',get)
    rows=context_data.fetch_vintages('DGS2','2013-01-01','2026-10-23',tmp_path)
    assert len(rows)==6
    assert [p['offset'] for p in requests]==[0,1,0,1,0,1]
    assert requests[0]['realtime_start']=='2013-01-01'
    assert requests[-1]['realtime_end']=='2026-10-23'
    for p in requests:
        assert p['observation_start']=='2013-01-01'
        assert p['observation_end']=='2026-10-23'
    # Complete response is cached, not downloaded again.
    assert context_data.fetch_vintages('DGS2','2013-01-01','2026-10-23',tmp_path)==rows
    assert len(requests)==6


def test_provider_error_is_actionable_but_never_exposes_key(monkeypatch,tmp_path):
    secret='0123456789abcdef0123456789abcdef'
    monkeypatch.setenv('FRED_API_KEY',secret)
    message='Bad Request. api_key '+secret+' is invalid. https://example.invalid/?api_key='+secret
    monkeypatch.setattr(context_data.requests,'get',lambda *a,**k:SimpleNamespace(
        status_code=400,json=lambda:{'error_message':message}))
    with pytest.raises(RuntimeError) as e:
        context_data.fetch_vintages('DGS2','2013-01-01','2026-10-23',tmp_path)
    assert 'invalid' in str(e.value)
    assert secret not in str(e.value)
    assert 'https://' not in str(e.value)
    assert not list(tmp_path.glob('*.json'))


def test_window_boundary_snapshots_preserve_point_in_time_values(monkeypatch,tmp_path):
    monkeypatch.setenv('FRED_API_KEY','TEST_ONLY')
    # Published data has a revision exactly at the five-year boundary.
    full=[{'date':'2017-12-20','realtime_start':'2017-12-21','realtime_end':'2018-01-01','value':'1'},
          {'date':'2017-12-20','realtime_start':'2018-01-02','realtime_end':'9999-12-31','value':'2'},
          {'date':'2018-01-03','realtime_start':'2018-01-04','realtime_end':'9999-12-31','value':'3'}]
    def get(url,params,timeout):
        lo,hi=params['realtime_start'],params['realtime_end']
        page=[dict(r,realtime_start=max(lo,r['realtime_start']),realtime_end=min(hi,r['realtime_end']))
              for r in full if r['realtime_start']<=hi and r['realtime_end']>=lo]
        return SimpleNamespace(status_code=200,json=lambda:{'count':len(page),'observations':page})
    monkeypatch.setattr(context_data.requests,'get',get)
    rows=context_data.fetch_vintages('DGS2','2013-01-01','2018-01-10',tmp_path)
    expected=context_data.vintage_events(full)
    actual=context_data.vintage_events(rows)
    timeline=pd.DataFrame({'t':pd.date_range('2017-12-24','2018-01-10',tz='UTC')})
    a=pd.merge_asof(timeline,actual,left_on='t',right_on='available_at')
    b=pd.merge_asof(timeline,expected,left_on='t',right_on='available_at')
    pd.testing.assert_series_equal(a.value,b.value)
