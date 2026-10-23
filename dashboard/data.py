"""Read-only adapters and comparable, maturity-aware dashboard statistics."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import numpy as np
import pandas as pd

BASIS = 'adjusted_close_ratio'
KEYS = ['market','version','spec','information_set','horizon','origin','target','model']
META = {'date','ticker','market','model_version','forecast_created_at_utc','forecast_for_session_date',
        'data_last_observation_date','close_at_forecast','universe_name','universe_snapshot',
        'universe_snapshot_sha256','universe_expected_count'}
MODELS = ['HistoricalMean','OLS','Ridge','Lasso','ElasticNet','RF','XGB','Ensemble']


def manifest(path):
    p=path.with_suffix('.json')
    obj=json.loads(p.read_text()) if p.exists() else {}
    if obj.get('csv_sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=obj['csv_sha256']:
        raise ValueError(f'CSV integrity failure: {path.name}')
    return obj


def load_forecasts(root):
    root=Path(root)
    frames=[]
    for p in sorted((root/'predictions').rglob('*.csv')):
        meta=manifest(p)
        z=pd.read_csv(p,dtype={'ticker':str})
        z=z.rename(columns={'predicted_return':'prediction','horizon_sessions':'horizon'})
        if not {'ticker','data_last_observation_date','forecast_for_session_date'}.issubset(z):
            raise ValueError(f'Unsupported forecast schema: {p.name}; explicit target session is required')
        if 'prediction' not in z or 'model' not in z:
            cols=[c for c in MODELS if c in z]
            if not cols:
                raise ValueError(f'No known model predictions: {p.name}')
            z=z.melt(id_vars=[c for c in z if c not in cols],value_vars=cols,
                     var_name='model',value_name='prediction')
        z['forecast_file']=p.relative_to(root).as_posix()
        z['market']=z['market'] if 'market' in z else ('JP' if 'JP' in p.parts else 'US')
        z['version']=z['model_version'] if 'model_version' in z else meta.get('model_version','unknown')
        z['spec']=meta.get('settings_sha256','legacy')[:12]
        z['information_set']=z['information_set'] if 'information_set' in z else 'M1'
        z['horizon']=z['horizon'].astype(int) if 'horizon' in z else 1
        z['origin']=pd.to_datetime(z.data_last_observation_date).dt.strftime('%Y-%m-%d')
        z['target']=pd.to_datetime(z.forecast_for_session_date).dt.strftime('%Y-%m-%d')
        z['created']=z['forecast_created_at_utc'] if 'forecast_created_at_utc' in z else meta.get('forecast_created_at_utc','')
        z['return_basis']=z['return_basis'] if 'return_basis' in z else BASIS
        z['prediction']=pd.to_numeric(z.prediction,errors='raise')
        frames.append(z[['forecast_file','ticker',*KEYS,'created','return_basis','prediction']])
    if not frames:
        return pd.DataFrame()
    f=pd.concat(frames,ignore_index=True)
    # Retired one-run V1.1 trial is excluded from display AND matched universes.
    # Original files and existing canonical archives remain read-only.
    f=f[f.version!='v1.1-actions']
    if (~np.isfinite(f.prediction)).any():
        raise ValueError('Forecast contains non-finite predictions')
    if (pd.to_datetime(f.target)<=pd.to_datetime(f.origin)).any():
        raise ValueError('Forecast target must follow its observation date')
    # An explicit specification identifies changes in training/model configuration.
    # If a run was retried, display the first recorded prediction, never average retries.
    return f.sort_values(['created','forecast_file']).drop_duplicates(KEYS+['ticker'],keep='first')


def attach_actuals(f,root,now=None):
    if f.empty:
        return f.assign(actual=pd.Series(dtype=float))
    chunks=[]
    paths=set(Path(root).glob('output/**/evaluations/**/*.csv')) | set(Path(root).glob('evaluations/**/*.csv'))
    for p in sorted(paths):
        manifest(p)
        z=pd.read_csv(p,dtype={'ticker':str})
        if not {'forecast_file','ticker'}.issubset(z):
            continue
        value='actual' if 'actual' in z else 'actual_return'
        if value not in z:
            continue
        z['horizon']=z.horizon.astype(int) if 'horizon' in z else 1
        z['actual']=pd.to_numeric(z[value],errors='raise')
        z['evaluated_at']=z['evaluated_at_utc'] if 'evaluated_at_utc' in z else ''
        chunks.append(z[['forecast_file','ticker','horizon','actual','evaluated_at']])
    if not chunks:
        return f.assign(actual=np.nan)
    a=pd.concat(chunks,ignore_index=True).sort_values('evaluated_at')
    id_cols=['forecast_file','ticker','horizon']
    if ((a.groupby(id_cols).actual.max()-a.groupby(id_cols).actual.min()).abs()>1e-10).any():
        raise ValueError('Conflicting realized returns for one forecast outcome')
    a=a.drop_duplicates(id_cols,keep='first')
    out=f.merge(a,on=id_cols,how='left',validate='many_to_one')
    now=pd.Timestamp(now or datetime.now(timezone.utc))
    # Even an accidentally supplied future outcome cannot turn a pending forecast into a score.
    future=pd.to_datetime(out.target)>pd.Timestamp(now.date())
    out.loc[future,'actual']=np.nan
    return out


def load_benchmarks(root):
    frames=[]
    for p in sorted((Path(root)/'data/benchmarks').rglob('*.csv')):
        manifest(p)
        z=pd.read_csv(p)
        required={'market','benchmark','date','close','return_basis','source'}
        if not required.issubset(z):
            raise ValueError(f'Benchmark CSV missing columns: {p.name}: {required-set(z)}')
        z['date']=pd.to_datetime(z.date).dt.strftime('%Y-%m-%d')
        z['close']=pd.to_numeric(z.close,errors='raise')
        if ((z.close<=0)|~np.isfinite(z.close)).any():
            raise ValueError('Benchmark closes must be positive and finite')
        # Observation snapshots stay immutable; display the earliest recorded value.
        frames.append(z[list(required)])
    if not frames:
        return pd.DataFrame(columns=['market','benchmark','date','close','return_basis','source'])
    return pd.concat(frames,ignore_index=True).drop_duplicates(['market','benchmark','date','return_basis'],keep='first')


def load_weights(root):
    path=Path(root)/'data/index_weights.csv'
    if not path.exists():
        return pd.DataFrame()
    z=pd.read_csv(path,dtype={'ticker':str})
    required={'market','benchmark','snapshot_date','available_at_utc','ticker','weight',
              'expected_count','source','return_basis'}
    if not required.issubset(z):
        raise ValueError(f'Index weight CSV missing columns: {required-set(z)}')
    z['snapshot_date']=pd.to_datetime(z.snapshot_date).dt.strftime('%Y-%m-%d')
    z['available_at_utc']=pd.to_datetime(z.available_at_utc,utc=True)
    z['weight']=pd.to_numeric(z.weight,errors='raise')
    if ((z.weight<0)|~np.isfinite(z.weight)).any():
        raise ValueError('Weights must be nonnegative and finite')
    for _,q in z.groupby(['market','benchmark','snapshot_date']):
        if q.ticker.duplicated().any() or not np.isclose(q.weight.sum(),1,atol=1e-6,rtol=0):
            raise ValueError('Index snapshot has duplicate stocks or weights do not sum to 1')
        if q.expected_count.nunique()!=1 or int(q.expected_count.iloc[0])!=len(q):
            raise ValueError('Index snapshot is incomplete')
        if q.source.isna().any() or q.return_basis.nunique()!=1:
            raise ValueError('Weight source and consistent return basis are required')
    return z


def index_weighted(q,weights,benchmark,cutoff):
    if weights.empty:
        return None
    w=weights[(weights.market==q.market.iloc[0]) & (weights.benchmark==benchmark) &
              (weights.snapshot_date==q.origin.iloc[0])]
    if w.empty or (w.available_at_utc>pd.Timestamp(cutoff)).any():
        return None
    if w.return_basis.iloc[0]!=q.return_basis.iloc[0] or q.return_basis.nunique()!=1:
        return None
    aligned=w.merge(q,on='ticker',how='left',validate='one_to_one',suffixes=('_w',''))
    if aligned.prediction.isna().any():
        return None  # Never renormalize a partial Nikkei/TOPIX membership set.
    prediction=float(np.dot(aligned.weight,aligned.prediction))
    actual=float(np.dot(aligned.weight,aligned.actual)) if aligned.actual.notna().all() else None
    return {'prediction':prediction,'actual':actual,'n':len(w),'weight_coverage':1.0}


def num(x):
    return float(x) if pd.notna(x) and np.isfinite(x) else None


def point(q,ids,benchmark_lookup,market_benchmarks,weights,cutoff,coverage=.95):
    valid=q.dropna(subset=['actual'])
    n=len(q)
    ready=n>0 and len(valid)/n>=coverage
    ic=(valid.prediction.corr(valid.actual,method='spearman')
        if ready and len(valid)>=3 and valid.prediction.nunique()>1 and valid.actual.nunique()>1 else np.nan)
    errors=valid.prediction-valid.actual
    p=dict(origin=ids['origin'],target=ids['target'],prediction=num(q.prediction.mean()),
           paired_prediction=num(valid.prediction.mean()) if ready else None,
           actual=num(valid.actual.mean()) if ready else None,
           rmse=num(np.sqrt(np.mean(errors**2))) if ready else None,ic=num(ic),n=n,
           n_evaluated=len(valid),coverage=num(len(valid)/n) if n else 0,ready=ready,
           reference={},weighted={})
    for b in market_benchmarks:
        a=benchmark_lookup.get((ids['market'],b,ids['origin']))
        end=benchmark_lookup.get((ids['market'],b,ids['target']))
        if a and end and a['return_basis']==end['return_basis']:
            realized=end['close']/a['close']-1
            p['reference'][b]={'actual':num(realized),'basis':a['return_basis'],
                'compatible_basis':a['return_basis']==q.return_basis.iloc[0]}
        weighted=index_weighted(q,weights,b,cutoff)
        if weighted:
            p['weighted'][b]=weighted
    return p


def build_data(f,benchmarks=None,weights=None,synthetic=False,scatter_origins=5,now=None):
    now=pd.Timestamp(now or datetime.now(timezone.utc))
    data={'schema_version':1,'generated_at_utc':now.isoformat(),'synthetic':synthetic,
          'series':[],'scatter':[],'benchmarks':{},'notes':[
            '横軸は予測対象営業日。h日先の累積リターンを同じ始点・終点で比較します。',
            '銘柄平均と価格指数との差は参考差です。個別株の予測誤差とは区別します。',
            '共通銘柄は同じ市場・観測日・対象日・期間に存在する全バージョン／設定の積集合です。',
            '実績カバレッジ95%以上の起点のみ精度を表示。ローリングは成熟済み起点の20本平均です。',
            '指数ウェイト集計は予測時点の固定ウェイトによる近似です。指数の途中の入替・除数変更を再現しません。']}
    if f.empty:
        return data
    f=f.copy()
    from dashboard.exchange_calendar import completed_schedule
    for market,q in f.groupby('market'):
        complete,_=completed_schedule(market,q.origin.min(),now)
        f.loc[(f.market==market)&(f.target>complete.index[-1].strftime('%Y-%m-%d')),'actual']=np.nan
    benchmarks=benchmarks if benchmarks is not None else pd.DataFrame()
    weights=weights if weights is not None else pd.DataFrame()
    lookup={}
    if not benchmarks.empty:
        # Actuals on an incomplete/future market session are not eligible.
        from dashboard.exchange_calendar import completed_schedule
        for market,b in benchmarks.groupby('market'):
            complete,_=completed_schedule(market,b.date.min(),now)
            eligible=set(complete.index.strftime('%Y-%m-%d'))
            b=b[b.date.isin(eligible)]
            for r in b.to_dict('records'):
                lookup[(r['market'],r['benchmark'],r['date'])]=r
            data['benchmarks'][market]=sorted(b.benchmark.unique().tolist())
    common={}
    for ident,z in f.groupby(['market','horizon','origin','target']):
        universes=[set(q.ticker) for _,q in z.groupby(['version','spec','information_set','model'])]
        common[ident]=set.intersection(*universes)
    # Compute actual forecast-close cutoffs once. Weight publication timestamps must
    # precede the information cutoff, not merely the forecast-file creation time.
    from dashboard.exchange_calendar import schedule
    closes={}
    for market,q in f.groupby('market'):
        cal=schedule(market,q.origin.min(),q.origin.max())
        closes.update({(market,d.strftime('%Y-%m-%d')):t for d,t in cal.market_close.items()})
    for ident,z in f.groupby(['market','version','spec','information_set','horizon','model'],sort=True):
        series=dict(zip(['market','version','spec','information_set','horizon','model'],(x.item() if isinstance(x,np.generic) else x for x in ident)))
        series['id']='|'.join(map(str,ident))
        series['points']=[]
        series['return_basis']=z.return_basis.iloc[0]
        for (origin,target),q in z.groupby(['origin','target'],sort=True):
            ids=dict(series,origin=origin,target=target)
            cutoff=closes.get((series['market'],origin))
            if cutoff is None:
                raise ValueError(f'Forecast origin is not an exchange session: {origin}')
            subset=q[q.ticker.isin(common[(series['market'],series['horizon'],origin,target)])]
            b=data['benchmarks'].get(series['market'],[])
            p={'origin':origin,'target':target}
            p['full']=point(q,ids,lookup,b,weights,cutoff)
            p['matched']=p['full'] if len(q)==len(subset) else point(subset,ids,lookup,b,weights,cutoff) if len(subset) else None
            series['points'].append(p)
        data['series'].append(series)
    # Compact recent cross sections: each outcome is stored once with a vector of predictions.
    for ident,z in f.groupby(['market','version','spec','information_set','horizon'],sort=True):
        matured=sorted(z.loc[z.actual.notna(),'target'].unique())[-scatter_origins:]
        latest=z.target.max()
        targets=set(matured+[latest])
        for (origin,target),q in z[z.target.isin(targets)].groupby(['origin','target']):
            models=sorted(q.model.unique().tolist())
            wide=q.pivot(index='ticker',columns='model',values='prediction')
            actual=q.groupby('ticker').actual.first()
            shared=common[(ident[0],ident[4],origin,target)]
            rows=[[str(t),num(actual.get(t)),t in shared,*[num(wide.loc[t,m]) for m in models]] for t in wide.index]
            data['scatter'].append(dict(zip(['market','version','spec','information_set','horizon'],(x.item() if isinstance(x,np.generic) else x for x in ident)),
                 origin=origin,target=target,models=models,rows=rows))
    return data
