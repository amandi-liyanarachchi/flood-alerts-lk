"""Evaluation metrics.

Two decisions here shape every number reported, and both are stated in the thesis
because both are contestable:

1. PR-AUC IS THE HEADLINE, NOT ROC-AUC. Flood hours are under 1% of the record.
   ROC-AUC is dominated by the vast negative class and looks flattering for
   almost any model; precision-recall does not. ROC is reported alongside for
   comparability with the literature, not as the primary result.

2. LEAD TIME IS MEASURED PER EPISODE, NOT PER HOUR. A forecaster that warns four
   hours before a flood begins has delivered four hours of warning once, not once
   for every hour of the event. Counting per hour would inflate the figure by the
   duration of the flood.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def choose_threshold(y_true: np.ndarray, scores: np.ndarray, beta: float = 1.0) -> float:
    """Operating point maximising F-beta on the VALIDATION split.

    beta > 1 weights recall over precision. For flood warning a case can be made
    for beta = 2 -- a missed flood costs more than a false alarm -- but false
    alarms train people to ignore the app, which is a cost that compounds. The
    study reports beta = 1 and shows the full curve so the reader can pick their
    own point; in deployment that choice belongs to the Disaster Management
    Centre, not to the modeller.
    """
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    precision, recall = precision[:-1], recall[:-1]
    denominator = (beta ** 2 * precision) + recall
    with np.errstate(invalid="ignore", divide="ignore"):
        f_beta = np.where(denominator > 0,
                          (1 + beta ** 2) * precision * recall / np.maximum(denominator, 1e-12), 0)
    if not len(f_beta):
        return 0.5
    return float(thresholds[int(np.nanargmax(f_beta))])


def classification_metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    predicted = (scores >= threshold).astype(int)
    tp = int(((predicted == 1) & (y_true == 1)).sum())
    fp = int(((predicted == 1) & (y_true == 0)).sum())
    fn = int(((predicted == 0) & (y_true == 1)).sum())
    tn = int(((predicted == 0) & (y_true == 0)).sum())

    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall and (precision + recall) else float("nan"))

    return {
        "threshold": round(float(threshold), 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        # False alarm ratio: of the warnings issued, how many were wrong. The
        # operational literature quotes FAR rather than precision.
        "far": fp / (tp + fp) if (tp + fp) else float("nan"),
        # Probability of false detection: of the calm hours, how many were warned.
        "pofd": fp / (fp + tn) if (fp + tn) else float("nan"),
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)) if len(np.unique(y_true)) > 1 else float("nan"),
        "base_rate": float(y_true.mean()),
    }


def brier(y_true: np.ndarray, scores: np.ndarray) -> float:
    return float(brier_score_loss(y_true, np.clip(scores, 0, 1)))


def skill_score(model_metric: float, baseline_metric: float) -> float:
    """Fractional improvement over a baseline. Reported instead of a raw
    difference because PR-AUC differences are hard to interpret without the
    base rate in mind."""
    if baseline_metric in (0, None) or np.isnan(baseline_metric):
        return float("nan")
    return (model_metric - baseline_metric) / baseline_metric


# ---------------------------------------------------------------------------
# Lead time
# ---------------------------------------------------------------------------


def lead_times(test: pd.DataFrame, scores: np.ndarray, episodes: pd.DataFrame,
               threshold: float, max_lookback_h: int = 24) -> pd.DataFrame:
    """Hours of warning delivered before each flood episode began.

    For each episode, find the earliest hour within `max_lookback_h` before onset
    at which the model's score was already above its operating threshold, AND
    from which the warning held continuously to onset. The continuity condition
    matters: a single spurious spike 20 hours out is not 20 hours of warning, and
    counting it as such is how lead-time figures get inflated.
    """
    frame = test[["station", "hour"]].copy()
    frame["score"] = scores
    frame["warned"] = (frame["score"] >= threshold).astype(int)
    indexed = {s: g.set_index("hour")["warned"] for s, g in frame.groupby("station")}

    rows = []
    for episode in episodes.itertuples():
        series = indexed.get(episode.station)
        if series is None:
            continue
        window = series.loc[
            (series.index >= episode.onset - pd.Timedelta(hours=max_lookback_h))
            & (series.index < episode.onset)
        ]
        if window.empty:
            continue

        # Walk backwards from onset while the warning was continuously on.
        values = window.to_numpy()
        lead = 0
        for value in values[::-1]:
            if value == 1:
                lead += 1
            else:
                break

        rows.append({
            "station": episode.station,
            "basin": episode.basin,
            "onset": episode.onset,
            "detected": lead > 0,
            "lead_time_h": lead,
            "peak_level_m": episode.peak_level_m,
            "river_driven": getattr(episode, "river_driven", True),
            "local_only": getattr(episode, "local_only", False),
        })
    return pd.DataFrame(rows)


def lead_time_summary(table: pd.DataFrame) -> dict:
    """Overall, and split by flood mechanism.

    The split matters more than the headline. A riverine flood is preceded by
    hours of rising stage upstream and is genuinely forecastable; a flash urban
    flood is caused by a downpour landing on a drain whose condition nobody
    measures, and arrives within the hour. Averaging the two produces a lead-time
    figure that describes neither, and quietly overstates what the system can do
    for the flash-flood case that matters most in Colombo.
    """
    if table.empty:
        return {"episodes": 0}

    def block(frame: pd.DataFrame, prefix: str) -> dict:
        if frame.empty:
            return {f"{prefix}episodes": 0}
        found = frame[frame["detected"]]
        return {
            f"{prefix}episodes": int(len(frame)),
            f"{prefix}detected": int(len(found)),
            f"{prefix}detection_rate": float(len(found) / len(frame)),
            f"{prefix}median_lead_h": float(found["lead_time_h"].median()) if len(found) else float("nan"),
            f"{prefix}mean_lead_h": float(found["lead_time_h"].mean()) if len(found) else float("nan"),
            f"{prefix}p90_lead_h": float(found["lead_time_h"].quantile(0.9)) if len(found) else float("nan"),
        }

    summary = block(table, "")
    if "river_driven" in table:
        summary.update(block(table[table["river_driven"]], "river_"))
        summary.update(block(table[table["local_only"]], "local_"))
    return summary


# ---------------------------------------------------------------------------
# Curves and calibration
# ---------------------------------------------------------------------------


def pr_curve(y_true: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    precision, recall, _ = precision_recall_curve(y_true, scores)
    return recall, precision


def roc_points(y_true: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    fpr, tpr, _ = roc_curve(y_true, scores)
    return fpr, tpr


def calibration_bins(y_true: np.ndarray, scores: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Reliability diagram data, on quantile bins so each has comparable weight."""
    frame = pd.DataFrame({"y": y_true, "p": scores})
    try:
        frame["bin"] = pd.qcut(frame["p"], bins, duplicates="drop", labels=False)
    except ValueError:
        frame["bin"] = pd.cut(frame["p"], bins, labels=False)
    grouped = frame.groupby("bin").agg(
        predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size")
    ).reset_index(drop=True)
    return grouped


def bootstrap_ci(y_true: np.ndarray, scores: np.ndarray, metric=average_precision_score,
                 iterations: int = 300, seed: int = 20260831) -> tuple[float, float]:
    """Percentile bootstrap interval.

    Reported because a single PR-AUC on ~250 episodes carries real uncertainty,
    and a difference between two models that sits inside the intervals is not a
    result.
    """
    rng = np.random.default_rng(seed)
    n = len(y_true)
    values = []
    for _ in range(iterations):
        idx = rng.integers(0, n, n)
        if len(np.unique(y_true[idx])) < 2:
            continue
        values.append(metric(y_true[idx], scores[idx]))
    if not values:
        return float("nan"), float("nan")
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))
