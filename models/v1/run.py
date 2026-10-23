"""Run V1 direct 1/5/20-session forecasts; also score legacy root-level logs."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import multihorizon as v1
from config import ROOT, PRED, EVAL

_original_evaluate=v1.evaluate_previous_forecasts
_original_duplicate=v1.already_forecast


def evaluate_both(px,market):
    result=(None,None)
    try:
        for predictions,evaluations in [(PRED,EVAL),(ROOT/'predictions',ROOT/'output/evaluations')]:
            v1.PRED,v1.EVAL=predictions,evaluations
            current=_original_evaluate(px,market)
            if current[1] is not None: result=current
    finally:
        v1.PRED,v1.EVAL=PRED,EVAL
    return result


def duplicate_in_either(market,date,*args,**kwargs):
    try:
        for folder in [PRED,ROOT/'predictions']:
            v1.PRED=folder
            if _original_duplicate(market,date,*args,**kwargs): return True
        return False
    finally:
        v1.PRED=PRED


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market',choices=['JP','US','BOTH'],required=True)
    parser.add_argument('--horizons',nargs='+',type=int,choices=[1,5,20],default=[1,5,20])
    parser.add_argument('--evaluate-only',action='store_true')
    args=parser.parse_args()
    v1.evaluate_previous_forecasts=evaluate_both
    v1.already_forecast=duplicate_in_either
    failed=[]
    for market in (['JP','US'] if args.market=='BOTH' else [args.market]):
        try:
            v1.run(market,horizons=args.horizons,evaluate_only=args.evaluate_only)
        except Exception as exc:
            print(f'{market}: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
            failed.append(market)
    if failed:
        raise SystemExit('V1 failed markets: '+', '.join(failed))
