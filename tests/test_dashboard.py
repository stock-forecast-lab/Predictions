from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import pytest
from dashboard.data import load_forecasts,attach_actuals,build_data,index_weighted,load_weights,BASIS
from dashboard.normalize import run as normalize


def create_v1(root):
    folder=root/'predictions/JP';folder.mkdir(parents=True)
    p=folder/'v1.csv'
    pd.DataFrame(dict(market=['JP']*3,model_version=['v1.4-topix500-membership']*3,
        forecast_created_at_utc=['2024-01-04T08:00:00Z']*3,
        data_last_observation_date=['2024-01-04']*3,forecast_for_session_date=['2024-01-05']*3,
        ticker=['A','B','C'],OLS=[.1,.2,.3],Ridge=[.2,.3,.4],Ensemble=[.15,.25,.35],close_at_forecast=[100]*3)).to_csv(p,index=False)
    folder=root/'output/evaluations/JP';folder.mkdir(parents=True)
    pd.DataFrame(dict(forecast_file=['predictions/JP/v1.csv']*3,ticker=['A','B','C'],
        actual_return=[.05,.15,.25],evaluated_at_utc=['2024-01-05T08:00:00Z']*3)).to_csv(folder/'eval.csv',index=False)
    return p


def create_v2(root):
    folder=root/'predictions/v2/JP';folder.mkdir(parents=True)
    p=folder/'v2.csv'
    pd.DataFrame(dict(market=['JP']*2,model_version=['v2.0.0']*2,
        forecast_created_at_utc=['2024-01-04T08:00:00Z']*2,
        data_last_observation_date=['2024-01-04']*2,forecast_for_session_date=['2024-01-05']*2,
        ticker=['A','B'],model=['Ridge']*2,information_set=['M2']*2,horizon=[1]*2,prediction=[.03,.08])).to_csv(p,index=False)
    folder=root/'output/v2/evaluations/JP';folder.mkdir(parents=True)
    pd.DataFrame(dict(forecast_file=['predictions/v2/JP/v2.csv']*2,ticker=['A','B'],horizon=[1]*2,
        actual=[.05,.15],evaluated_at_utc=['2024-01-05T08:00:00Z']*2)).to_csv(folder/'eval.csv',index=False)
    return p


def test_wide_v1_long_v2_common_sample_and_broadcast_actual(tmp_path):
    create_v1(tmp_path);create_v2(tmp_path)
    f=attach_actuals(load_forecasts(tmp_path),tmp_path)
    assert len(f)==11
    assert f.actual.notna().all()
    data=build_data(f)
    v1=next(s for s in data['series'] if s['model']=='Ridge' and s['version'].startswith('v1'))
    p=v1['points'][0]
    assert p['full']['n']==3
    assert p['matched']['n']==2
    assert p['matched']['prediction']==pytest.approx(.25)
    assert p['matched']['actual']==pytest.approx(.1)
    assert p['matched']['rmse']==pytest.approx(.15)
    assert len(data['series'])==4


def test_pending_target_never_scores_even_if_future_actual_was_supplied(tmp_path):
    create_v1(tmp_path)
    f=attach_actuals(load_forecasts(tmp_path),tmp_path)
    data=build_data(f,now='2024-01-05T03:00:00Z') # Japan is still trading.
    assert all(s['points'][0]['full']['actual'] is None for s in data['series'])
    assert all(s['points'][0]['full']['rmse'] is None for s in data['series'])


def test_exact_benchmark_interval_and_not_next_quote(tmp_path):
    create_v1(tmp_path)
    f=attach_actuals(load_forecasts(tmp_path),tmp_path)
    b=pd.DataFrame(dict(market=['JP']*3,benchmark=['NIKKEI225']*3,date=['2024-01-04','2024-01-05','2024-01-09'],
        close=[100,105,140],return_basis=['price_index']*3,source=['fixture']*3))
    data=build_data(f,b)
    p=data['series'][0]['points'][0]['full']
    assert p['reference']['NIKKEI225']['actual']==pytest.approx(.05)
    assert not p['reference']['NIKKEI225']['compatible_basis']
    data=build_data(f,b[b.date!='2024-01-05'])
    assert 'NIKKEI225' not in data['series'][0]['points'][0]['full']['reference']


def test_canonical_archives_append_only_and_legacy_forecasts_unchanged(tmp_path):
    p=create_v1(tmp_path);before=p.read_bytes()
    normalize(tmp_path)
    f=list((tmp_path/'records/predictions').glob('*.csv'))
    a=list((tmp_path/'records/evaluations').glob('*.csv'))
    assert len(f)==len(a)==1
    first=f[0].read_bytes()
    n=pd.read_csv(f[0]);e=pd.read_csv(a[0])
    assert len(n)==len(e)==9
    assert set(n.system_version)=={'V1'}
    assert n.forecast_id.nunique()==9
    normalize(tmp_path)
    assert f[0].read_bytes()==first and p.read_bytes()==before
    # Legacy manifests did not contain hashes; the new archive catches mutation.
    p.write_bytes(before+b'\n')
    with pytest.raises(ValueError,match='history changed'): normalize(tmp_path)


