from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data'
RAW = DATA / 'raw'
PRED = ROOT / 'predictions' / 'v1'
OUT = ROOT / 'output'
EVAL = ROOT / 'evaluations' / 'v1'
for p in [DATA, RAW, PRED, OUT, EVAL]:
    p.mkdir(parents=True, exist_ok=True)

START = '2015-01-01'
MIN_HISTORY = 252
TOP_N = 10
RANDOM_STATE = 42
MODEL_VERSION = 'v1.4-topix500-membership'

# Japan: verified official JPX TOPIX 500 component snapshot, fetched via
# topix500_universe.py and stored under data/universes/JP/.
# The original five-name CSV is no longer used for production forecasts.
