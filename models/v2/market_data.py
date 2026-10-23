from datetime import datetime, timezone
import hashlib
from pathlib import Path
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf


def schedule(market, start, end):
    return mcal.get_calendar('NYSE' if market=='US' else 'JPX').schedule(start_date=start,end_date=end)


def completed_schedule(market, start, now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    cal = schedule(market,start,(now + pd.Timedelta(days=60)).date())
    complete = cal[cal.market_close <= now]
    if complete.empty:
        raise RuntimeError('No completed sessions')
    return complete, cal


def universe(market, root, as_of):
    if market == 'JP':
        from topix500_universe import ensure_snapshot
        path, frame = ensure_snapshot(pd.Timestamp(as_of).date())
        # The inherited JPX snapshot is intentionally bounded. It is not a perpetual
        # membership feed. Newer official classifications must be explicitly added.
        if pd.Timestamp(as_of) >= pd.Timestamp('2027-10-01'):
            raise RuntimeError('Refresh TOPIX500 membership source before October 2027')
        ticks = frame.ticker.tolist()
        content = path.read_bytes()
    else:
        url = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
        frame = pd.read_csv(url)
        ticks = sorted(set(frame.Symbol.dropna().str.replace('.','-',regex=False)))
        if not 450 <= len(ticks) <= 550:
            raise RuntimeError(f'Unexpected S&P500 size: {len(ticks)}')
        content = pd.DataFrame({'ticker':ticks}).to_csv(index=False).encode()
    folder = Path(root)/'data/universes'/market
    folder.mkdir(parents=True,exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    dest = folder/f'{as_of}_{digest[:12]}.csv'
    if not dest.exists():
        dest.write_bytes(content)
    return ticks, {'name':'TOPIX500' if market=='JP' else 'SP500',
                   'snapshot':str(dest.relative_to(root)), 'sha256':digest,'count':len(ticks)}


def prices(tickers, start, last):
    frames = []
    for offset in range(0,len(tickers),50):
        batch = tickers[offset:offset+50]
        raw = yf.download(batch,start=start,end=(pd.Timestamp(last)+pd.Timedelta(days=1)).date().isoformat(),
                          auto_adjust=True,repair=True,group_by='ticker',threads=True,progress=False)
        if len(batch)==1 and not isinstance(raw.columns,pd.MultiIndex):
            raw.columns = pd.MultiIndex.from_product([batch,raw.columns])
        for ticker in batch:
            if ticker not in raw.columns.get_level_values(0):
                continue
            x = raw[ticker][['Open','High','Low','Close','Volume']].dropna(subset=['Close']).copy()
            x['date'] = pd.to_datetime(x.index).tz_localize(None).normalize()
            x['ticker'] = ticker
            frames.append(x.reset_index(drop=True))
    if not frames:
        raise RuntimeError('No prices downloaded')
    return pd.concat(frames,ignore_index=True)
