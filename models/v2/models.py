import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.dummy import DummyRegressor
from config import SEED


def build_models(names=None, quick=False):
    scale = lambda m: make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),m)
    choices = {
        'HistoricalMean':lambda:DummyRegressor(strategy='mean'),
        'OLS':lambda:scale(LinearRegression()),
        'Ridge':lambda:scale(Ridge(alpha=10.0)),
        'Lasso':lambda:scale(Lasso(alpha=1e-4,max_iter=10000)),
        'ElasticNet':lambda:scale(ElasticNet(alpha=1e-4,l1_ratio=.5,max_iter=10000)),
        'RF':lambda:make_pipeline(SimpleImputer(strategy='median'),RandomForestRegressor(
            n_estimators=10 if quick else 250,min_samples_leaf=20,max_features='sqrt',n_jobs=1 if quick else -1,random_state=SEED)),
    }
    names = names or list(choices) + ['XGB']
    if 'XGB' in names:
        try:
            from xgboost import XGBRegressor
        except ImportError:
            raise RuntimeError('XGBoost is required for the selected models; install requirements.txt') from None
        choices['XGB'] = lambda:make_pipeline(SimpleImputer(strategy='median'),XGBRegressor(
            n_estimators=10 if quick else 300,max_depth=4,learning_rate=.03,subsample=.8,
            colsample_bytree=.8,objective='reg:squarederror',n_jobs=1 if quick else -1,random_state=SEED))
    unknown = set(names)-set(choices)
    if unknown:
        raise ValueError(f'Unknown models: {unknown}')
    return {n:choices[n]() for n in names}


def predict(tr, te, columns, target, names=None, quick=False):
    result = {}
    for name, model in build_models(names,quick).items():
        model.fit(tr[columns],tr[target])
        result[name] = model.predict(te[columns])
    members = [v for n,v in result.items() if n != 'HistoricalMean']
    if members:
        result['Ensemble'] = np.mean(members, axis=0)
    return result
