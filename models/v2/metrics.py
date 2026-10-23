"""Non-overlapping h-session diagnostic portfolios, 50% long and 50% short.
No claim of executable same-close trading. See README for price/execution limits.
"""
import numpy as np
import pandas as pd


def portfolio_path(z, horizon, cost_bps=10):
    previous, rows, equity, gross_equity = {}, [], 1., 1.
    for date, q in z.groupby('date',sort=True):
        q = q.dropna(subset=['actual','prediction']).sort_values(['prediction','ticker'])
        if len(q)<4:
            continue
        k = max(1,len(q)//10)
        weights = {}
        if q.prediction.nunique()>1:
            weights = {t:-.5/k for t in q.head(k).ticker}
            weights.update({t:.5/k for t in q.tail(k).ticker})
        turnover = sum(abs(weights.get(t,0)-previous.get(t,0)) for t in set(weights)|set(previous))
        realized = q.set_index('ticker').actual.to_dict()
        gross = sum(w*realized[t] for t,w in weights.items())
        cost = turnover*cost_bps/10000
        net = gross-cost
        if 1+net <= 0:
            raise RuntimeError('Diagnostic portfolio insolvent; simple leverage assumptions invalid')
        equity *= 1+net
        gross_equity *= 1+gross
        previous = {t:w*(1+realized[t])/(1+net) for t,w in weights.items()}
        rows.append(dict(date=date,gross_return=gross,net_return=net,turnover=turnover,
                         benchmark=q.actual.mean(),equity=equity,gross_equity=gross_equity))
    if rows:
        # Close all positions on final endpoint, including the liquidation cost.
        exit_turnover = sum(abs(w) for w in previous.values())
        # Terminal weights are relative to post-interval equity.
        rows[-1]['net_return'] = (1+rows[-1]['net_return'])*(1-exit_turnover*cost_bps/10000)-1
        rows[-1]['equity'] *= 1-exit_turnover*cost_bps/10000
        rows[-1]['turnover'] += exit_turnover
    return pd.DataFrame(rows)


def summarize(predictions, cost_bps=10):
    rows, paths = [], []
    group = ['market','horizon','information_set','model']
    for key,z in predictions.groupby(group):
        z = z.dropna(subset=['actual','prediction','historical_mean'])
        if z.empty:
            continue
        err = z.prediction-z.actual
        denominator = ((z.actual-z.historical_mean)**2).sum()
        ics = [q.prediction.corr(q.actual,method='spearman') for _,q in z.groupby('date')
               if q.prediction.nunique()>1 and q.actual.nunique()>1 and len(q)>2]
        row = dict(zip(group,key))
        row.update(n=len(z),RMSE=float(np.sqrt(np.mean(err**2))),MAE=float(np.mean(abs(err))),
                   OOS_R2=float(1-(err**2).sum()/denominator) if denominator>0 else np.nan,
                   DirectionAcc=float(np.mean(np.sign(z.prediction)==np.sign(z.actual))),
                   RankIC=float(np.mean(ics)) if ics else np.nan)
        p = portfolio_path(z,key[1],cost_bps)
        if not p.empty:
            factor = 252/key[1]
            std = p.net_return.std(ddof=1)
            row['Sharpe'] = float(p.net_return.mean()/std*np.sqrt(factor)) if std>0 else np.nan
            wealth = np.r_[1.,p.equity.to_numpy()]
            row['MaxDrawdown'] = float(np.min(wealth/np.maximum.accumulate(wealth)-1))
            row['LongShortMean'] = float(p.net_return.mean())
            row['MeanTurnover'] = float(p.turnover.mean())
            row['AlphaAnnualized'] = np.nan
            if len(p)>=12 and p.benchmark.std()>0:
                design = np.column_stack([np.ones(len(p)),p.benchmark])
                alpha, beta = np.linalg.lstsq(design,p.net_return,rcond=None)[0]
                row['AlphaAnnualized'] = float(alpha*factor)
                row['Beta'] = float(beta)
            for col,val in zip(group,key):
                p[col] = val
            paths.append(p)
        rows.append(row)
    return pd.DataFrame(rows),pd.concat(paths,ignore_index=True) if paths else pd.DataFrame()
