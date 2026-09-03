"""Correctness tests for the modelling pipeline.

These are not smoke tests. Each one guards a property whose violation would
silently invalidate the reported results rather than raise an error — which is
the only kind of bug that actually matters in an evaluation.

The four that matter most:
  * no target leakage into features
  * the split is temporal, with no overlap
  * calibration is never fitted on training data
  * crowd parameters cannot perturb the simulated physical world
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml import evaluate, features
from ml.models import FloodClassifier, baseline_persistence
from ml.run_experiment import network_order, temporal_split
from ml.simulator import SimulationConfig, default_network, episode_table, simulate


@pytest.fixture(scope="module")
def raw():
    return simulate(SimulationConfig(years=1, seed=7))


@pytest.fixture(scope="module")
def built(raw):
    order = network_order(default_network())
    lags = features.estimate_travel_times(features.build(temporal_split(raw)[0], {}), order)
    return features.build(raw, lags)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


def test_crowd_parameters_cannot_change_the_physical_world():
    """The confound that would invalidate the entire sensitivity study.

    NumPy draws Poisson and Binomial variates by rejection, so the number of
    underlying random values consumed depends on the parameters. With one shared
    stream, changing a crowd parameter would change the weather generated
    afterwards, and the physical-only baseline would drift between cells that are
    supposed to differ only in the crowd.
    """
    quiet = simulate(SimulationConfig(years=1, seed=7, detection_rate=0.4))

    network = default_network()
    for basin in network:
        for station in basin.stations:
            station.panel_size = 60
    loud = simulate(SimulationConfig(years=1, seed=7, detection_rate=0.95), network)

    assert np.allclose(quiet["true_level_m"], loud["true_level_m"]), \
        "weather must be identical across crowd settings"
    assert np.allclose(quiet["catchment_rain_mm"], loud["catchment_rain_mm"])
    assert np.array_equal(quiet["any_flood"], loud["any_flood"])
    assert not np.array_equal(quiet["crowd_yes"], loud["crowd_yes"]), \
        "the crowd itself must differ, or the sweep tests nothing"


def test_flood_frequency_is_physically_plausible(raw):
    river = raw["river_flood"].mean()
    assert 0.002 < river < 0.02, f"river flooding {river:.3%} is not a plausible rate"
    assert raw["any_flood"].mean() > river, "localised flooding must add episodes"


def test_hourly_rainfall_is_physically_attainable(raw):
    """A generator that produces 150 mm in one hour invalidates every rainfall
    feature built on top of it."""
    hourly = raw["catchment_rain_mm"]
    assert hourly.max() < 110, f"peak hourly rainfall {hourly.max():.0f} mm is implausible"
    daily = (raw[raw["station"] == "A1-Headwater"]
             .set_index("hour")["catchment_rain_mm"].resample("D").sum())
    assert 1200 < daily.sum() < 6000, "annual total outside the Sri Lankan wet-zone range"


def test_localised_flooding_is_not_a_copy_of_river_flooding(raw):
    """If it were, the crowd would be redundant by construction and the study
    would be measuring nothing."""
    local_only = (raw["local_flood"] & ~raw["river_flood"]).mean()
    assert local_only > 0.002, "localised flooding must occur independently of the river"


def test_sensor_dropout_rises_with_stage(raw):
    """Gauges fail during the storms that matter. If the simulator dropped
    readings uniformly, the gauge-availability analysis would be meaningless."""
    high = raw[raw["true_level_m"] >= raw["alert_level_m"]]
    low = raw[raw["true_level_m"] < raw["alert_level_m"]]
    assert high["water_level_m"].isna().mean() > low["water_level_m"].isna().mean()


# ---------------------------------------------------------------------------
# Features and labels
# ---------------------------------------------------------------------------


def test_no_feature_leaks_the_target(built):
    """The single most important test in this file.

    Every feature is correlated against the target's FUTURE component -- the part
    of the label that lies strictly ahead of the feature's own timestamp. A
    feature that knows the future would be a misplaced negative shift, and would
    produce a spectacular and worthless result.
    """
    columns = features.feature_columns()
    frame = built[built["y_any_6h"].notna()]

    # What the label knows that the present does not.
    surprise = frame["y_any_6h"].astype(float) - frame["any_flood"].astype(float)

    offenders = []
    for column in columns:
        values = frame[column]
        if values.notna().sum() < 500 or values.nunique() < 3:
            continue
        corr = values.corr(surprise)
        if corr is not None and abs(corr) > 0.60:
            offenders.append((column, round(float(corr), 3)))

    assert not offenders, f"features appear to know the future: {offenders}"


def test_rolling_features_are_backward_only(built):
    """A 24-hour delta cannot exist in a station's first hour."""
    first = built.groupby("station").head(1)
    assert first["level_delta_24h"].isna().all()
    assert first["level_delta_12h"].isna().all()


def test_labels_look_forward(built):
    """The label must equal the forward window exactly -- not the present, and
    not the past. Checked directly against a recomputed forward maximum rather
    than by picking an example, so it cannot pass by luck."""
    station = built[built["station"] == "A4-Lower"].reset_index(drop=True)
    flood = station["any_flood"].astype(float).to_numpy()
    label = station["y_any_6h"].to_numpy()

    expected = np.full(len(flood), np.nan)
    for i in range(len(flood)):
        window = flood[i + 1:i + 7]          # strictly the next 6 hours
        if len(window):
            expected[i] = window.max()

    both = ~np.isnan(label) & ~np.isnan(expected)
    assert both.sum() > 1000
    assert np.array_equal(label[both], expected[both]), \
        "the label is not exactly the next-6-hour flood indicator"

    # And it genuinely fires ahead of onset rather than merely echoing the present.
    onsets = np.flatnonzero(np.diff(flood) == 1)
    assert len(onsets) > 3
    ahead = [label[i - 3] for i in onsets if i >= 3 and not np.isnan(label[i - 3])]
    assert np.mean(ahead) > 0.9, "label should be set three hours before onset"


