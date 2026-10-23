import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile
import numpy as np
import pandas as pd
import pytest
from models.v3 import sec,edinet,bridge
from models.v3.financial_features import attach_financials
from models.v3.main import forecast_origin
from models.v3.storage import save_filing,load_facts,save_bundle,evaluate
from models.v3.config import FINANCIAL_FEATURES


def fact(metric,value,end='2020-12-31',start='',available='2021-02-02T00:00:00+00:00',ticker='AAA',filing='A'):
    return dict(ticker=ticker,metric=metric,start=start,end=end,value=float(value),unit='shares' if metric in {'shares','issued_shares','treasury_shares'} else 'USD',scope='consolidated',tag=metric,priority=0,filing_id=filing,published_at_utc=available,available_at_utc=available,provider='SEC',valuation_allowed=True)


def annual_facts(ticker='AAA'):
    rows=[]
    for year in range(2018,2023):
        for metric,value,start in [('assets',1000,''),('equity',600,''),('income',100,f'{year}-01-01'),('revenue',500+year,f'{year}-01-01'),('cfo',110,f'{year}-01-01'),('shares',100,'')]:
            rows.append(fact(metric,value,f'{year}-12-31',start,f'{year+1}-02-02T00:00:00+00:00',ticker,str(year)))
    return pd.DataFrame(rows)


def prices(n=720,count=3):
    dates=pd.bdate_range('2020-01-01',periods=n);rng=np.random.default_rng(2);parts=[]
    for i in range(count):
        close=(20+i)*np.exp(np.cumsum(rng.normal(.0001,.01,n)))
        parts.append(pd.DataFrame(dict(date=dates,ticker=f'T{i}',Open=close,High=close*1.01,Low=close*.99,Close=close,RawClose=close,Volume=rng.integers(10000,100000,n),Split=0.)))
    return pd.concat(parts,ignore_index=True),dates


def test_sec_currency_period_and_availability():
    row=dict(val=1000,accn='A',end='2020-12-31',filed='2021-02-01',form='10-K')
    body={'facts':{'us-gaap':{'Assets':{'units':{'USD':[row],'CAD':[dict(row,val=9999)]}}}}}
    result=sec.extract_facts(body,['AAA'],{'A':'2021-02-01T21:00:00Z'})['A']
    assert len(result)==1 and result[0]['value']==1000
    assert result[0]['available_at_utc']=='2021-02-02T21:00:00+00:00'
    assert sec.extract_facts(body,['AAA'],{})['A'][0]['available_at_utc']=='2021-02-03T00:00:00+00:00'


def package():
    xml='''<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:j="http://example.invalid/jpcrp" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<x:context id="I"><x:entity><x:identifier scheme="x">E1</x:identifier></x:entity><x:period><x:instant>2020-12-31</x:instant></x:period></x:context>
<x:context id="D"><x:entity><x:identifier scheme="x">E1</x:identifier></x:entity><x:period><x:startDate>2020-01-01</x:startDate><x:endDate>2020-12-31</x:endDate></x:period></x:context>
<x:context id="SEG"><x:entity><x:identifier scheme="x">E1</x:identifier></x:entity><x:period><x:instant>2020-12-31</x:instant></x:period><x:scenario><d:explicitMember dimension="j:Axis">j:RegionMember</d:explicitMember></x:scenario></x:context>
<x:unit id="JPY"><x:measure>iso4217:JPY</x:measure></x:unit>
<j:TotalAssetsSummaryOfBusinessResults contextRef="I" unitRef="JPY">1000</j:TotalAssetsSummaryOfBusinessResults>
<j:NetAssetsSummaryOfBusinessResults contextRef="I" unitRef="JPY">600</j:NetAssetsSummaryOfBusinessResults>
<j:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults contextRef="D" unitRef="JPY">100</j:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults>
<j:NetSalesSummaryOfBusinessResults contextRef="D" unitRef="JPY">500</j:NetSalesSummaryOfBusinessResults>
<j:TotalAssetsSummaryOfBusinessResults contextRef="SEG" unitRef="JPY">999999</j:TotalAssetsSummaryOfBusinessResults>
</x:xbrl>'''
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('XBRL/PublicDoc/report.xbrl',xml)
    return out.getvalue()


def document():
    return dict(secCode='12340',docTypeCode='120',withdrawalStatus='0',disclosureStatus='0',xbrlFlag='1',edinetCode='E1',docID='DOC1',submitDateTime='2021-03-01 15:00')


