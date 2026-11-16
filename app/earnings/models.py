"""Fixed-configuration cross-company quantile trees; no parameter search."""
from copy import deepcopy
from dataclasses import dataclass
import random
from importlib.metadata import version
from .dataset import feature_registry


@dataclass(frozen=True)
class TreeConfig:
    feature_set: str = 'financials'
    iterations: int = 100
    learning_rate: float = .08
    max_leaf_nodes: int = 7
    min_samples_leaf: int = 20
    l2_regularization: float = 1
    seed: int = 1729


def runtime_versions():
    return {name:version(name) for name in ('scikit-learn','numpy','scipy','threadpoolctl','joblib')}


def feature_names(config):
    fields=list(feature_registry())
    return [f for f in fields if not f.endswith('_latest_to_scale')] if config.feature_set=='profit_history' else fields


def tree_factory(config=TreeConfig(), *, seasonal_residual=False):
    """Imports are confined to offline training, never the deployed HTTP runtime."""
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    from threadpoolctl import threadpool_limits
    fields=feature_names(config)
    def matrix(rows):
        return np.array([[r['features'][f] if r['features'][f] is not None else np.nan for f in fields]
                         for r in rows], dtype=float)
    def fit(rows):
        x=matrix(rows)
        y=np.array([(float(r['label']['value'])-(r['baselines']['seasonal_naive'] if seasonal_residual else 0))/r['scale'] for r in rows])
        models=[]
        with threadpool_limits(limits=1):
            for q in (.1,.5,.9):
                model=HistGradientBoostingRegressor(loss='quantile',quantile=q,max_iter=config.iterations,
                    learning_rate=config.learning_rate,max_leaf_nodes=config.max_leaf_nodes,
                    min_samples_leaf=config.min_samples_leaf,l2_regularization=config.l2_regularization,
                    max_bins=63,early_stopping=False,random_state=config.seed)
                model.fit(x,y)
                models.append(model)
        def predict(row):
            with threadpool_limits(limits=1):
                values=sorted(float(m.predict(matrix([row]))[0]) for m in models)
            name='quantile_tree_'+config.feature_set
            if seasonal_residual:
                offset=row['baselines']['seasonal_naive']/row['scale']
                values=[value+offset for value in values]
                name='seasonal_residual_'+name
            return {name:dict(point=values[1]*row['scale'],raw_lower=values[0],raw_upper=values[2])}
        return predict
    return fit


def shuffled_label_factory(factory, seed=1729):
    """Negative control: permute normalized TRAINING labels within each temporal fold."""
    def fit(rows):
        shuffled=deepcopy(rows)
        labels=[float(r['label']['value'])/r['scale'] for r in rows]
        random.Random(seed).shuffle(labels)
        for row,value in zip(shuffled,labels):row['label']['value']=str(value*row['scale'])
        return factory(shuffled)
    return fit
