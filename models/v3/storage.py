"""Immutable filing facts, forecasts and separate realized-outcome archives."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,uuid
import pandas as pd
from .config import VERSION,PARSER_VERSION

def now(): return datetime.now(timezone.utc).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()

def write_json_once(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if json.loads(path.read_text())!=obj:
            raise RuntimeError(f'Immutable archive collision: {path.name}')
        return
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,sort_keys=True,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        try:os.link(temporary,path)
        except FileExistsError:
            if json.loads(path.read_text())!=obj:raise RuntimeError('Immutable archive collision: '+path.name)
    finally:
        temporary.unlink(missing_ok=True)

def save_filing(root,market,provider,entity,filing_id,facts,source_sha):
    folder=Path(root)/'data/fundamentals/v3'/market/'filings'
    ident=digest(f'{provider}|{entity}|{filing_id}'.encode())
    path=folder/f'{ident}.json'
    facts=sorted(facts,key=lambda f:json.dumps(f,sort_keys=True))
    payload={'provider':provider,'entity':str(entity),'filing_id':filing_id,
             'parser_version':PARSER_VERSION,'facts':facts}
    if path.exists():
        old=json.loads(path.read_text())
        if {k:old[k] for k in payload}!=payload:
            raise RuntimeError(f'Previously archived filing facts changed: {filing_id}; manual review required')
        return path
    write_json_once(path,dict(payload,source_sha256=source_sha,retrieved_at_utc=now()))
    return path

def load_facts(root,market,tickers=None):
    facts=[]
    for path in sorted((Path(root)/'data/fundamentals/v3'/market/'filings').glob('*.json')):
        obj=json.loads(path.read_text())
        facts.extend(f for f in obj['facts'] if tickers is None or f['ticker'] in tickers)
    return pd.DataFrame(facts)

def save_bundle(frame,folder,stem,metadata):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'{stem}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}.csv'
    frame.to_csv(path,index=False,mode='x')
    obj=dict(metadata,csv_file=path.name,csv_sha256=digest(path.read_bytes()),
             created_at_utc=now(),model_version=VERSION,github_sha=os.getenv('GITHUB_SHA'),
             github_run_id=os.getenv('GITHUB_RUN_ID'))
    write_json_once(path.with_suffix('.json'),obj)
    return path

def bundles(folder):
    for p in sorted(Path(folder).glob('*.json')):
        obj=json.loads(p.read_text());csv=p.with_name(obj['csv_file'])
        if digest(csv.read_bytes())!=obj['csv_sha256']:
            raise RuntimeError('V3 history hash mismatch: '+csv.name)
        yield csv,obj

def forecast_tickers(root,market):
    out=set()
    for p,_ in bundles(Path(root)/'predictions/v3'/market):
        out.update(pd.read_csv(p,usecols=['ticker'],dtype=str).ticker)
    return out

def evaluate(px,market,root):
    folder=Path(root)/'evaluations/v3'/market
    done=set()
    for p,_ in bundles(folder):
        z=pd.read_csv(p,dtype={'ticker':str})
        done.update(zip(z.forecast_file,z.ticker,z.horizon,z.information_set,z.model))
    lookup=px.copy();lookup['date']=pd.to_datetime(lookup.date).dt.strftime('%Y-%m-%d')
    close=lookup.set_index(['ticker','date']).Close
    rows=[]
    for p,_ in bundles(Path(root)/'predictions/v3'/market):
        for r in pd.read_csv(p,dtype={'ticker':str}).itertuples():
            ref=p.relative_to(root).as_posix();key=(ref,r.ticker,r.horizon,r.information_set,r.model)
            if key in done: continue
            a=close.get((r.ticker,r.data_last_observation_date))
            b=close.get((r.ticker,r.forecast_for_session_date))
            if pd.isna(a) or pd.isna(b) or a is None or b is None: continue
            actual=float(b/a-1)
            rows.append(dict(forecast_file=ref,market=market,ticker=r.ticker,horizon=r.horizon,
                information_set=r.information_set,model=r.model,model_version=r.model_version,
                data_last_observation_date=r.data_last_observation_date,
                forecast_for_session_date=r.forecast_for_session_date,
                realized_session_date=r.forecast_for_session_date,prediction=r.prediction,
                actual=actual,error=r.prediction-actual,evaluated_at_utc=now(),
                return_basis='current_adjusted_close_ratio'))
    return save_bundle(pd.DataFrame(rows),folder,'evaluation',{'market':market}) if rows else None
