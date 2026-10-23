"""As-of financial state; no backfill across disclosure dates or restatements."""
import numpy as np
import pandas as pd
from .config import FINANCIAL_FEATURES


def annualized(row):
    if row is None or not row.get('start'): return np.nan
    days=(pd.Timestamp(row['end'])-pd.Timestamp(row['start'])).days+1
    return row['value']*365.25/days if 60<=days<=400 else np.nan


def snapshot(state,available):
    rows=list(state.values())
    def latest(metric,scope=None,end=None,exact=False):
        candidates=[r for r in rows if r['metric']==metric and (scope is None or r['scope']==scope)
            and (end is None or (r['end']==end if exact else r['end']<=end))]
        return max(candidates,key=lambda r:(r['end'],r['available_at_utc'],
            (pd.Timestamp(r['end'])-pd.Timestamp(r['start'])).days if r.get('start') else 0,
            r['scope']=='consolidated',-r.get('priority',0))) if candidates else None
    assets=latest('assets')
    if assets is None or assets['value']<=0: return None
    scope=assets['scope'];end=assets['end'];unit=assets['unit']
    def monetary(metric,exact=False):
        r=latest(metric,scope,end,exact)
        return r if r is not None and r['unit']==unit else None
    income=monetary('income')
    equity=monetary('equity',True)
    revenue=monetary('revenue')
    cfo=monetary('cfo')
    liabilities=monetary('liabilities',True)
    if income is None: return None
    a=assets['value'];i=annualized(income);e=equity['value'] if equity else np.nan
    out={k:np.nan for k in FINANCIAL_FEATURES}
    out.update(fin_log_assets=np.log1p(a),fin_roa_annualized=i/a,
               fin_roe_annualized=i/e if e>0 else np.nan,fin_equity_ratio=e/a,
               fin_liabilities_ratio=(liabilities['value']/a if liabilities else (a-e)/a),
               fin_cashflow_assets_annualized=annualized(cfo)/a)
    if revenue and revenue['value']!=0 and revenue['start']==income['start'] and revenue['end']==income['end']:
        out['fin_margin']=income['value']/revenue['value']
    previous=None
    if revenue:
        period_days=(pd.Timestamp(revenue['end'])-pd.Timestamp(revenue['start'])).days
        prior=[r for r in rows if r['metric']=='revenue' and r['scope']==scope and r['unit']==unit
               and r.get('start') and abs((pd.Timestamp(revenue['end'])-pd.Timestamp(r['end'])).days-365)<=15
               and abs((pd.Timestamp(r['end'])-pd.Timestamp(r['start'])).days-period_days)<=15]
        if prior:
            previous=max(prior,key=lambda r:r['available_at_utc'])
            if previous['value']>0:out['fin_revenue_growth']=revenue['value']/previous['value']-1
    treasury=None
    shares=latest('shares',scope,end)
    if shares is None:
        issued=latest('issued_shares',scope,end)
        treasury=latest('treasury_shares',scope,issued['end'],True) if issued else None
        if issued and treasury and issued['value']>treasury['value']>=0:
            shares=dict(issued,value=issued['value']-treasury['value'])
    out.update(fin_event_at=available,fin_scope=scope,fin_currency=unit,
        fin_assets_end=end,fin_income_end=income['end'],fin_income_annualized=i,
        fin_equity=e,fin_shares=shares['value'] if shares else np.nan,
        fin_shares_end=shares['end'] if shares else '',
        fin_valuation_allowed=bool(shares and shares.get('valuation_allowed',False)),
        fin_source_ids='|'.join(sorted({r['filing_id'] for r in [assets,income,equity,revenue,cfo,shares,liabilities,treasury,previous] if r})))
    return out


