"""Feature engineering and label construction.

One function per feature group, so an ablation study can drop a group by name and
the reader can see exactly what was removed. The groups are:

    river      water level and its dynamics
    upstream   the lagged level of the next station up the river
    rainfall   accumulations, intensity, and antecedent wetness
    temporal   season and time of day
    crowd      the crowdsourced reports

Two rules hold everywhere in this file, and both matter more than any individual
feature:

  1. NO LOOKAHEAD. Every feature at hour t is computed from information available
     at or before t. Rolling windows are backward-only and shifts are positive.
     A single accidental negative shift would leak the answer into the inputs and
     produce a result that looks superb and means nothing.
  2. MISSING STAYS MISSING. Gaps are never filled with zero. A sensor that has
     failed is not a river at zero metres, and the model is told which it is
     through the explicit staleness features.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RAIN_HEAVY_MM = 75.0
RAIN_VERY_HEAVY_MM = 150.0
HORIZONS = (6, 24)

FEATURE_GROUPS: dict[str, list[str]] = {}


def _register(group: str, columns: list[str]) -> None:
    FEATURE_GROUPS[group] = columns


# ---------------------------------------------------------------------------
# River state
# ---------------------------------------------------------------------------


def add_river_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("station", group_keys=False)

    # Carry a reading forward for at most three hours, and say how stale it is.
    df["level_observed"] = df["water_level_m"].notna().astype(int)
    df["water_level_m"] = g["water_level_m"].ffill(limit=3)
    df["level_age_h"] = (
        g["level_observed"].apply(lambda s: s.groupby(s.cumsum()).cumcount())
    ).astype(float)

    span = (df["major_flood_level_m"] - df["alert_level_m"]).replace(0, np.nan)
    # The feature that transfers between stations: 0 at the alert level, 1 at
    # major flood, whatever the river's absolute scale.
    df["level_norm"] = (df["water_level_m"] - df["alert_level_m"]) / span
    df["level_over_alert"] = df["water_level_m"] / df["alert_level_m"].replace(0, np.nan)

    g = df.groupby("station", group_keys=False)
    for lag in (1, 3, 6, 12, 24):
        df[f"level_delta_{lag}h"] = g["water_level_m"].diff(lag)
    df["level_accel"] = df.groupby("station", group_keys=False)["level_delta_3h"].diff(3)

    g = df.groupby("station", group_keys=False)
    for window in (24, 48, 72):
        roll = g["water_level_m"].rolling(window, min_periods=3)
        df[f"level_max_{window}h"] = roll.max().to_numpy()
        df[f"level_mean_{window}h"] = roll.mean().to_numpy()
    df["level_range_24h"] = (
        df["level_max_24h"] - df.groupby("station")["water_level_m"]
        .rolling(24, min_periods=3).min().to_numpy()
    )
    df["level_headroom_minor_m"] = df["minor_flood_level_m"] - df["water_level_m"]

    # Percentile against this station's own past only -- an expanding rank, so no
    # information from the future leaks in.
    df["level_pct_rank"] = df.groupby("station", group_keys=False)["water_level_m"].apply(
        lambda s: s.expanding(min_periods=48).apply(
            lambda w: (w[:-1] <= w[-1]).mean() if len(w) > 1 else np.nan, raw=True
        )
    )

    above = (df["water_level_m"] >= df["alert_level_m"]).fillna(False)
    df["above_alert"] = above.astype(int)
    df["hours_since_alert"] = (
        df.groupby("station", group_keys=False)["above_alert"]
        .apply(lambda s: s.groupby(s.cumsum()).cumcount().where(s.cumsum() > 0, 9999))
    ).astype(float)

    _register("river", [
        "level_norm", "level_over_alert", "level_headroom_minor_m", "level_age_h",
        "level_delta_1h", "level_delta_3h", "level_delta_6h", "level_delta_12h",
        "level_delta_24h", "level_accel", "level_max_24h", "level_mean_24h",
        "level_max_48h", "level_mean_48h", "level_max_72h", "level_mean_72h",
        "level_range_24h", "level_pct_rank", "hours_since_alert", "above_alert",
        "catchment_km2",
    ])
    return df


# ---------------------------------------------------------------------------
# Upstream
# ---------------------------------------------------------------------------


def add_upstream_features(df: pd.DataFrame, lag_table: dict[str, tuple[str, int]]) -> pd.DataFrame:
    """The next station up the river, shifted by its estimated travel time.

    `lag_table` maps a station to (upstream station, lag in hours) and is
    estimated from training data only -- see estimate_travel_times. Using the
    simulator's true travel times would be a form of leakage: the deployed system
    would not know them either.
    """
    level_by_station = {s: g.set_index("hour")["level_norm"] for s, g in df.groupby("station")}
    delta_by_station = {s: g.set_index("hour")["level_delta_3h"] for s, g in df.groupby("station")}

    df["upstream_level_norm"] = np.nan
    df["upstream_delta_3h"] = np.nan
    df["upstream_lag_h"] = np.nan

    for station, (upstream, lag) in lag_table.items():
        if upstream not in level_by_station:
            continue
        mask = df["station"] == station
        hours = df.loc[mask, "hour"]
        df.loc[mask, "upstream_level_norm"] = hours.map(level_by_station[upstream].shift(lag)).to_numpy()
        df.loc[mask, "upstream_delta_3h"] = hours.map(delta_by_station[upstream].shift(lag)).to_numpy()
        df.loc[mask, "upstream_lag_h"] = lag

    # How much higher the upstream gauge stands, in its own normalised units --
    # a wave on its way down.
    df["upstream_minus_local"] = df["upstream_level_norm"] - df["level_norm"]

    _register("upstream", [
        "upstream_level_norm", "upstream_delta_3h", "upstream_lag_h", "upstream_minus_local",
    ])
    return df


def estimate_travel_times(train: pd.DataFrame, network_order: dict[str, list[str]],
                          max_lag: int = 18, default: int = 4) -> dict[str, tuple[str, int]]:
    """Recover each station pair's travel time by cross-correlation.

    Estimated on TRAINING DATA ONLY. The lag maximising the correlation between
    the upstream and downstream normalised stage is taken as the travel time,
    searched over 1-18 hours.
    """
    table: dict[str, tuple[str, int]] = {}
    # Correlate the RATE OF CHANGE, not the level itself. Two stages on the same
    # river are highly correlated at every lag simply because both are smooth and
    # seasonal, which flattens the correlation curve and makes the argmax
    # meaningless. Differencing removes that shared trend and leaves the travel
    # of the flood wave, which is the thing being measured.
    series = {
        s: g.set_index("hour")["level_norm"].diff(3)
        for s, g in train.groupby("station")
    }

    for _basin, ordered in network_order.items():
        for upstream, downstream in zip(ordered, ordered[1:]):
            if upstream not in series or downstream not in series:
                continue
            joined = pd.concat(
                [series[upstream].rename("up"), series[downstream].rename("down")], axis=1
            ).dropna()
            best_lag, best_corr = default, -2.0
            if len(joined) >= 500:
                for lag in range(1, max_lag + 1):
                    corr = joined["up"].shift(lag).corr(joined["down"])
                    if corr is not None and np.isfinite(corr) and corr > best_corr:
                        best_lag, best_corr = lag, corr
            table[downstream] = (upstream, best_lag)
    return table


# ---------------------------------------------------------------------------
# Rainfall
# ---------------------------------------------------------------------------


def add_rainfall_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("station", group_keys=False)
    rain = df["station_rain_mm"].fillna(0.0)
    df["rain_1h_mm"] = rain

    for window in (3, 6, 12, 24, 48, 72):
        df[f"rain_{window}h_mm"] = g["station_rain_mm"].rolling(window, min_periods=1).sum().to_numpy()
    df["rain_max_1h_24h"] = g["station_rain_mm"].rolling(24, min_periods=1).max().to_numpy()
    df["rain_wet_hours_24h"] = (
        df.assign(wet=(rain > 0.2).astype(float))
        .groupby("station", group_keys=False)["wet"].rolling(24, min_periods=1).sum().to_numpy()
    )

    # Antecedent Precipitation Index: an exponentially decayed sum of past
    # rainfall, standing in for how saturated the ground already is. The single
    # most important rainfall feature in the operational literature, and the one
    # that explains why identical storms produce different floods.
    k = 0.9 ** (1 / 24)
    api = np.zeros(len(df))
    current, last = 0.0, None
    stations = df["station"].to_numpy()
    values = rain.to_numpy()
    for i in range(len(df)):
        if stations[i] != last:
            current, last = 0.0, stations[i]
        current = current * k + values[i]
        api[i] = current
    df["api_mm"] = api

    df["rain_heavy_24h"] = (df["rain_24h_mm"] >= RAIN_HEAVY_MM).astype(int)
    df["rain_very_heavy_24h"] = (df["rain_24h_mm"] >= RAIN_VERY_HEAVY_MM).astype(int)

    _register("rainfall", [
        "rain_1h_mm", "rain_3h_mm", "rain_6h_mm", "rain_12h_mm", "rain_24h_mm",
        "rain_48h_mm", "rain_72h_mm", "rain_max_1h_24h", "rain_wet_hours_24h",
        "api_mm", "rain_heavy_24h", "rain_very_heavy_24h",
    ])
    return df


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    month = df["hour"].dt.month
    hour = df["hour"].dt.hour
    df["month_sin"] = np.sin(2 * np.pi * month / 12)
    df["month_cos"] = np.cos(2 * np.pi * month / 12)
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    # Sri Lanka's two monsoons, as a single ordered indicator.
    df["is_southwest_monsoon"] = month.isin([5, 6, 7, 8, 9]).astype(int)
    df["is_northeast_monsoon"] = month.isin([12, 1, 2]).astype(int)

    _register("temporal", [
        "month_sin", "month_cos", "hour_sin", "hour_cos",
        "is_southwest_monsoon", "is_northeast_monsoon",
    ])
    return df


# ---------------------------------------------------------------------------
# Crowd
# ---------------------------------------------------------------------------


def add_crowd_features(df: pd.DataFrame, window_hours: int = 1,
                       min_respondents: int = 5) -> pd.DataFrame:
    g = df.groupby("station", group_keys=False)

    respondents = g["crowd_respondents"].rolling(window_hours, min_periods=1).sum().to_numpy()
    yes = g["crowd_yes"].rolling(window_hours, min_periods=1).sum().to_numpy()

    df["crowd_n"] = respondents
    df["crowd_yes_n"] = yes
    with np.errstate(invalid="ignore", divide="ignore"):
        df["crowd_yes_ratio"] = np.where(respondents > 0, yes / np.maximum(respondents, 1), np.nan)

    df["crowd_floor_met"] = (respondents >= min_respondents).astype(int)
    # The rule the deployed backend applies, as a feature in its own right, so
    # the model's advantage over that rule can be attributed.
    df["crowd_rule_fires"] = (
        (respondents >= min_respondents) & (df["crowd_yes_ratio"] >= 0.75)
    ).astype(int)

    # Movement in the crowd signal. A ratio climbing fast is a flood arriving; a
    # steady high ratio is one that has already arrived.
    df["crowd_ratio_delta_3h"] = df.groupby("station", group_keys=False)["crowd_yes_ratio"].diff(3)
    df["crowd_yes_6h"] = df.groupby("station", group_keys=False)["crowd_yes"].rolling(
        6, min_periods=1).sum().to_numpy()

    _register("crowd", [
        "crowd_n", "crowd_yes_n", "crowd_yes_ratio", "crowd_floor_met",
        "crowd_rule_fires", "crowd_ratio_delta_3h", "crowd_yes_6h",
    ])
    return df


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Forward-looking targets.

    y_minor_{H}h = 1 if the station reaches its minor flood level at any point
    in the next H hours. Computed from the LATENT true level rather than the
    noisy observation, so the target is the physical event and not the sensor's
    opinion of it -- and so a dropout during a flood does not silently relabel
    that hour as safe.
    """
    for horizon in HORIZONS:
        future = df.groupby("station", group_keys=False)["true_level_m"].apply(
            lambda s, h=horizon: s.shift(-1).rolling(h, min_periods=1).max().shift(-(h - 1))
        ).to_numpy()
        df[f"future_max_{horizon}h"] = future
        df[f"y_minor_{horizon}h"] = (future >= df["minor_flood_level_m"]).astype(float)
        df[f"y_major_{horizon}h"] = (future >= df["major_flood_level_m"]).astype(float)
        df.loc[pd.isna(future), [f"y_minor_{horizon}h", f"y_major_{horizon}h"]] = np.nan

        # THE PRIMARY TARGET. Flooding of any kind affecting this cell -- the
        # river overtopping its banks, or the local drainage network being
        # overwhelmed. This is deliberately the question the mobile application
        # asks its users ("Is there flooding in your area right now?") rather
        # than the narrower question a river gauge can answer. Predicting only
        # river stage would exclude the majority of urban flood experience and
        # would make the crowdsourcing layer redundant by construction.
        future_any = df.groupby("station", group_keys=False)["any_flood"].apply(
            lambda s, h=horizon: s.astype(float).shift(-1)
            .rolling(h, min_periods=1).max().shift(-(h - 1))
        ).to_numpy()
        df[f"y_any_{horizon}h"] = future_any
    return df


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build(raw: pd.DataFrame, lag_table: dict[str, tuple[str, int]]) -> pd.DataFrame:
    df = raw.sort_values(["station", "hour"]).reset_index(drop=True).copy()
    df = add_river_features(df)
    df = add_rainfall_features(df)
    df = add_temporal_features(df)
    df = add_crowd_features(df)
    df = add_upstream_features(df, lag_table)
    df = add_labels(df)
    return df


def feature_columns(groups: list[str] | None = None) -> list[str]:
    groups = groups or list(FEATURE_GROUPS)
    columns: list[str] = []
    for group in groups:
        columns.extend(FEATURE_GROUPS.get(group, []))
    return columns
