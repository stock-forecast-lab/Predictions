from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
RAW = DATA / 'raw'
PRED = ROOT / 'predictions'
OUT = ROOT / 'output'
EVAL = OUT / 'evaluations'
for p in [DATA, RAW, PRED, OUT, EVAL]:
    p.mkdir(parents=True, exist_ok=True)

START = '2015-01-01'
MIN_HISTORY = 252
TOP_N = 10
RANDOM_STATE = 42
MODEL_VERSION = 'v1.1-actions'

# V1: public-data prototype. US universe is downloaded automatically.
# Japan: place a CSV at data/jp_tickers.csv with a column named ticker.
# Yahoo Japan tickers should look like 7203.T, 6758.T, etc.
