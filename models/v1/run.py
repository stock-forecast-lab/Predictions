"""Run the unchanged V1.4 estimator; also score/read legacy root-level logs."""
import argparse
import main as v1
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


def duplicate_in_either(market,date):
    try:
        for folder in [PRED,ROOT/'predictions']:
            v1.PRED=folder
            if _original_duplicate(market,date): return True
        return False
    finally:
        v1.PRED=PRED


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market',choices=['JP','US'],required=True)
    args=parser.parse_args()
    v1.evaluate_previous_forecasts=evaluate_both
    v1.already_forecast=duplicate_in_either
    v1.run(args.market)