def test_edinet_context_scope_and_units():
    rows=edinet.parse_package(package(),document(),'1234.T')
    assert len(rows)==4 and {r['scope'] for r in rows}=={'consolidated'}
    assert next(r for r in rows if r['metric']=='assets')['value']==1000
    assert all(r['available_at_utc']=='2021-03-02T06:00:00+00:00' for r in rows)


def test_edinet_resumes_and_never_writes_key(monkeypatch,tmp_path):
    secret='DO_NOT_WRITE_THIS_KEY';monkeypatch.setenv('EDINET_API_KEY',secret);calls=[]
    class C:
        def get(self,url,params):
            calls.append(url)
            if url.endswith('documents.json'):
                body={'metadata':{'status':'200'},'results':[document()]}
                return SimpleNamespace(json=lambda:body,content=json.dumps(body).encode())
            return SimpleNamespace(content=package())
    for _ in range(2):assert edinet.synchronize(tmp_path,['1234.T'],'2021-03-01','2021-03-01',client=C())['complete']
    assert sum(not u.endswith('documents.json') for u in calls)==1
    assert len(load_facts(tmp_path,'JP'))==4
    assert all(secret not in p.read_text() for p in tmp_path.rglob('*.json'))


def test_changed_filing_facts_stop(tmp_path):
    rows=annual_facts().to_dict('records')
    save_filing(tmp_path,'US','SEC','1','A',rows,'first')
    save_filing(tmp_path,'US','SEC','1','A',rows,'second_download')
    rows[0]['value']+=1
    with pytest.raises(RuntimeError,match='changed'):save_filing(tmp_path,'US','SEC','1','A',rows,'changed')


def test_disclosure_and_revision_do_not_leak_backward():
    px,dates=prices(count=1);px['ticker']='AAA'
    cutoff=pd.Series(pd.to_datetime(dates,utc=True)+pd.Timedelta(hours=21),index=dates)
    base=annual_facts();a=attach_financials(px,base,cutoff)
    future=fact('income',900,'2020-12-31','2020-01-01','2022-10-01T00:00:00+00:00','AAA','AMEND')
    b=attach_financials(px,pd.concat([base,pd.DataFrame([future])],ignore_index=True),cutoff)
    before=dates[dates<pd.Timestamp('2022-10-01')]
    pd.testing.assert_frame_equal(a.loc[a.date.isin(before),FINANCIAL_FEATURES],b.loc[b.date.isin(before),FINANCIAL_FEATURES])
    assert set(a.loc[a.date<pd.Timestamp('2020-02-02'),'fin_income_end'])=={'2018-12-31'}
    assert set(a.loc[(a.date>pd.Timestamp('2020-02-03'))&(a.date<pd.Timestamp('2021-02-02')),'fin_income_end'])=={'2019-12-31'}


def test_split_consistency_and_unknown_treasury():
    dates=pd.bdate_range('2020-12-01',periods=110)
    z=pd.DataFrame(dict(date=dates,ticker='AAA',RawClose=20.,Close=10.,Split=0.))
    z.loc[z.date>=dates[80],'RawClose']=10.;z.loc[z.date==dates[80],'Split']=2.
    cutoff=pd.Series(pd.to_datetime(dates,utc=True)+pd.Timedelta(hours=21),index=dates)
    f=annual_facts();out=attach_financials(z,f,cutoff)
    a=out.loc[out.date==dates[79],'fin_log_market_cap_estimate'].iloc[0]
    b=out.loc[out.date==dates[80],'fin_log_market_cap_estimate'].iloc[0]
    assert np.isfinite(a) and a==pytest.approx(b)
    f=pd.concat([f[f.metric!='shares'],pd.DataFrame([fact('issued_shares',100)])],ignore_index=True)
    assert attach_financials(z,f,cutoff).fin_log_market_cap_estimate.isna().all()


