import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import uuid
import numpy as np
import pandas as pd
from config import VERSION


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def save_bundle(frame, folder, stem, metadata):
    """Manifest is the commit marker. Data alone is never treated as a complete run."""
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/f'{stem}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}.csv'
    frame.to_csv(path,index=False,mode='x')
    obj = dict(metadata, csv_file=path.name, csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               created_at_utc=now_utc(), model_version=VERSION,
               github_sha=os.getenv('GITHUB_SHA'),github_run_id=os.getenv('GITHUB_RUN_ID'))
    with path.with_suffix('.json').open('x',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2,default=str,allow_nan=False)
    return path


def bundles(folder):
    for p in sorted(Path(folder).glob('*.json')):
        obj = json.loads(p.read_text())
        path = p.with_name(obj['csv_file'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != obj['csv_sha256']:
            raise RuntimeError(f'Forecast log changed: {path}')
        yield path, obj


def forecast_tickers(root, market):
    """Retain quotes needed for evaluation after a constituent leaves the index."""
    tickers = set()
    for path, _ in bundles(Path(root)/'predictions/v2'/market):
        tickers.update(pd.read_csv(path,usecols=['ticker'],dtype=str).ticker)
    return tickers


def evaluate(px, market, root):
    """Score ONLY the exact saved target session. No substituting a later quote.

    Rebase both closes to the same current adjusted-price vintage to avoid false
    split/dividend jumps when the forecast's stored adjusted close is restated.
    """
    root = Path(root)
    eval_dir = root/'evaluations/v2'/market
    done = set()
    for directory in [eval_dir,root/'output/v2/evaluations'/market]:
        for path, _ in bundles(directory):
            z = pd.read_csv(path,dtype={'ticker':str})
            done.update(zip(z.forecast_file,z.ticker,z.horizon,z.information_set,z.model))
    lookup = px.copy()
    lookup['date'] = pd.to_datetime(lookup.date).dt.strftime('%Y-%m-%d')
    close = lookup.set_index(['ticker','date']).Close
    rows = []
    for path, _ in bundles(root/'predictions/v2'/market):
        forecasts = pd.read_csv(path,dtype={'ticker':str})
        ref = str(path.relative_to(root))
        for r in forecasts.itertuples():
            key = (ref,r.ticker,r.horizon,r.information_set,r.model)
            if key in done:
                continue
            a = close.get((r.ticker,r.data_last_observation_date))
            b = close.get((r.ticker,r.forecast_for_session_date))
            if a is None or b is None or pd.isna(a) or pd.isna(b):
                continue
            actual = float(b/a-1)
            rows.append(dict(forecast_file=ref,market=market,ticker=r.ticker,horizon=r.horizon,
                information_set=r.information_set,model=r.model,model_version=r.model_version,
                data_last_observation_date=r.data_last_observation_date,
                forecast_for_session_date=r.forecast_for_session_date,
                prediction=r.prediction,actual=actual,error=r.prediction-actual,
                direction_correct=int(np.sign(r.prediction)==np.sign(actual)),
                evaluated_at_utc=now_utc(),return_basis='current_adjusted_close_ratio'))
    if not rows:
        return None
    return save_bundle(pd.DataFrame(rows),eval_dir,'evaluation',{'market':market,'rows':len(rows)})
