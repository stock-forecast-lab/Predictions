"""Adjusted return labels plus contemporaneous unadjusted valuation prices."""
import pandas as pd
import yfinance as yf

def prices(tickers,start,last):
    frames=[]
    for offset in range(0,len(tickers),50):
        batch=tickers[offset:offset+50]
        raw=yf.download(batch,start=start,end=(pd.Timestamp(last)+pd.Timedelta(days=1)).date().isoformat(),
                        auto_adjust=False,actions=True,repair=False,group_by='ticker',threads=True,progress=False)
        if len(batch)==1 and not isinstance(raw.columns,pd.MultiIndex):
            raw.columns=pd.MultiIndex.from_product([batch,raw.columns])
        for ticker in batch:
            if ticker not in raw.columns.get_level_values(0):continue
            x=raw[ticker].copy()
            if not {'Close','Adj Close','Open','High','Low','Volume','Stock Splits'}.issubset(x):
                raise RuntimeError('V3 requires both raw and adjusted Yahoo prices: '+ticker)
            factor=x['Adj Close']/x.Close
            # Yahoo historical Close already reflects later stock splits.
            # Undo only those splits after each row to reconstruct its quoted
            # price. Using Close directly would leak future splits into historic
            # capitalization while shares are still on their original basis.
            ratios=x['Stock Splits'].fillna(0).replace(0,1)
            future_splits=ratios.iloc[::-1].cumprod().iloc[::-1]/ratios
            x['RawClose']=x.Close*future_splits
            for c in ['Open','High','Low','Close']:x[c]=x[c]*factor
            x['Split']=x.get('Stock Splits',0.)
            x['date']=pd.to_datetime(x.index).tz_localize(None).normalize();x['ticker']=ticker
            frames.append(x[['date','ticker','Open','High','Low','Close','Volume','RawClose','Split']].dropna(subset=['Close']))
    if not frames:raise RuntimeError('No V3 prices downloaded')
    return pd.concat(frames,ignore_index=True)
