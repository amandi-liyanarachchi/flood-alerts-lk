"""Models and baselines.

Four systems are compared, all on identical inputs and identical splits:

    B1  Rainfall threshold      the operational counterfactual -- roughly what a
                                person with a weather app already has
    B2  Persistence             warn if the river is already above its alert
                                level; a strong baseline that many published
                                flood models quietly fail to beat
    M1  Physical                gradient boosting on river, upstream, rainfall
                                and temporal features
    M2  Physical + crowd        M1 plus the crowdsourced features

M2 minus M1 is the contribution of crowdsourcing, measured on identical data.
Both baselines are included because a model that beats a random guess is not
interesting; a model that beats persistence is.

WHY GRADIENT BOOSTING. The design matrix is tabular, mixed-type, contains
missing values by construction, and has strong non-linear thresholds in it (a
river does almost nothing until it reaches its bank). Gradient-boosted trees
handle all four natively and remain the strongest family on tabular data at this
sample size. A recurrent network was not used: with roughly 300,000 rows and
~250 flood episodes it would be badly under-determined, and the explicit lag and
rolling features already encode the temporal structure an RNN would have to
learn.

CALIBRATION. Raw boosting scores are not probabilities. An operator deciding
whether to approve a public warning needs "0.7" to mean "this happens about 70%
of the time", so every model is wrapped in isotonic calibration fitted on the
validation split.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier

RANDOM_SEED = 20260831

# Kept modest on purpose. Flood episodes are few; a deeper forest would memorise
# individual events, and the temporal split would not reveal it because the same
# storm can span the boundary of a season.
GBM_PARAMS = dict(
    max_iter=400,
    learning_rate=0.06,
    max_depth=6,
    min_samples_leaf=120,
    l2_regularization=1.0,
    early_stopping=True,
    validation_fraction=0.15,
    n_iter_no_change=30,
    random_state=RANDOM_SEED,
)


@dataclass
class Prediction:
    name: str
    scores: np.ndarray            # calibrated probability, or a rule's 0/1
    is_probabilistic: bool = True


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def baseline_rainfall_threshold(df: pd.DataFrame, threshold_mm: float = 75.0) -> Prediction:
    """B1. Fires when 24-hour rainfall reaches the Department of Meteorology's
    'heavy rain' threshold. Scored continuously (rain / threshold, capped) so it
    can be given a precision-recall curve rather than a single point."""
    ratio = (df["rain_24h_mm"].fillna(0.0) / threshold_mm).clip(0, 2) / 2
    return Prediction("B1 Rainfall threshold", ratio.to_numpy(), is_probabilistic=False)


def baseline_persistence(df: pd.DataFrame) -> Prediction:
    """B2. Fires on how far the river already stands above its alert level.

    The baseline that matters. Rivers are autocorrelated: a gauge above its alert
    level now is quite likely to be at minor flood in six hours, with no model at
    all. Any claimed skill has to be measured against this.
    """
    span = (df["major_flood_level_m"] - df["alert_level_m"]).replace(0, np.nan)
    score = ((df["water_level_m"] - df["alert_level_m"]) / span).clip(0, 1).fillna(0.0)
    return Prediction("B2 Persistence", score.to_numpy(), is_probabilistic=False)


# ---------------------------------------------------------------------------
# Learned models
# ---------------------------------------------------------------------------


class FloodClassifier:
    """Gradient boosting plus isotonic calibration."""

    def __init__(self, name: str, features: list[str], class_weight: str | None = "balanced"):
        self.name = name
        self.features = features
        self.class_weight = class_weight
        self.model: CalibratedClassifierCV | None = None

    def fit(self, train: pd.DataFrame, target: str,
            validation: pd.DataFrame | None = None) -> "FloodClassifier":
        X, y = self._matrix(train, target)

        base = HistGradientBoostingClassifier(
            class_weight=self.class_weight, **GBM_PARAMS
        )
        base.fit(X, y)

        # Calibrate on the VALIDATION split, never on training data -- calibrating
        # on data the model has already fitted produces a confident liar.
        if validation is not None and len(validation):
            Xv, yv = self._matrix(validation, target)
            if len(np.unique(yv)) > 1:
                # Calibrate the ALREADY-FITTED model. scikit-learn >= 1.6 spells
                # this with FrozenEstimator; older versions used cv="prefit".
                try:
                    from sklearn.frozen import FrozenEstimator

                    wrapped = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
                except ImportError:  # pragma: no cover
                    wrapped = CalibratedClassifierCV(base, method="isotonic", cv="prefit")
                wrapped.fit(Xv, yv)
                self.model = wrapped
            else:
                self.model = base
        else:
            self.model = base
        return self

    def predict(self, df: pd.DataFrame) -> Prediction:
        X = df[self.features].to_numpy(dtype=float)
        scores = self.model.predict_proba(X)[:, 1]
        return Prediction(self.name, scores, is_probabilistic=True)

    def _matrix(self, df: pd.DataFrame, target: str) -> tuple[np.ndarray, np.ndarray]:
        usable = df[df[target].notna()]
        return usable[self.features].to_numpy(dtype=float), usable[target].to_numpy(dtype=int)


def make_models(physical_features: list[str], crowd_features: list[str]) -> dict[str, FloodClassifier]:
    return {
        "M1 Physical": FloodClassifier("M1 Physical", physical_features),
        "M2 Physical + crowd": FloodClassifier(
            "M2 Physical + crowd", physical_features + crowd_features
        ),
    }
