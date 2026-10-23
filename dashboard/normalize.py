"""Append-only canonical archives for unchanged V1 and current/future long-form versions."""
from pathlib import Path
import hashlib
import json
import pandas as pd
from dashboard.data import load_forecasts,manifest

ROOT=Path(__file__).resolve().parents[1]
FORECAST_COLUMNS=['schema_version','forecast_id','source_forecast_file','market','system_version','model_version',
    'model','information_set','horizon_sessions','ticker','forecast_created_at_utc',
    'data_last_observation_date','forecast_for_session_date','predicted_return','return_basis','settings_id']


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def append_archive(frame,directory,source,root):
    source_hash=digest(source)
    source_ref=source.relative_to(root).as_posix()
    directory.mkdir(parents=True,exist_ok=True)
    # One source file is immutable, including sources whose legacy manifest lacked a hash.
    for p in directory.glob('*.json'):
        meta=json.loads(p.read_text())
        if meta.get('source_file')==source_ref:
            if meta['source_sha256']!=source_hash: raise ValueError(f'Source history changed: {source_ref}')
            manifest(p.with_suffix('.csv'))
            return
    name=hashlib.sha256(source_ref.encode()).hexdigest()[:16]
    path=directory/f'{source.stem}_{name}.csv'
    frame.to_csv(path,index=False,mode='x')
    meta={'schema_version':1,'source_file':source_ref,'source_sha256':source_hash,
          'csv_file':path.name,'csv_sha256':digest(path)}
    with path.with_suffix('.json').open('x') as fh: json.dump(meta,fh,ensure_ascii=False,indent=2)


def canonical_forecasts(f,root):
    z=f.copy()
    z['schema_version']=1
    z['system_version']=z.version.str.extract(r'(?i)^v(\d+)',expand=False).map(lambda x:'V'+str(x))
    z['forecast_id']=[hashlib.sha256('|'.join(map(str,[r.forecast_file,r.ticker,r.horizon,r.information_set,r.model])).encode()).hexdigest()
                      for r in z.itertuples()]
    z=z.rename(columns={'forecast_file':'source_forecast_file','version':'model_version','horizon':'horizon_sessions',
        'created':'forecast_created_at_utc','origin':'data_last_observation_date','target':'forecast_for_session_date',
        'prediction':'predicted_return','spec':'settings_id'})
    return z[FORECAST_COLUMNS]


def run(root=ROOT):
    root=Path(root)
    f=load_forecasts(root)
    if f.empty: return
    normalized=canonical_forecasts(f,root)
    for ref,z in normalized.groupby('source_forecast_file'):
        append_archive(z,root/'records/predictions',root/ref,root)
    keys=['source_forecast_file','ticker','horizon_sessions']
    for p in sorted(set(root.glob('output/**/evaluations/**/*.csv')) | set(root.glob('evaluations/**/*.csv'))):
        manifest(p)
        e=pd.read_csv(p,dtype={'ticker':str})
        if not {'forecast_file','ticker'}.issubset(e): continue
        col='actual' if 'actual' in e else 'actual_return'
        if col not in e: continue
        e['horizon_sessions']=e.horizon.astype(int) if 'horizon' in e else 1
        e['actual_return']=e[col]
        e=e.rename(columns={'forecast_file':'source_forecast_file'})
        e=e.drop_duplicates(keys,keep='first')
        q=normalized.merge(e[keys+['actual_return','evaluated_at_utc']],on=keys,how='inner',validate='many_to_one')
        q['realized_session_date']=q.forecast_for_session_date
        q=q[['schema_version','forecast_id','source_forecast_file','market','system_version','model_version','model',
             'information_set','horizon_sessions','ticker','forecast_for_session_date','realized_session_date',
             'actual_return','evaluated_at_utc','return_basis']]
        append_archive(q,root/'records/evaluations',p,root)


if __name__=='__main__':
    run()
    print('Canonical archives updated; existing records left unchanged')