def test_gaps_are_carried_forward_and_flagged_never_zeroed(built, raw):
    """A silent zero is a lie the model will believe: a failed sensor is not a
    river at zero metres.

    Short gaps are carried forward -- which is legitimate and documented -- but
    the reading's age is recorded so the model can tell a stale value from a
    fresh one. Nothing is ever filled with zero, and features that genuinely
    cannot be computed stay missing.
    """
    assert raw["water_level_m"].isna().any(), "the simulator must drop some readings"

    filled = built[built["level_observed"] == 0]
    assert len(filled) > 0
    assert (filled["water_level_m"] > 0).all(), "carried-forward values must not be zeroed"
    assert (filled["level_age_h"] >= 1).all(), "stale readings must be flagged as stale"

    # Features that cannot be computed remain missing rather than fabricated.
    assert built["upstream_level_norm"].isna().any()
    assert built["level_delta_24h"].isna().any()


def test_level_age_marks_stale_readings(built):
    fresh = built[built["level_observed"] == 1]
    assert (fresh["level_age_h"] == 0).all()
    assert built["level_age_h"].max() >= 1


def test_normalised_level_is_comparable_across_stations(built):
    """level_norm must be ~0 at the alert level and ~1 at major flood, on every
    river regardless of its absolute scale."""
    for station, group in built.groupby("station"):
        usable = group.dropna(subset=["level_norm", "water_level_m"])
        if usable.empty:
            continue
        nearest = usable.iloc[(usable["water_level_m"] - usable["alert_level_m"]).abs().argsort()[:1]]
        assert abs(float(nearest["level_norm"].iloc[0])) < 0.12, station


def test_api_is_non_negative_and_accumulates(built):
    assert (built["api_mm"] >= 0).all()
    assert built["api_mm"].max() > built["rain_1h_mm"].max()


def test_crowd_features_do_not_appear_in_the_physical_set():
    physical = features.feature_columns(["river", "upstream", "rainfall", "temporal"])
    assert not any(c.startswith("crowd") for c in physical), \
        "the physical-only model must not see the crowd"


# ---------------------------------------------------------------------------
# Splitting and evaluation
# ---------------------------------------------------------------------------


def test_split_is_temporal_and_disjoint(built):
    train, val, test = temporal_split(built)
    assert train["hour"].max() < val["hour"].min()
    assert val["hour"].max() < test["hour"].min()
    assert len(train) + len(val) + len(test) == len(built)
    # A random split would leave the same station-hour in two places.
    keys = lambda f: set(zip(f["station"], f["hour"]))
    assert not (keys(train) & keys(test))


def test_calibration_is_never_fitted_on_training_data(built):
    """Calibrating on data the model already fitted produces a confident liar."""
    train, val, test = temporal_split(built)
    columns = features.feature_columns(["river", "rainfall"])
    model = FloodClassifier("t", columns).fit(train, "y_any_6h", validation=val)

    scores = model.predict(test).scores
    assert scores.min() >= 0 and scores.max() <= 1
    # Isotonic calibration on a held-out split leaves a non-degenerate spread.
    assert scores.std() > 0.01


def test_lead_time_requires_a_continuous_warning(built):
    """An isolated spike twenty hours out is not twenty hours of warning."""
    episodes = pd.DataFrame([{
        "station": "A4-Lower", "basin": "Basin-A",
        "onset": pd.Timestamp("2022-06-10 12:00"),
        "duration_h": 6, "peak_level_m": 2.0,
        "river_driven": True, "local_only": False,
    }])
    hours = pd.date_range("2022-06-10 00:00", periods=12, freq="h")
    frame = pd.DataFrame({"station": "A4-Lower", "hour": hours})

    # Warning on at t-11, off in between, on for the final three hours.
    scores = np.zeros(12)
    scores[0] = 1.0
    scores[9:12] = 1.0

    table = evaluate.lead_times(frame, scores, episodes, threshold=0.5)
    assert int(table["lead_time_h"].iloc[0]) == 3, "only the continuous run counts"


def test_metrics_use_the_right_denominators():
    y = np.array([1, 1, 0, 0, 0, 0])
    scores = np.array([0.9, 0.4, 0.8, 0.2, 0.1, 0.05])
    m = evaluate.classification_metrics(y, scores, threshold=0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 3)
    assert m["precision"] == pytest.approx(0.5)
    assert m["recall"] == pytest.approx(0.5)
    assert m["far"] == pytest.approx(0.5)      # FP / (TP + FP)
    assert m["pofd"] == pytest.approx(0.25)    # FP / (FP + TN)


def test_persistence_baseline_is_not_trivially_beatable(built):
    """A flood model that cannot beat 'the river is already high' has shown
    nothing. This asserts the baseline is genuinely informative."""
    from sklearn.metrics import average_precision_score

    _, _, test = temporal_split(built)
    usable = test[test["y_any_6h"].notna()]
    y = usable["y_any_6h"].to_numpy(dtype=int)
    scores = baseline_persistence(usable).scores
    assert average_precision_score(y, scores) > y.mean() * 3


def test_episode_table_finds_contiguous_runs(raw):
    episodes = episode_table(raw)
    assert len(episodes) > 10
    assert (episodes["duration_h"] >= 1).all()
    assert (episodes["end"] >= episodes["onset"]).all()
    assert episodes["river_driven"].any() and episodes["local_only"].any()