def test_matched_predictions_are_invariant_to_future_information():
    px,dates=prices();facts=pd.concat([annual_facts(f'T{i}') for i in range(3)],ignore_index=True)
    cutoffs=pd.Series(pd.to_datetime(dates,utc=True)+pd.Timedelta(hours=21),index=dates)
    panel=attach_financials(bridge.features.make_panel(px,dates),facts,cutoffs);day=dates[650]
    before=forecast_origin(panel,day,[1,5,20],bridge.features.MARKET_FEATURES,None,quick=True)
    px.loc[px.date>day,'Close']*=5
    future=fact('income',1e9,'2021-12-31','2021-01-01',(day+pd.Timedelta(days=3)).tz_localize('UTC').isoformat(),'T0','FUTURE')
    panel=attach_financials(bridge.features.make_panel(px,dates),pd.concat([facts,pd.DataFrame([future])],ignore_index=True),cutoffs)
    after=forecast_origin(panel,day,[1,5,20],bridge.features.MARKET_FEATURES,None,quick=True)
    np.testing.assert_allclose(before.prediction,after.prediction,rtol=1e-12,atol=1e-14)
    for _,g in before.groupby('horizon'):
        assert g.training_sample_sha256.nunique()==g.train_rows.nunique()==1
        assert (g.train_last_label_end<day).all() and g.ticker.nunique()==3
    assert set(before.information_set)=={'M3_CONTROL','M3_FINANCIAL'}


def test_evaluation_and_shared_dashboard(tmp_path):
    from dashboard.data import load_forecasts,attach_actuals,build_data
    from dashboard.normalize import run
    rows=pd.DataFrame([dict(ticker='AAA',market='US',model_version='v3.0.0',model='Ridge',information_set='M3_FINANCIAL',horizon=1,prediction=.02,data_last_observation_date='2024-01-02',forecast_for_session_date='2024-01-03',forecast_created_at_utc='2024-01-02T22:00:00Z',return_basis='adjusted_close_ratio')])
    p=save_bundle(rows,tmp_path/'predictions/v3/US','prediction',{})
    px=pd.DataFrame(dict(ticker=['AAA']*2,date=pd.to_datetime(['2024-01-02','2024-01-03']),Close=[100.,101.]))
    assert evaluate(px,'US',tmp_path) and evaluate(px,'US',tmp_path) is None
    f=attach_actuals(load_forecasts(tmp_path),tmp_path)
    assert f.actual.iloc[0]==pytest.approx(.01) and build_data(f)['series'][0]['version']=='v3.0.0'
    run(tmp_path);assert list((tmp_path/'records/evaluations').glob('*.csv'))
    original=p.read_bytes();run(tmp_path);assert p.read_bytes()==original


def test_timestamp_mapping_and_workflow():
    import yaml
    assert sec.submission_times({'accessionNumber':['A','B'],'acceptanceDateTime':['2020-01-01T10:00:00Z']})=={'A':'2020-01-01T10:00:00Z'}
    root=Path(__file__).resolve().parents[2]
    workflow=yaml.safe_load((root/'.github/workflows/v3-forecast.yml').read_text())
    assert workflow['concurrency']['group']=='stock-forecast-${{ github.workflow }}-${{ github.ref }}'
    artifact=next(s for s in workflow['jobs']['forecast']['steps'] if s.get('uses','').startswith('actions/upload-artifact'))
    assert 'data/fundamentals/v3' in artifact['with']['path']
    source=(root/'scripts/publish_results.py').read_text()
    assert "'evaluations/v3'" in source and "'data/fundamentals/v3'" in source


@pytest.mark.parametrize('market',['JP','US'])
def test_live_entrypoint_preserves_forecasts_and_exports_audits(monkeypatch,tmp_path,market):
    import models.v3.main as main
    px,dates=prices()
    calendar=pd.DataFrame({'market_close':pd.to_datetime(dates,utc=True)+pd.Timedelta(hours=21)},index=dates)
    extended=pd.bdate_range(dates[0],periods=len(dates)+40)
    full=pd.DataFrame({'market_close':pd.to_datetime(extended,utc=True)+pd.Timedelta(hours=21)},index=extended)
    tickers=sorted(px.ticker.unique())
    for ticker in tickers:
        save_filing(tmp_path,market,'SEC',ticker,'synthetic',annual_facts(ticker).to_dict('records'),'mock')
    monkeypatch.setattr(main.bridge.market,'completed_schedule',lambda *a:(calendar,full))
    monkeypatch.setattr(main.bridge,'universe',lambda *a:(tickers,{'name':'SYNTHETIC','count':3}))
    monkeypatch.setattr(main,'prices',lambda *a:px)
    args=SimpleNamespace(market=market,sync=False,sync_only=False,base_set='M1',financial_start='2018-01-01',max_scan_days=120,max_age_days=550,min_coverage=.8,horizons=[1,5,20],models=['HistoricalMean','Ridge'],profile='light',train_sessions=756)
    path=main.run(args,root=tmp_path)
    assert path and path.exists()
    data=pd.read_csv(path)
    assert len(data)==3*3*2*3
    assert set(data.model_version)=={'v3.0.0'} and set(data.market)=={market}
    assert data.groupby('horizon').training_sample_sha256.nunique().eq(1).all()
    assert list((tmp_path/f'data/fundamentals/v3/{market}/feature_snapshots').glob('*.csv'))
    original=path.read_bytes();assert main.run(args,root=tmp_path) is None
    assert original==path.read_bytes() and len(list(path.parent.glob('*.csv')))==1


