import numpy as np
import pandas as pd

MARKET_FEATURES = ['ret1','ret5','ret20','ret60','vol20','vol60',
                   'vol_z20','hl20','ma20_gap','ma60_gap','mkt_ret1','rel_ret20']


def make_panel(px, sessions, horizons=(1,5,20)):
    px = px.copy()
    px['date'] = pd.to_datetime(px.date).dt.normalize()
    if px.duplicated(['ticker','date']).any():
        raise ValueError('Duplicate ticker/session price rows')
    if (px.Close.dropna() <= 0).any():
        raise ValueError('Prices must be positive')
    sessions = pd.DatetimeIndex(sessions).tz_localize(None).normalize()
    parts = []
    for ticker, raw in px.groupby('ticker', sort=True):
        # Calendar alignment is essential: a suspension must not turn h sessions
        # into h available quotes. No forward-filling of stock prices.
        x = raw.set_index('date').reindex(sessions)
        x['ticker'] = ticker
        x.index.name = 'date'
        for n in (1,5,20,60):
            x[f'ret{n}'] = x.Close.pct_change(n, fill_method=None)
        for n in (20,60):
            x[f'vol{n}'] = x.ret1.rolling(n).std()
            x[f'ma{n}_gap'] = x.Close / x.Close.rolling(n).mean() - 1
        lv = np.log1p(x.Volume)
        x['vol_z20'] = (lv - lv.rolling(20).mean()) / lv.rolling(20).std().replace(0,np.nan)
        x['hl20'] = ((x.High-x.Low)/x.Close).rolling(20).mean()
        date_series = pd.Series(sessions, index=sessions)
        for h in horizons:
            x[f'target_{h}'] = x.Close.shift(-h)/x.Close - 1
            x[f'label_end_{h}'] = date_series.shift(-h)
        parts.append(x.reset_index())
    panel = pd.concat(parts, ignore_index=True)
    mkt = panel.groupby('date').ret1.mean()
    panel = panel.join(mkt.rename('mkt_ret1'), on='date')
    panel = panel.join(((1+mkt).rolling(20).apply(np.prod,raw=True)-1).rename('mkt20'),on='date')
    panel['rel_ret20'] = panel.ret20-panel.mkt20
    panel[MARKET_FEATURES] = panel[MARKET_FEATURES].replace([np.inf,-np.inf],np.nan)
    # Existing prices after IPO/suspension only; preserve precomputed calendar labels.
    return panel.dropna(subset=['Close']).reset_index(drop=True)


def training_rows(panel, cutoff, horizon, min_history=252, feature_columns=None):
    # Strictly before the forecast close. A 20-session outcome not yet observed
    # cannot enter a training set even if its feature date is in the past.
    tr = panel[(panel.date < cutoff) & (panel[f'label_end_{horizon}'] < cutoff) &
               panel[f'target_{horizon}'].notna()].copy()
    if feature_columns:
        tr = tr.dropna(subset=feature_columns)
    if tr.date.nunique() < min_history:
        raise RuntimeError(f'Insufficient matured training history for horizon {horizon}')
    return tr
