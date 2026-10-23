"""Load unchanged V2 engines without confusing V1/V2/V3 absolute imports."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
V2 = Path(__file__).resolve().parents[1] / 'v2'

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, V2/filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

@contextmanager
def aliases(**modules):
    before = {k:sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for k,v in before.items():
            if v is None: sys.modules.pop(k,None)
            else: sys.modules[k]=v

config = load('_v3_v2_config','config.py')
with aliases(config=config):
    estimators = load('_v3_v2_estimators','models.py')
    context = load('_v3_v2_context','context_data.py')
features = load('_v3_v2_features','features.py')
market = load('_v3_v2_market','market_data.py')
topix = load('_v3_v2_topix','topix500_universe.py')

def universe(*args):
    with aliases(topix500_universe=topix):
        return market.universe(*args)
