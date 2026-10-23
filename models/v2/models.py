from time import perf_counter
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.dummy import DummyRegressor
from config import SEED


def profile_settings(profile='standard'):
    if profile not in {'standard', 'light'}:
        raise ValueError(f'Unknown profile: {profile}')
    return ({'rf_trees': 80, 'rf_depth': 12, 'xgb_trees': 120,
             'xgb_depth': 4, 'xgb_tree_method': 'hist'} if profile == 'light' else
            {'rf_trees': 250, 'rf_depth': None, 'xgb_trees': 300,
             'xgb_depth': 4, 'xgb_tree_method': 'auto'})


def build_models(names=None, quick=False, profile='standard'):
    params = profile_settings(profile)
    scale = lambda m: make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),m)
    choices = {
        'HistoricalMean':lambda:DummyRegressor(strategy='mean'),
        'OLS':lambda:scale(LinearRegression()),
        'Ridge':lambda:scale(Ridge(alpha=10.0)),
        'Lasso':lambda:scale(Lasso(alpha=1e-4,max_iter=10000)),
        'ElasticNet':lambda:scale(ElasticNet(alpha=1e-4,l1_ratio=.5,max_iter=10000)),
        'RF':lambda:make_pipeline(SimpleImputer(strategy='median'),RandomForestRegressor(
            n_estimators=10 if quick else params['rf_trees'],max_depth=params['rf_depth'],min_samples_leaf=20,max_features='sqrt',n_jobs=1 if quick else -1,random_state=SEED)),
    }
    names = names or list(choices) + ['XGB']
    if 'XGB' in names:
        try:
            from xgboost import XGBRegressor
        except ImportError:
            raise RuntimeError('XGBoost is required for the selected models; install requirements.txt') from None
        choices['XGB'] = lambda:make_pipeline(SimpleImputer(strategy='median'),XGBRegressor(
            n_estimators=10 if quick else params['xgb_trees'],max_depth=params['xgb_depth'],tree_method=params['xgb_tree_method'],learning_rate=.03,subsample=.8,
            colsample_bytree=.8,objective='reg:squarederror',n_jobs=1 if quick else -1,random_state=SEED))
    unknown = set(names)-set(choices)
    if unknown:
        raise ValueError(f'Unknown models: {unknown}')
    return {n:choices[n]() for n in names}


def predict(tr, te, columns, target, names=None, quick=False, profile='standard'):
    result = {}
    for name, model in build_models(names,quick,profile).items():
        start = perf_counter()
        print(f'  {name}: training {len(tr):,} rows, {len(columns)} features, profile={profile}',flush=True)
        model.fit(tr[columns],tr[target])
        result[name] = model.predict(te[columns])
        print(f'  {name}: finished in {perf_counter()-start:.1f}s',flush=True)
    members = [v for n,v in result.items() if n != 'HistoricalMean']
    if members:
        result['Ensemble'] = np.mean(members, axis=0)
    return result
