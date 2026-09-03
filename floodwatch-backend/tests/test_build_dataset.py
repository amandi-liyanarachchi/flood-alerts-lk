"""The dataset pipeline, end to end, against replayed upstream payloads.

The network is not available in CI and should not be needed to trust the feature
engineering. `MockSources` returns the exact shapes the real services return --
including the foot/metre mix and the epoch-millisecond timestamps -- over a
synthetic but hydrologically plausible history: a flood wave that starts at the
upstream station and arrives downstream some hours later.

That last detail is the point. If the upstream-lag estimator cannot recover a
lag it was explicitly given, it does not work.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from scripts import build_dataset

HOURS = 24 * 14          # two weeks of hourly history
TRUE_LAG_HOURS = 5       # planted travel time upstream -> downstream
START = datetime(2026, 5, 1)

STATIONS = [
    # name,            basin,          lat,      lon,     alert, minor, major, unit, elev
    ("Kithulgala",     "Kelani Ganga", 6.991259, 80.419213, 3.0,  4.0,  6.0,  "m",  120.0),
    ("Glencourse",     "Kelani Ganga", 6.976981, 80.194247, 15.0, 16.0, 19.0, "m",   30.0),
    ("Nagalagam St",   "Kelani Ganga", 6.958265, 79.878642, 4.0,  5.0,  7.0,  "ft",   1.0),
]


def _wave(hours: int, peak_at: int, width: float, height: float) -> np.ndarray:
    t = np.arange(hours)
    return height * np.exp(-((t - peak_at) ** 2) / (2 * width ** 2))


class MockSources:
    """Same interface as scripts.dataset_sources.Sources."""

    def __init__(self):
        self.calls = 0
        self.cache_hits = 0

    def close(self):
        pass

    def stations(self):
        out = []
        for name, basin, lat, lon, alert, minor, major, unit, elev in STATIONS:
            factor = 0.3048 if unit == "ft" else 1.0
            out.append({
                "station": name, "basin": basin, "tributary": basin,
                "latitude": lat, "longitude": lon,
                "alert_level_m": round(alert * factor, 4),
                "minor_flood_level_m": round(minor * factor, 4),
                "major_flood_level_m": round(major * factor, 4),
                "elevation_m": elev, "source_unit": unit,
            })
        return out

    def readings(self, max_pages=12, page_size=1000):
        rows = []
        for index, (name, basin, lat, lon, alert, minor, major, unit, _elev) in enumerate(STATIONS):
            # Upstream peaks first; each station downstream lags by TRUE_LAG_HOURS.
            peak = 200 + index * TRUE_LAG_HOURS
            # In the station's OWN published unit, so the pipeline must convert.
            baseline = alert * 0.45
            series = baseline + _wave(HOURS, peak, 14.0, major * 1.25 - baseline)
            rain = np.clip(_wave(HOURS, peak - 12, 18.0, 9.0), 0, None)

            for hour in range(HOURS):
                # Readings arrive every ~50 minutes, not on the hour.
                observed = START + timedelta(hours=hour, minutes=(hour * 50) % 60)
                rows.append({
                    "station": name,
                    "basin": basin,
                    "observed_at": observed.isoformat(),
                    "water_level_raw": round(float(series[hour]), 3),
                    "rainfall_mm": round(float(rain[hour]), 2),
                    "latitude": lat,
                    "longitude": lon,
                })
        return rows

    def flood_catalogue(self):
        return [
            {"event_name": "Kelani Ganga Flood 2016", "layer_id": 84,
             "gnd_name": "Kolonnawa", "gnd_no": "12A", "dsd_name": "Kolonnawa",
             "district": "COLOMBO", "province": "WESTERN", "admin_code": 1101010,
             "latitude": 6.93, "longitude": 79.89, "area_sqm": 1_200_000.0},
            {"event_name": "Nilwala Ganga Flood 2017", "layer_id": 79,
             "gnd_name": "Malimbada", "gnd_no": "230", "dsd_name": "Malimbada",
             "district": "MATARA", "province": "SOUTHERN", "admin_code": 3105005,
             "latitude": 5.98, "longitude": 80.51, "area_sqm": 800_000.0},
        ]

    def rainfall_hourly(self, latitude, longitude, start, end):
        span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
        stamps, values = [], []
        for hour in range(span * 24):
            moment = datetime.fromisoformat(start) + timedelta(hours=hour)
            stamps.append(moment.strftime("%Y-%m-%dT%H:%M"))
            values.append(round(float(max(0.0, math.sin(hour / 17) * 3.0)), 2))
        return {"hourly": {"time": stamps, "precipitation": values}}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dataset")
    sources = MockSources()
    data = build_dataset.extract(sources, out, want_rainfall=True,
                                 rainfall_days=30, max_pages=1)
    panel = build_dataset.build_station_hours(
        data["stations"], data["readings"], data["rainfall"], gauge_rain_cumulative=False
    )
    regions = build_dataset.build_region_hours(panel, data["stations"])
    crowd = build_dataset.build_crowd_scenarios(panel)
    return {"out": out, "data": data, "panel": panel, "regions": regions, "crowd": crowd}


# --- extraction -------------------------------------------------------------


def test_feet_are_converted_to_metres(built):
    stations = built["data"]["stations"].set_index("station")
    # 4 ft alert level -> 1.2192 m. Comparing a metre reading against "4" would
    # put central Colombo permanently in flood.
    assert stations.loc["Nagalagam St", "alert_level_m"] == pytest.approx(1.2192, abs=1e-3)
    assert stations.loc["Nagalagam St", "source_unit"] == "ft"
    assert stations.loc["Glencourse", "alert_level_m"] == 15.0


def test_readings_archive_accumulates_across_runs(built, tmp_path):
    from scripts.build_dataset import _merge_reading_archive

    archive = tmp_path / "archive.json"
    first = pd.DataFrame([{"station": "A", "observed_at": "2026-05-01T00:00:00", "water_level_raw": 1}])
    second = pd.DataFrame([{"station": "A", "observed_at": "2026-05-02T00:00:00", "water_level_raw": 2}])

    _merge_reading_archive(first, archive)
    merged = _merge_reading_archive(second, archive)
    # The upstream feed is a rolling window; this archive is not.
    assert len(merged) == 2

    again = _merge_reading_archive(second, archive)
    assert len(again) == 2, "re-running must not duplicate"


# --- panel construction -----------------------------------------------------


def test_panel_is_hourly_and_complete(built):
    panel = built["panel"]
    assert not panel.empty
    assert set(panel["station"]) == {s[0] for s in STATIONS}
    for _, group in panel.groupby("station"):
        gaps = group["hour"].diff().dropna().unique()
        assert all(pd.Timedelta(g) == pd.Timedelta(hours=1) for g in gaps)


def test_levels_are_in_metres_in_the_panel(built):
    panel = built["panel"]
    nagalagam = panel[panel["station"] == "Nagalagam St"]
    major = nagalagam["major_flood_level_m"].iloc[0]
    assert major == pytest.approx(7 * 0.3048, abs=1e-3)
    # The planted wave peaks above the major flood level, in metres.
    assert nagalagam["water_level_m"].max() > major


def test_normalised_level_is_comparable_between_stations(built):
    """level_norm is 0 at the alert level and 1 at major flood, everywhere."""
    panel = built["panel"].dropna(subset=["level_norm"])
    for station, group in panel.groupby("station"):
        at_alert = group.iloc[(group["water_level_m"] - group["alert_level_m"]).abs().argsort()[:1]]
        assert abs(float(at_alert["level_norm"].iloc[0])) < 0.08, station


def test_rate_of_change_and_acceleration_exist(built):
    panel = built["panel"]
    for column in ("level_delta_1h", "level_delta_3h", "level_delta_24h", "level_accel"):
        assert column in panel
        assert panel[column].notna().any()
    rising = panel[panel["level_delta_3h"] > 0]
    assert len(rising) > 0


def test_hours_since_alert_resets_on_exceedance(built):
    panel = built["panel"]
    flooded = panel[panel["station_above_alert"] == 1]
    assert len(flooded) > 0
    assert (flooded["hours_since_alert"] == 0).all()


def test_api_accumulates_and_decays(built):
    panel = built["panel"][built["panel"]["station"] == "Kithulgala"]
    api = panel["api_mm"].to_numpy()
    assert api.min() >= 0
    assert api.max() > panel["rain_mm"].max(), "API should exceed any single hour's rain"


def test_monsoon_season_is_labelled(built):
    # The synthetic history is in May, which is the south-west monsoon.
    assert set(built["panel"]["monsoon"]) == {"southwest"}


# --- the upstream feature ---------------------------------------------------


def test_upstream_lag_is_recovered_from_the_data(built):
    """The single most valuable engineered feature. If the estimator cannot
    recover a lag that was deliberately planted, it does not work."""
    panel = built["panel"]
    downstream = panel[panel["station"] == "Nagalagam St"]

    assert downstream["upstream_station"].iloc[0] == "Glencourse", "elevation ordering"
    assert downstream["upstream_lag_estimated"].iloc[0] == 1, "should be estimated, not defaulted"

    estimated = float(downstream["upstream_lag_hours"].iloc[0])
    assert abs(estimated - TRUE_LAG_HOURS) <= 2, f"recovered {estimated}h, planted {TRUE_LAG_HOURS}h"
    assert downstream["upstream_level_m"].notna().any()


def test_most_upstream_station_has_no_upstream(built):
    panel = built["panel"]
    top = panel[panel["station"] == "Kithulgala"]
    assert top["upstream_station"].isna().all()


# --- labels -----------------------------------------------------------------


def test_labels_look_forward_not_backward(built):
    """y_major_6h must be 1 BEFORE the peak, not after it."""
    panel = built["panel"]
    station = panel[panel["station"] == "Glencourse"].reset_index(drop=True)
    peak_index = station["water_level_m"].idxmax()

    before = station.loc[max(0, peak_index - 4), "y_major_6h"]
    assert before == 1, "the label must fire before the water arrives"

    long_after = station.loc[min(len(station) - 1, peak_index + 60), "y_major_6h"]
    assert long_after == 0, "the label must not still be set long after the peak"


def test_label_hierarchy_holds(built):
    """Anything reaching major flood level also reached minor and alert."""
    panel = built["panel"].dropna(subset=["y_major_6h", "y_minor_6h", "y_alert_6h"])
    major = panel["y_major_6h"].astype(float)
    minor = panel["y_minor_6h"].astype(float)
    alert = panel["y_alert_6h"].astype(float)
    assert ((major <= minor) | major.isna()).all()
    assert ((minor <= alert) | minor.isna()).all()


def test_both_horizons_present_and_longer_catches_more(built):
    panel = built["panel"]
    six = panel["y_minor_6h"].dropna().astype(float).sum()
    day = panel["y_minor_24h"].dropna().astype(float).sum()
    assert six > 0 and day > 0
    assert day >= six, "a 24 h horizon must catch at least as much as a 6 h one"


def test_regression_target_exists(built):
    panel = built["panel"]
    assert panel["y_rise_6h"].notna().any()
    assert (panel["y_rise_6h"].dropna() >= -0.001).any()


# --- regions ----------------------------------------------------------------


def test_region_panel_maps_cells_to_nearest_station(built):
    regions = built["regions"]
    assert not regions.empty
    assert regions["region"].str.len().eq(5).all(), "geohash precision 5"
    assert (regions["station_km"] <= 25.0).all()
    assert regions["gauge_confidence"].between(0.5, 1.0).all()
    assert regions["region"].nunique() > 10


# --- crowd simulation -------------------------------------------------------


def test_crowd_rows_are_all_flagged_as_simulated(built):
    crowd = built["crowd"]
    assert not crowd.empty
    assert (crowd["is_simulated"] == 1).all()
    simulated = [c for c in crowd.columns if c.startswith("sim_")]
    assert len(simulated) >= 6
    # Nothing simulated may leak into the real training table.
    assert not any(c.startswith("sim_") for c in built["panel"].columns)
    assert "is_simulated" not in built["panel"].columns


def test_crowd_sweep_covers_the_grid(built):
    crowd = built["crowd"]
    expected = (len(build_dataset.CROWD_PANEL_SIZES)
                * len(build_dataset.CROWD_DETECTION_RATES)
                * len(build_dataset.CROWD_FALSE_POSITIVE_RATES))
    assert crowd["scenario_id"].nunique() == expected


def test_a_better_crowd_detects_more(built):
    """The whole point of the sweep: reliability and panel size must move the
    detection rate, or the sensitivity study says nothing."""
    crowd = built["crowd"]
    flooding = crowd[crowd["truth_label"] == 1]

    weak = flooding[(flooding["sim_panel_size"] == 5)
                    & (flooding["sim_detection_rate"] == 0.5)]["sim_crosses_75pct"].mean()
    strong = flooding[(flooding["sim_panel_size"] == 50)
                      & (flooding["sim_detection_rate"] == 0.9)]["sim_crosses_75pct"].mean()
    assert strong > weak

    quiet = crowd[crowd["truth_label"] == 0]
    low_fp = quiet[quiet["sim_false_positive_rate"] == 0.02]["sim_crosses_75pct"].mean()
    high_fp = quiet[quiet["sim_false_positive_rate"] == 0.10]["sim_crosses_75pct"].mean()
    assert high_fp >= low_fp


def test_small_panels_rarely_meet_the_respondent_floor(built):
    """Confirms the pilot-size warning quantitatively rather than rhetorically."""
    crowd = built["crowd"]
    tiny = crowd[crowd["sim_panel_size"] == 5]["sim_floor_met"].mean()
    large = crowd[crowd["sim_panel_size"] == 50]["sim_floor_met"].mean()
    assert tiny < large
    assert large > 0.95


# --- splits and outputs -----------------------------------------------------


def test_split_is_temporal_and_ordered(built):
    splits = build_dataset.temporal_split(built["panel"])
    train_end = pd.Timestamp(splits["train"][1])
    val_end = pd.Timestamp(splits["validation"][1])
    test_end = pd.Timestamp(splits["test"][1])
    assert train_end <= val_end <= test_end
    assert splits["validation"][0] == splits["train"][1], "no gap, no overlap"
    assert "never random" in splits["method"]


def test_report_and_dictionary_are_written(built, tmp_path):
    out = tmp_path / "written"
    out.mkdir()
    splits = build_dataset.temporal_split(built["panel"])
    build_dataset.write_report(out, built["data"], built["panel"],
                               built["regions"], built["crowd"], splits)
    (out / "DATA_DICTIONARY.md").write_text(build_dataset.DICTIONARY)

    report = (out / "dataset_report.md").read_text()
    assert "Coverage" in report and "Label balance" in report
    assert "rolling window" in report, "the history-depth caveat must be stated"
    assert "Simulated, not observed" in report

    dictionary = (out / "DATA_DICTIONARY.md").read_text()
    for column in ("level_norm", "api_mm", "upstream_lag_hours", "y_major_6h", "sim_yes_ratio"):
        assert column in dictionary


def test_no_missing_values_are_silently_filled(built):
    """Unknown must stay null. A silent zero is a lie the model will believe."""
    panel = built["panel"]
    early = panel.groupby("station").head(1)
    assert early["level_delta_24h"].isna().all(), "no 24h delta can exist in the first hour"