def test_new_versions_accept_the_canonical_schema_without_a_new_adapter(tmp_path):
    folder=tmp_path/'predictions/v5/US';folder.mkdir(parents=True)
    p=folder/'forecast.csv'
    pd.DataFrame([dict(schema_version=1,market='US',model_version='v5.0.0',model='ParticleFilter',
        information_set='M3',horizon_sessions=20,ticker='ABC',predicted_return=.02,
        data_last_observation_date='2024-01-02',forecast_for_session_date='2024-01-31',
        forecast_created_at_utc='2024-01-02T22:00:00Z',return_basis=BASIS)]).to_csv(p,index=False)
    f=load_forecasts(tmp_path)
    assert len(f)==1 and f.model.iloc[0]=='ParticleFilter' and f.horizon.iloc[0]==20
    assert build_data(attach_actuals(f,tmp_path))['series'][0]['version']=='v5.0.0'


def weights(root):
    folder=root/'data';folder.mkdir(exist_ok=True)
    p=folder/'index_weights.csv'
    pd.DataFrame(dict(market=['JP']*2,benchmark=['TOPIX500']*2,snapshot_date=['2024-01-04']*2,
        available_at_utc=['2024-01-04T00:00:00Z']*2,ticker=['A','B'],weight=[.8,.2],
        expected_count=[2]*2,source=['fixture']*2,return_basis=[BASIS]*2)).to_csv(p,index=False)
    return p


def test_weights_require_complete_membership_and_pre_cutoff_publication(tmp_path):
    create_v2(tmp_path);weights(tmp_path)
    f=attach_actuals(load_forecasts(tmp_path),tmp_path)
    w=load_weights(tmp_path)
    result=index_weighted(f,w,'TOPIX500','2024-01-04T06:00:00Z')
    assert result['prediction']==pytest.approx(.04)
    assert index_weighted(f.iloc[:1],w,'TOPIX500','2024-01-04T06:00:00Z') is None
    w['available_at_utc']=pd.Timestamp('2024-01-04T07:00:00Z')
    assert index_weighted(f,w,'TOPIX500','2024-01-04T06:00:00Z') is None


def test_invalid_weights_are_not_silently_renormalized(tmp_path):
    p=weights(tmp_path)
    z=pd.read_csv(p);z.loc[0,'weight']=.5;z.to_csv(p,index=False)
    with pytest.raises(ValueError,match='sum to 1'): load_weights(tmp_path)


def test_live_v1_estimator_file_is_byte_for_byte_reference():
    root=Path(__file__).resolve().parents[1]
    expected='665656877e731573d6a63cbf49172734016fd2e796a642c6307bcf0a133dc232'
    # The reference is the actual working V1 ZIP supplied by the user.
    assert hashlib.sha256((root/'models/v1/main.py').read_bytes()).hexdigest()==expected


def test_two_workflows_have_independent_commands_and_shared_concurrency():
    import yaml
    root=Path(__file__).resolve().parents[1]
    for version in ['v1','v2']:
        workflow=yaml.safe_load((root/'.github/workflows'/f'{version}-forecast.yml').read_text())
        steps=workflow['jobs']['forecast']['steps']
        run=next(s for s in steps if s.get('name','').startswith('Run '))['run']
        assert f'python models/{version}/' in run
        assert workflow['concurrency']['cancel-in-progress'] is False
        assert any('dashboard.normalize' in s.get('run','') for s in steps)
        assert any('dashboard/data.json' in s.get('run','') for s in steps)


def test_optional_jquants_topix_and_topix500_pagination(tmp_path,monkeypatch):
    import dashboard.build as dashboard
    import requests
    monkeypatch.setenv('JQUANTS_API_KEY','DO_NOT_STORE')
    calls=[]
    class Response:
        status_code=200
        def __init__(self,body): self.body=body
        def json(self): return self.body
    def get(url,headers,params,timeout):
        calls.append((url,dict(params)))
        assert headers['x-api-key']=='DO_NOT_STORE'
        page=params.get('pagination_key')
        return Response({'data':[{'Date':'2024-01-04' if not page else '2024-01-05','C':100 if not page else 105}],
                         'pagination_key':None if page else 'PAGE2'})
    monkeypatch.setattr(requests,'get',get)
    f=pd.DataFrame(dict(origin=['2024-01-04'],market=['JP']))
    assert dashboard.refresh_jquants(tmp_path,f)==[]
    from dashboard.data import load_benchmarks
    b=load_benchmarks(tmp_path)
    assert set(b.benchmark)=={'TOPIX','TOPIX500'} and len(b)==4
    assert len(calls)==4
    assert any(params.get('code')=='002C' for _,params in calls)
    assert all('DO_NOT_STORE' not in p.read_text() for p in tmp_path.rglob('*') if p.is_file())


def test_v1_wrapper_preserves_new_and_legacy_history_paths():
    import subprocess,sys
    root=Path(__file__).resolve().parents[1]
    code='''
import run as r
seen=[]
r._original_evaluate=lambda px,market:(seen.append((r.v1.PRED,r.v1.EVAL)) or (None,None))
r.evaluate_both(None,'JP')
assert seen==[(r.PRED,r.EVAL),(r.ROOT/'predictions',r.ROOT/'output/evaluations')]
assert r.v1.PRED==r.PRED and r.v1.EVAL==r.EVAL
r._original_duplicate=lambda market,date:r.v1.PRED==r.ROOT/'predictions'
assert r.duplicate_in_either('JP','2024-01-04')
assert r.v1.PRED==r.PRED
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=root/'models/v1',capture_output=True,text=True)
    assert result.returncode==0,result.stderr
