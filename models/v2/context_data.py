"""Point-in-time economic features. FRED key is never written or printed."""
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from config import COMMON, MACRO, VINTAGE_DELAY_DAYS, YOY_FROM_LEVEL, PERCENT_TO_FRACTION


def fetch_vintages(series, start, end, cache):
    path = Path(cache) / f'{series}_{start}_{end}.json'
    if path.exists():
        return json.loads(path.read_text())
    key = os.getenv('FRED_API_KEY')
    if not key:
        raise RuntimeError('M2/M3 require FRED_API_KEY. Add it to GitHub Actions Secrets or use --sets M1.')
    rows, offset = [], 0
    while True:
        try:
            r = requests.get('https://api.stlouisfed.org/fred/series/observations',
                params={'series_id':series, 'api_key':key, 'file_type':'json',
                        'observation_start':start, 'observation_end':end,
                        'realtime_start':'1776-07-04', 'realtime_end':end,
                        'output_type':1, 'limit':100000, 'offset':offset}, timeout=90)
        except requests.RequestException:
            raise RuntimeError(f'FRED network failure: {series}; check access and retry') from None
        # raise_for_status would embed a URL containing the API key.
        if r.status_code != 200:
            raise RuntimeError(f'FRED request failed: {series}, HTTP {r.status_code}')
        body = r.json()
        page = body.get('observations', [])
        rows.extend(page)
        offset += len(page)
        if offset >= int(body.get('count', 0)):
            break
        if not page:
            raise RuntimeError(f'FRED pagination stopped unexpectedly: {series}')
    if not rows:
        raise RuntimeError(f'No vintage observations: {series}')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows), encoding='utf-8')
    tmp.replace(path)
    return rows


def vintage_events(rows, growth=False):
    """Replay revisions for *all* periods; revising an old period never makes it the latest.

    A YoY rate is calculated from values known in the SAME vintage. Output includes
    observation date, allowing stale/discontinued series to be detected downstream.
    """
    z = pd.DataFrame(rows)
    z['date'] = pd.to_datetime(z['date'])
    z['value'] = pd.to_numeric(z['value'], errors='coerce')
    z['available_at'] = pd.to_datetime(z['realtime_start'], utc=True) + pd.Timedelta(days=VINTAGE_DELAY_DAYS)
    z = z.sort_values(['available_at', 'date'])
    state, events = {}, []
    for available, batch in z.groupby('available_at', sort=True):
        for r in batch.itertuples():
            if pd.notna(r.value):
                state[r.date] = float(r.value)
            else:
                state.pop(r.date, None)
        if not state:
            continue
        latest = max(state)
        val = state[latest]
        if growth:
            base = state.get(latest - pd.DateOffset(years=1), np.nan)
            val = val / base - 1 if np.isfinite(base) and base != 0 else np.nan
        events.append((available, val, latest))
    if not events:
        raise RuntimeError('No valid vintage events')
    return pd.DataFrame(events, columns=['available_at', 'value', 'observation_date'])


def context_features(cutoffs, market, sets, start, end, cache, supplied=None):
    """cutoffs indexed by exchange session; values are actual UTC close timestamps."""
    left = pd.DataFrame({'date':cutoffs.index, 'cutoff':cutoffs.values})
    left['cutoff'] = pd.to_datetime(left.cutoff, utc=True)
    left = left.sort_values('cutoff')
    out = left[['date']].copy()
    mapping = dict(COMMON) if any(s in sets for s in ('M2','M3')) else {}
    if 'M3' in sets:
        mapping.update(MACRO[market])
    evidence = {}
    for name, series in mapping.items():
        rows = supplied[series] if supplied is not None else fetch_vintages(series, start, end, cache)
        events = vintage_events(rows, growth=series in YOY_FROM_LEVEL)
        if series in PERCENT_TO_FRACTION:
            events['value'] /= 100
        merged = pd.merge_asof(left, events, left_on='cutoff', right_on='available_at', direction='backward')
        out[name] = merged.value.to_numpy()
        out[name + '_age'] = (pd.to_datetime(out.date).reset_index(drop=True) -
                                  merged.observation_date.reset_index(drop=True)).dt.days.to_numpy()
        # Reject stale features in production instead of silently imputing indefinitely.
        max_age = 14 if name in COMMON else 180
        if not len(merged) or pd.isna(merged.value.iloc[-1]) or out[name + '_age'].iloc[-1] > max_age:
            raise RuntimeError(f'{series}: latest usable observation is missing or stale')
        evidence[series] = {'sha256':hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                            'last_available_at':str(merged.available_at.iloc[-1]),
                            'last_observation_date':str(merged.observation_date.iloc[-1]),
                            'n_vintage_records':len(rows)}
    m2 = []
    if mapping:
        out['term_spread'] = out.us10y - out.us2y
        for n in COMMON:
            if n == 'fx':
                out['fx_ret1'] = out.fx.pct_change(fill_method=None)
                out['fx_ret20'] = out.fx.pct_change(20, fill_method=None)
                m2.extend(['fx_ret1','fx_ret20'])
            else:
                out[n + '_change20'] = out[n].diff(20)
                m2.extend([n, n + '_change20'])
        m2.append('term_spread')
    m3 = list(MACRO[market]) if 'M3' in sets else []
    return out, m2, m3, evidence
