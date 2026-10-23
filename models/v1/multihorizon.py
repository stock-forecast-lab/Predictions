"""V1 price/volume baseline: direct 1/5/20-session cumulative-return forecasts.

The historical main.py stays byte-for-byte intact. Its feature builder and
estimators are reused; labels, completion checks, and immutable I/O live here.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import numpy as np
import pandas as pd
import main as legacy
from config import ROOT, PRED, EVAL, START, MIN_HISTORY, TOP_N
from dashboard.exchange_calendar import completed_schedule

MODEL_VERSION = 'v1.5-multihorizon'
DEFAULT_HORIZONS = (1, 5, 20)
FEATURES = legacy.FEATURES
RETURN_BASIS = 'adjusted_close_ratio'


def settings(horizon):
    estimators = legacy.models()
    parameters = {name: estimator.named_steps['m'].get_params(deep=False)
                  for name, estimator in estimators.items()}
    # XGBoost expresses its missing-value sentinel as NaN; JSON stores its name.
    parameters = json.loads(json.dumps(parameters, default=str), parse_constant=str)
    return dict(model_version=MODEL_VERSION, horizon_sessions=int(horizon),
                features=FEATURES, start=START, min_history=MIN_HISTORY,
                target='adjusted_close_at_exact_session_t_plus_h / adjusted_close_t - 1',
                training='expanding; origin < cutoff; label_end <= cutoff',
                estimators=parameters,
                preprocessing='legacy V1 imputation/scaling; unchanged feature builder')


def settings_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest()


def target_session(sessions, origin, horizon):
    origin = pd.Timestamp(origin).normalize()
    position = sessions.get_indexer([origin])[0]
    if position < 0 or position + horizon >= len(sessions):
        raise ValueError(f'No exact exchange target for {origin.date()} + {horizon} sessions')
    return sessions[position + horizon]


def add_target(frame, sessions, horizon):
    """Align by exchange sessions, never by the next available stock quote."""
    if horizon not in DEFAULT_HORIZONS:
        raise ValueError('V1 horizons must be 1, 5 or 20 sessions')
    z = frame.copy()
    dates = pd.DatetimeIndex(sessions).normalize()
    targets = pd.Series(dates[horizon:], index=dates[:-horizon])
    z['label_end_date'] = z.date.map(targets)
    prices = z.set_index(['ticker', 'date']).Close
    future_keys = pd.MultiIndex.from_arrays([z.ticker, z.label_end_date], names=['ticker', 'date'])
    future = prices.reindex(future_keys).to_numpy()
    z['target'] = future / z.Close.to_numpy() - 1
    z.loc[~np.isfinite(z.target), 'target'] = np.nan
    return z


def training_rows(frame, cutoff):
    cutoff = pd.Timestamp(cutoff)
    return frame[(frame.date < cutoff) & (frame.label_end_date <= cutoff) & frame.target.notna()]


def already_forecast(market, observation_date, horizon=1, signature=None):
    signature = signature or settings_hash(settings(horizon))
    for path in (PRED / market).glob('*.json'):
        meta = json.loads(path.read_text(encoding='utf-8'))
        if (meta.get('model_version') == MODEL_VERSION
                and meta.get('data_last_observation_date') == observation_date
                and meta.get('horizon_sessions') == horizon
                and meta.get('settings_sha256') == signature):
            csv = path.with_suffix('.csv')
            if not csv.exists() or hashlib.sha256(csv.read_bytes()).hexdigest() != meta['csv_sha256']:
                raise ValueError(f'Incomplete or changed forecast: {path.name}')
            return True
    return False


def save_bundle(frame, folder, stem, meta):
    path = legacy.unique_path(folder, stem, '.csv')
    legacy.exclusive_csv(frame, path)
    meta = dict(meta, csv_file=path.name, csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with path.with_suffix('.json').open('x', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def live_forecast(frame, market, sessions, horizon, universe_meta):
    cutoff = pd.Timestamp(frame.date.max())
    spec = settings(horizon)
    signature = settings_hash(spec)
    if already_forecast(market, cutoff.date().isoformat(), horizon, signature):
        print(f'{market} h={horizon}: already saved; preserving original prediction', flush=True)
        return None, None
    labeled = add_target(frame, sessions, horizon)
    train = training_rows(labeled, cutoff)
    if train.date.nunique() < MIN_HISTORY:
        raise RuntimeError(f'{market} h={horizon}: fewer than {MIN_HISTORY} matured training origins')
    today = frame[frame.date == cutoff].copy()
    estimates = {}
    for name, estimator in legacy.models().items():
        print(f'{market} h={horizon}: fitting {name} ({len(train)} rows)', flush=True)
        estimator.fit(train[FEATURES], train.target)
        estimates[name] = estimator.predict(today[FEATURES])
    result = today[['ticker', 'Close']].rename(columns={'Close': 'close_at_forecast'})
    for name, values in estimates.items():
        if not np.isfinite(values).all():
            raise ValueError(f'{name} returned non-finite predictions')
        result[name] = values
    result['Ensemble'] = result[list(estimates)].mean(axis=1)
    target = target_session(sessions, cutoff, horizon).date().isoformat()
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    columns = dict(market=market, model_version=MODEL_VERSION, horizon_sessions=horizon,
                   information_set='M1', return_basis=RETURN_BASIS,
                   data_last_observation_date=cutoff.date().isoformat(),
                   forecast_for_session_date=target, forecast_created_at_utc=created)
    for name, value in {**columns, **universe_meta}.items():
        result[name] = value
    result = result.sort_values('Ensemble', ascending=False)
    meta = dict(columns, **universe_meta, settings=spec, settings_sha256=signature,
                rows=len(result), training_rows=len(train),
                training_origin_start=train.date.min().date().isoformat(),
                training_origin_end=train.date.max().date().isoformat(),
                latest_training_label_end=train.label_end_date.max().date().isoformat(),
                training_sample_sha256=hashlib.sha256(
                    train[['ticker','date','label_end_date','target']].to_csv(index=False).encode()).hexdigest(),
                github_run_id=os.getenv('GITHUB_RUN_ID'), github_sha=os.getenv('GITHUB_SHA'))
    path = save_bundle(result, PRED / market,
                       f'{cutoff:%Y-%m-%d}_h{horizon}_for_{target}_{MODEL_VERSION}', meta)
    return result, path


def evaluate_previous_forecasts(px, market, now=None):
    """Score exact, completed target sessions. Missing endpoints remain pending.

    Both endpoints come from the same downloaded adjusted-price vintage, avoiding
    false returns after splits/dividends revise the stored observation price.
    Legacy CSVs with no horizon field are interpreted as h=1.
    """
    folder = PRED / market
    if not folder.exists():
        return None, None
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    complete, _ = completed_schedule(market, px.date.min(), now)
    eligible = set(complete.index)
    prices = px[px.date.isin(eligible)].set_index(['ticker', 'date']).Close
    done = set()
    for path in (EVAL / market).glob('*.csv'):
        prior = pd.read_csv(path, dtype={'ticker': str})
        if {'forecast_file', 'ticker'}.issubset(prior):
            h = prior['horizon'] if 'horizon' in prior else pd.Series(1, index=prior.index)
            done.update(zip(prior.forecast_file, prior.ticker, h.astype(int)))
    rows = []
    for path in sorted(folder.glob('*.csv')):
        data = pd.read_csv(path, dtype={'ticker': str})
        if not {'ticker','data_last_observation_date','forecast_for_session_date','Ensemble'}.issubset(data):
            continue
        manifest = path.with_suffix('.json')
        if manifest.exists():
            meta = json.loads(manifest.read_text(encoding='utf-8'))
            if meta.get('csv_sha256') and meta['csv_sha256'] != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError(f'Changed forecast: {path.name}')
        ref = path.relative_to(ROOT).as_posix()
        for row in data.to_dict('records'):
            h = int(row.get('horizon_sessions', row.get('horizon', 1)))
            if (ref, row['ticker'], h) in done:
                continue
            origin, target = pd.Timestamp(row['data_last_observation_date']), pd.Timestamp(row['forecast_for_session_date'])
            if target not in eligible or target <= origin:
                continue
            a = prices.get((row['ticker'], origin))
            b = prices.get((row['ticker'], target))
            if a is None or b is None or not np.isfinite([a,b]).all() or a <= 0 or b <= 0:
                continue
            actual = float(b/a - 1)
            predicted = float(row['Ensemble'])
            rows.append(dict(market=market, forecast_file=ref, ticker=row['ticker'],
                model_version=row['model_version'], horizon=h, horizon_sessions=h,
                data_last_observation_date=origin.date().isoformat(),
                forecast_for_session_date=target.date().isoformat(), realized_session_date=target.date().isoformat(),
                close_at_forecast=row.get('close_at_forecast'),
                observation_close_same_vintage=float(a), realized_close=float(b),
                predicted_return=predicted, actual_return=actual, error=predicted-actual,
                direction_correct=int(np.sign(predicted)==np.sign(actual)),
                evaluated_at_utc=now.isoformat(), return_basis=RETURN_BASIS))
    if not rows:
        return None, None
    result = pd.DataFrame(rows)
    path = save_bundle(result, EVAL / market, f'evaluation_{market}_{MODEL_VERSION}',
                       dict(market=market, model_version=MODEL_VERSION, rows=len(result)))
    return result, path


def run(market, horizons=DEFAULT_HORIZONS, evaluate_only=False):
    horizons = tuple(sorted(set(horizons)))
    if not horizons or any(h not in DEFAULT_HORIZONS for h in horizons):
        raise ValueError('Choose horizons from 1, 5, 20')
    tickers = legacy.us_tickers() if market == 'US' else legacy.jp_tickers()
    px = legacy.download_prices(tickers)
    px['ticker'] = px.ticker.astype(str)
    px['date'] = pd.to_datetime(px.date).dt.tz_localize(None).dt.normalize()
    if px.duplicated(['ticker', 'date']).any():
        raise ValueError('Duplicate stock/date quotes')
    complete, calendar = completed_schedule(market, px.date.min())
    px = px[px.date.isin(complete.index) & np.isfinite(px.Close) & (px.Close > 0)].copy()
    if px.empty:
        raise RuntimeError('No quotes for completed exchange sessions')
    evaluation, path = evaluate_previous_forecasts(px, market)
    if path is not None:
        print(f'Evaluated {len(evaluation)} forecasts: {path}', flush=True)
    if evaluate_only:
        return
    cutoff = complete.index[-1]
    if px.date.max() != cutoff:
        raise RuntimeError('Latest completed session is missing; refusing a stale live forecast')
    if market == 'JP':
        from topix500_universe import snapshot_path, validate_snapshot
        snapshot = snapshot_path(cutoff.date())
        expected = set(validate_snapshot(snapshot).ticker.astype(str))
        ref = snapshot.relative_to(ROOT).as_posix()
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    else:
        expected = set(tickers)
        ref, digest = 'GitHub datasets S&P500 constituents (live)', ''
    observed = set(px.loc[px.date == cutoff, 'ticker']) & expected
    if len(observed) < int(np.ceil(.95 * len(expected))):
        raise RuntimeError(f'{market}: latest-session coverage {len(observed)}/{len(expected)} is below 95%')
    px = px[px.ticker.isin(expected)]
    metadata = dict(universe_name='TOPIX500' if market=='JP' else 'SP500',
                    universe_snapshot=ref, universe_snapshot_sha256=digest,
                    universe_expected_count=len(expected), universe_observed_count=len(observed))
    frame = legacy.features(px)
    # Missing features retain the legacy estimator's median-imputation policy.
    # Non-finite ratios carry no usable information and must not crash sklearn.
    frame[FEATURES] = frame[FEATURES].replace([np.inf, -np.inf], np.nan)
    failures = []
    for h in horizons:
        try:
            result, path = live_forecast(frame, market, calendar.index, h, metadata)
            if path is not None:
                print(f'Forecast saved: {path}', flush=True)
                print(result[['ticker','Ensemble']].head(TOP_N).to_string(index=False), flush=True)
        except Exception as exc:
            failures.append(f'h={h}: {type(exc).__name__}: {exc}')
            print(f'{market} {failures[-1]}', flush=True)
    if failures:
        raise RuntimeError('; '.join(failures))
