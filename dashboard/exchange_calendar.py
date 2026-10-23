from datetime import datetime, timezone
import pandas as pd
import pandas_market_calendars as mcal

def schedule(market, start, end):
    return mcal.get_calendar('NYSE' if market=='US' else 'JPX').schedule(start_date=start,end_date=end)


def completed_schedule(market, start, now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    cal = schedule(market,start,(now + pd.Timedelta(days=60)).date())
    complete = cal[cal.market_close <= now]
    if complete.empty:
        raise RuntimeError('No completed sessions')
    return complete, cal

