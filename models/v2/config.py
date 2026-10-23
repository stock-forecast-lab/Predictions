from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
START = '2015-01-01'
VERSION = 'v2.0.0'
HORIZONS = (1, 5, 20)
MIN_HISTORY = 252
SEED = 42
# Every value is obtained from its ALFRED real-time history, not today's revised series.
COMMON = {'fx':'DEXJPUS', 'us2y':'DGS2', 'us10y':'DGS10',
          'credit':'BAA10Y', 'vix':'VIXCLS'}
MACRO = {
    'US': {'us_cpi_yoy':'CPIAUCSL', 'us_ip_yoy':'INDPRO', 'us_unemployment':'UNRATE', 'us_short_rate':'FEDFUNDS'},
    # Use global US inflation explicitly. The old FRED Japan CPI index ends in
    # 2021 and must not masquerade as a current domestic inflation series.
    'JP': {'us_cpi_yoy':'CPIAUCSL', 'jp_ip_yoy':'JPNPRINTO01GYSAM',
           'jp_unemployment':'LRHUTTTTJPM156S', 'jp_short_rate':'IRSTCI01JPM156N',
           'jp10y':'IRLTLT01JPM156N'},
}
YOY_FROM_LEVEL = {'CPIAUCSL','INDPRO'}
PERCENT_TO_FRACTION = {'JPNPRINTO01GYSAM'}
# No exact original release time in ALFRED: wait two full calendar days from vintage date.
VINTAGE_DELAY_DAYS = 2
