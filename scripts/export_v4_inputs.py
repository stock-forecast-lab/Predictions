"""Export V3's publication-time panel for V4. Run in the V1--V3 repository."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish_panel(destination, market, calendar, quotes, panel, columns, tickers,
                  metadata, synthetic=False):
    """Publish compressed CSV shards and a last-written hash manifest.

    The caller supplies the as-of joins. Session close is a conservative upper
    bound on row availability because prices are closing prices and V3 admits
    financial/macro observations only after their own availability timestamps.
    """
    destination = Path(destination)
    if not columns or len(set(columns)) != len(columns):
        raise ValueError('Explicit unique feature columns required')
    if len(set(tickers)) != len(tickers) or not tickers:
        raise ValueError('Explicit unique universe required')
    if any(c.startswith('target') or c.startswith('label_end') for c in columns):
        raise ValueError('Future outcomes cannot be input features')
    renaming = {c: c if c.startswith('f_') else 'f_' + c for c in columns}
    if len(set(renaming.values())) != len(columns):
        raise ValueError('Feature renaming collision')
    sessions = pd.DataFrame(dict(date=calendar.index.strftime('%Y-%m-%d'),
                                 close_at_utc=pd.to_datetime(calendar.market_close, utc=True).to_numpy()))
    if sessions.date.duplicated().any() or not sessions.date.is_monotonic_increasing:
        raise ValueError('Invalid exchange calendar')
    prices = quotes[['date', 'ticker', 'Close']].rename(columns={'Close': 'adjusted_close'}).copy()
    features = panel[['date', 'ticker', *columns]].rename(columns=renaming).copy()
    for frame in [prices, features]:
        frame['date'] = pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d')
        frame['ticker'] = frame.ticker.astype(str)
        if frame.duplicated(['ticker', 'date']).any():
            raise ValueError('Duplicate ticker/date')
        if not set(frame.date).issubset(set(sessions.date)):
            raise ValueError('Non-session input row')
    if not np.isfinite(prices.adjusted_close).all() or (prices.adjusted_close <= 0).any():
        raise ValueError('Invalid adjusted price')
    fcols = list(renaming.values())
    if np.isinf(features[fcols].to_numpy(float)).any() or features.empty:
        raise ValueError('Empty/infinite features')
    if features[fcols].notna().sum(axis=1).eq(0).any():
        raise ValueError('All features missing')
    closes = sessions.set_index('date').close_at_utc
    features['available_at_utc'] = features.date.map(closes)
    if features.available_at_utc.isna().any():
        raise ValueError('Missing row availability bound')
    features = features[['date', 'ticker', 'available_at_utc', *fcols]]
    features = features.sort_values(['ticker', 'date']).reset_index(drop=True)
    prices = prices.sort_values(['ticker', 'date']).reset_index(drop=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.v4-build-') as tmp:
        tmp = Path(tmp)
        files, hashes = {}, {}
        for name, frame in [('sessions', sessions), ('prices', prices), ('features', features)]:
            files[name] = []
            for part, start in enumerate(range(0, len(frame), 50000)):
                filename = f'{name}-{part:05d}.csv.gz'
                path = tmp / filename
                frame.iloc[start:start + 50000].to_csv(
                    path, index=False, compression={'method': 'gzip', 'mtime': 0})
                if path.stat().st_size >= 90 * 1024 * 1024:
                    raise RuntimeError('Input shard exceeds safe Git file size')
                files[name].append(filename)
                hashes[filename] = sha256(path)
        manifest = dict(schema_version=1, market=market, return_basis='adjusted_close_ratio',
                        point_in_time=True, synthetic=synthetic,
                        source='V3_publication_time_export_v1', feature_columns=fcols,
                        expected_tickers=list(tickers), files=files, sha256=hashes,
                        export_run_id=os.getenv('GITHUB_RUN_ID', 'local'),
                        availability_policy='V3 as-of joins; daily rows available by actual exchange close',
                        **metadata)
        (tmp / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False))
        destination.mkdir(parents=True, exist_ok=True)
        old = json.loads((destination / 'manifest.json').read_text()) if (destination / 'manifest.json').exists() else {}
        for path in tmp.iterdir():
            if path.name != 'manifest.json':
                os.replace(path, destination / path.name)
        os.replace(tmp / 'manifest.json', destination / 'manifest.json')
        # Remove only previously declared payload files, never an arbitrary path.
        for group in old.get('files', {}).values():
            for name in group:
                if Path(name).name == name and name not in hashes and name.endswith('.csv.gz'):
                    (destination / name).unlink(missing_ok=True)
    return manifest


def build(market, root=ROOT, base_set='M3', max_age_days=550):
    # Imports intentionally occur here so the format writer can be verified offline.
    from models.v3 import bridge
    from models.v3.config import FINANCIAL_FEATURES, VERSION, PARSER_VERSION
    from models.v3.financial_features import attach_financials
    from models.v3.prices import prices
    from models.v3.storage import load_facts

    root = Path(root)
    complete, calendar = bridge.market.completed_schedule(market, bridge.config.START)
    cutoff = complete.index[-1]
    tickers, membership = bridge.universe(market, root, cutoff.date().isoformat())
    facts = load_facts(root, market, set(tickers))
    if facts.empty:
        raise RuntimeError(f'{market}: V3 financial archive is not ready. Run V3 forecast/sync first.')
    ctx, m2, m3, evidence = bridge.context.context_features(
        complete.market_close, market, [base_set],
        (pd.Timestamp(bridge.config.START) - pd.DateOffset(years=2)).date().isoformat(),
        pd.Timestamp.now(tz='UTC').date().isoformat(), root / 'data/context_cache')
    columns = bridge.features.MARKET_FEATURES + (m2 if base_set in {'M2', 'M3'} else []) + (m3 if base_set == 'M3' else [])
    previous = root / 'v4_inputs' / market
    retained = set(tickers)
    if (previous / 'manifest.json').exists():
        meta = json.loads((previous / 'manifest.json').read_text())
        for name in meta.get('files', {}).get('prices', []):
            if Path(name).name != name or not name.startswith('prices-'):
                raise ValueError('Unsafe previous price payload')
            path = previous / name
            if sha256(path) != meta['sha256'][name]:
                raise ValueError('Previous input integrity failure')
            retained.update(pd.read_csv(path, usecols=['ticker'], dtype=str).ticker)
    quotes = prices(sorted(retained), bridge.config.START, cutoff)
    quotes = quotes[quotes.date.isin(complete.index)]
    panel = bridge.features.make_panel(quotes[quotes.ticker.isin(tickers)], complete.index, (1, 5, 20))
    panel = panel.merge(ctx, on='date', how='left', validate='many_to_one')
    panel = attach_financials(panel, facts, complete.market_close, max_age_days)
    # Keep V3's financial information set. Missing optional financial items use
    # V3's indicators; never backfill facts before their availability date.
    panel = panel[panel.fin_available].dropna(subset=columns).copy()
    columns += FINANCIAL_FEATURES + [c + '_missing' for c in FINANCIAL_FEATURES]
    if panel.empty:
        raise RuntimeError(f'{market}: no V3 rows with point-in-time financial coverage yet')
    coverage = panel.loc[panel.date == cutoff, 'ticker'].nunique() / len(tickers)
    manifest = publish_panel(previous, market, calendar, quotes, panel, columns, tickers,
        dict(data_last_observation_date=cutoff.date().isoformat(),
             v3_version=VERSION, parser_version=PARSER_VERSION, base_set=base_set,
             train_sessions=2520, current_financial_coverage=coverage,
             universe=membership, macro_sources=evidence,
             financial_facts_sha256=hashlib.sha256(facts.sort_values(
                 ['ticker', 'available_at_utc', 'metric', 'filing_id']).to_csv(index=False).encode()).hexdigest()))
    print(f'{market}: input exported; {len(panel):,} rows, {len(columns)} features, '
          f'current financial coverage {coverage:.1%}. V4 also checks matured history and 95% coverage.', flush=True)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--market', type=str.upper, choices=['JP', 'US', 'BOTH'], default='BOTH')
    p.add_argument('--base-set', choices=['M1', 'M2', 'M3'], default='M3')
    p.add_argument('--max-age-days', type=int, default=550)
    args = p.parse_args()
    if args.max_age_days <= 0:
        p.error('--max-age-days must be positive')
    failures = []
    for market in (['JP', 'US'] if args.market == 'BOTH' else [args.market]):
        try:
            build(market, base_set=args.base_set, max_age_days=args.max_age_days)
        except (ValueError, RuntimeError) as exc:
            failures.append(market)
            print(f'{market}: {exc}', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