def events(facts):
    if facts.empty:return pd.DataFrame()
    f=facts.copy();f['available_at_utc']=pd.to_datetime(f.available_at_utc,utc=True)
    output=[]
    for ticker,group in f.groupby('ticker',sort=True):
        state={}
        for stamp,batch in group.sort_values(['available_at_utc','filing_id','priority']).groupby('available_at_utc',sort=True):
            updated={}
            for r in batch.to_dict('records'):
                key=tuple(r[k] for k in ['metric','start','end','scope','unit'])
                if key in updated and updated[key]['value']!=r['value']:
                    raise RuntimeError(f'Ambiguous simultaneous financial disclosures: {ticker} {stamp}')
                updated[key]=r
            state.update(updated)
            row=snapshot(state,stamp)
            if row is not None:output.append(dict(row,ticker=ticker))
    return pd.DataFrame(output)


def attach_financials(panel,facts,cutoffs,max_age_days=550):
    out=[];ev=events(facts)
    for ticker,z in panel.groupby('ticker',sort=False):
        z=z.sort_values('date').copy()
        z['cutoff']=pd.to_datetime(z.date.map(cutoffs),utc=True)
        r=ev[ev.ticker==ticker].drop(columns=['ticker']) if not ev.empty else pd.DataFrame()
        if r.empty:
            for c in FINANCIAL_FEATURES:z[c]=np.nan
            z['fin_available']=False;out.append(z);continue
        z=pd.merge_asof(z,r.sort_values('fin_event_at'),left_on='cutoff',right_on='fin_event_at',direction='backward')
        z['fin_age_days']=np.maximum((z.date-pd.to_datetime(z.fin_assets_end)).dt.days,
                                    (z.date-pd.to_datetime(z.fin_income_end)).dt.days)
        z['fin_available']=z.fin_event_at.notna() & z.fin_age_days.between(0,max_age_days)
        # Shares must be aligned to their measurement date, using only splits
        # that have already occurred by each feature date. Future splits cannot
        # change old valuation features. Unknown/out-of-range share bases fail closed.
        split=z.get('Split',pd.Series(0.,index=z.index)).fillna(0).replace(0,1).cumprod()
        share_dates=pd.to_datetime(z.fin_shares_end,errors='coerce')
        lookup=pd.DataFrame({'date':z.date,'cum':split.to_numpy()})
        base=pd.merge_asof(pd.DataFrame({'date':share_dates,'row':np.arange(len(z))}).dropna().sort_values('date'),
                           lookup,on='date',direction='backward').set_index('row')['cum']
        base=base.reindex(range(len(z))).to_numpy()
        # If the share measurement predates the downloaded actions, splits are unknown.
        valid=(z.fin_valuation_allowed.fillna(False).astype(bool) & (z.fin_shares>0) &
               (share_dates>=z.date.min()) & (share_dates<=z.date) & np.isfinite(base))
        shares=z.fin_shares*split.to_numpy()/base
        cap=(z.RawClose*shares).where(valid)
        out_income=z.fin_income_annualized
        out_equity=z.fin_equity
        out_eps=out_income/shares
        out_per=(cap/out_income).where(out_income>0)
        out_pbr=(cap/out_equity).where(out_equity>0)
        z['fin_log_market_cap_estimate']=np.log1p(cap.where(cap>0))
        z['fin_earnings_yield']=(out_income/cap).where(cap>0)
        z['fin_book_to_market']=(out_equity/cap).where(cap>0)
        z['fin_log_per']=np.log1p(out_per.where(out_per>0))
        z['fin_log_pbr']=np.log1p(out_pbr.where(out_pbr>0))
        z['fin_log_abs_eps']=np.log1p(out_eps.abs()).where(valid)
        z['fin_eps_negative']=(out_eps<0).astype(float).where(valid)
        z.loc[~z.fin_available,FINANCIAL_FEATURES]=np.nan
        out.append(z)
    result=pd.concat(out,ignore_index=True)
    for c in FINANCIAL_FEATURES:
        result[c]=pd.to_numeric(result[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
        result[c+'_missing']=result[c].isna().astype(float)
    return result