def test_edinet_failed_document_does_not_mark_day_complete(monkeypatch,tmp_path):
    from models.v3.http import FetchError
    monkeypatch.setenv('EDINET_API_KEY','TEST_ONLY')
    class C:
        def get(self,url,params):
            if url.endswith('documents.json'):
                body={'metadata':{'status':'200'},'results':[document()]}
                return SimpleNamespace(json=lambda:body,content=json.dumps(body).encode())
            return SimpleNamespace(content=b'not a ZIP')
    with pytest.raises(FetchError):edinet.synchronize(tmp_path,['1234.T'],'2021-03-01','2021-03-01',client=C())
    assert not list((tmp_path/'data/fundamentals/v3/JP/scans').glob('**/*.json'))


def test_sec_skips_unchanged_periodic_facts(monkeypatch,tmp_path):
    monkeypatch.setenv('SEC_USER_AGENT','Research test@example.invalid')
    calls=[]
    row=dict(val=1000,accn='A',end='2020-12-31',filed='2021-02-01',form='10-K')
    body={'facts':{'us-gaap':{'Assets':{'units':{'USD':[row]}}}}}
    recent={'filings':{'recent':{'accessionNumber':['A'],'acceptanceDateTime':['2021-02-01T21:00:00Z'],'form':['10-K'],'filingDate':['2021-02-01']}}}
    class C:
        def get(self,url):
            calls.append(url)
            value=({'0':{'cik_str':1,'ticker':'AAA'}} if 'company_tickers' in url else recent if '/submissions/' in url else body)
            return SimpleNamespace(json=lambda:value,content=json.dumps(value).encode())
    sec.synchronize(tmp_path,['AAA'],'2018-01-01',client=C())
    sec.synchronize(tmp_path,['AAA'],'2018-01-01',client=C())
    assert sum('companyfacts' in u for u in calls)==1
    assert len(load_facts(tmp_path,'US'))==1


def test_quarterly_periods_and_custom_edinet_namespace():
    from models.v3.financial_features import annualized
    quarter=dict(val=25,accn='Q',start='2021-01-01',end='2021-03-31',filed='2021-05-01',form='10-Q')
    body={'facts':{'us-gaap':{'NetIncomeLoss':{'units':{'USD':[quarter]}}}}}
    facts=sec.extract_facts(body,['AAA'],{})['Q']
    assert facts[0]['start']=='2021-01-01'
    assert annualized(facts[0])==pytest.approx(25*365.25/90)
    assert np.isnan(annualized(dict(value=1,start='2021-01-01',end='2021-01-02')))
    # A corporate extension reusing a standard local tag must not supply facts.
    with zipfile.ZipFile(BytesIO(package())) as z:
        xml=z.read('XBRL/PublicDoc/report.xbrl').replace(b'http://example.invalid/jpcrp',b'http://company.invalid/custom')
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('XBRL/PublicDoc/report.xbrl',xml)
    assert edinet.parse_package(out.getvalue(),document(),'1234.T')==[]


def test_yahoo_split_adjusted_close_restores_contemporaneous_valuation(monkeypatch):
    from models.v3 import prices as provider
    dates=pd.bdate_range('2021-01-04',periods=4)
    def data(close,split):
        return pd.DataFrame({'Close':close,'Adj Close':close,'Open':close,'High':close,'Low':close,
                             'Volume':1000,'Stock Splits':split},index=dates[:len(close)])
    monkeypatch.setattr(provider.yf,'download',lambda *a,**k:data([20.,20.],[0.,0.]))
    before=provider.prices(['AAA'],'2021-01-01',dates[1])
    monkeypatch.setattr(provider.yf,'download',lambda *a,**k:data([10.,10.,10.,10.],[0.,0.,2.,0.]))
    after=provider.prices(['AAA'],'2021-01-01',dates[-1])
    assert after.RawClose.tolist()==[20.,20.,10.,10.]
    assert before.RawClose.tolist()==after.RawClose.iloc[:2].tolist()
