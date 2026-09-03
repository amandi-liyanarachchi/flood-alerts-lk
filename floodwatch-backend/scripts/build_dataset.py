"""Build a machine-learning dataset from the real Sri Lankan sources.

    python -m scripts.build_dataset                    # fetch and build
    python -m scripts.build_dataset --offline          # rebuild from cache
    python -m scripts.build_dataset --no-rainfall      # skip ERA5 (much faster)
    python -m scripts.build_dataset --out data/

WHAT THIS PRODUCES

    data/stations.csv            station master, all levels in metres
    data/gauge_readings.csv      every reading ever collected (accumulating)
    data/flood_events.csv        ~17 named historical events by GN division
    data/event_dates_template.csv  ^ with blank date columns for you to fill
    data/station_hours.csv       THE TRAINING TABLE: one row per station-hour
    data/region_hours.csv        the same features mapped onto geohash-5 cells
    data/crowd_scenarios.csv     simulated crowd responses, swept over a grid
    data/DATA_DICTIONARY.md      every column, its meaning and its provenance
    data/dataset_report.md       row counts, class balance, coverage, caveats

WHAT IS REAL AND WHAT IS NOT

    REAL, measured        station metadata, published flood thresholds, water
                          levels, station rainfall, ERA5 rainfall, the flood
                          event catalogue, and every feature derived from them
    REAL, derived         labels from threshold exceedance -- computed from
                          observed levels, no simulation anywhere
    SIMULATED, flagged    crowd_scenarios.csv ONLY. Every column there is
                          prefixed `sim_` and the file carries an is_simulated
                          column set to 1 on every row. It exists because you
                          have no crowd data yet, and it is designed as a
                          sensitivity study -- "how good and how numerous would
                          the crowd have to be to add value?" -- not as a stand-in
                          for observations you do not have.

    Nothing in station_hours.csv is invented. If a value is unknown it is null.

THE HONEST LIMITATION, UP FRONT

    The gauge feed is a ROLLING window of roughly three to four days, not an
    archive. One run therefore yields a small dataset, and the report tells you
    exactly how small. The fix is time, not cleverness: this script appends to
    data/raw/gauge_readings_archive.json on every run, and your backend is
    already storing every reading its scheduler sees. Run this weekly and the
    dataset grows by itself. In the meantime, ERA5 rainfall reaches back to 1940,
    which is what makes a real multi-year rainfall-driven dataset possible today
    -- see --long-range once you have filled in event dates.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app import geo
from scripts.dataset_sources import Sources

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dataset")

RANDOM_SEED = 20260831

# Department of Meteorology rainfall language
RAIN_HEAVY_MM = 75.0
RAIN_VERY_HEAVY_MM = 150.0

# Forecast horizons, in hours. Each produces its own label columns.
HORIZONS = (6, 24)

# Crowd sensitivity grid. Deliberately small and interpretable.
CROWD_PANEL_SIZES = (5, 20, 50)
CROWD_DETECTION_RATES = (0.5, 0.7, 0.9)
CROWD_FALSE_POSITIVE_RATES = (0.02, 0.10)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract(sources: Sources, out: Path, want_rainfall: bool,
            rainfall_days: int, max_pages: int) -> dict:
    log.info("1/6  Fetching station master list...")
    stations = pd.DataFrame(sources.stations())
    if stations.empty:
        raise SystemExit("No stations returned. Check the network, or use --offline with a cache.")
    log.info("     %d stations (%d published in feet, converted to metres)",
             len(stations), int((stations["source_unit"] == "ft").sum()))

    log.info("2/6  Fetching gauge readings...")
    fresh = pd.DataFrame(sources.readings(max_pages=max_pages))
    log.info("     %d readings in this pull", len(fresh))

    readings = _merge_reading_archive(fresh, out / "raw" / "gauge_readings_archive.json")
    log.info("     %d readings in the accumulated archive", len(readings))

    log.info("3/6  Fetching the flood event catalogue...")
    try:
        catalogue = pd.DataFrame(sources.flood_catalogue())
    except Exception as exc:  # noqa: BLE001
        log.warning("     catalogue unavailable (%s) -- continuing without it", exc)
        catalogue = pd.DataFrame(columns=["event_name", "gnd_name", "dsd_name", "district"])
    if not catalogue.empty:
        log.info("     %d GN-division records across %d named events",
                 len(catalogue), catalogue["event_name"].nunique())

    rainfall = pd.DataFrame()
    if want_rainfall and not readings.empty:
        log.info("4/6  Fetching ERA5 rainfall for each station (%d days)...", rainfall_days)
        rainfall = _fetch_rainfall(sources, stations, readings, rainfall_days)
        log.info("     %d hourly rainfall values", len(rainfall))
    else:
        log.info("4/6  Skipping rainfall.")

    return {"stations": stations, "readings": readings,
            "catalogue": catalogue, "rainfall": rainfall}


def _merge_reading_archive(fresh: pd.DataFrame, archive_path: Path) -> pd.DataFrame:
    """Append-only archive. The upstream feed is a rolling window; this is not.

    This is the single most valuable line in the script over a six-month
    project: every run permanently keeps what the feed was showing that day.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    if archive_path.exists():
        try:
            frames.append(pd.DataFrame(json.loads(archive_path.read_text())))
        except Exception as exc:  # noqa: BLE001
            log.warning("     archive unreadable (%s), starting fresh", exc)
    if not fresh.empty:
        frames.append(fresh)
    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["station", "observed_at"], keep="last")
    merged = merged.sort_values(["station", "observed_at"]).reset_index(drop=True)
    archive_path.write_text(merged.to_json(orient="records"))
    return merged


def _fetch_rainfall(sources: Sources, stations: pd.DataFrame,
                    readings: pd.DataFrame, days: int) -> pd.DataFrame:
    observed = pd.to_datetime(readings["observed_at"])
    end = observed.max().date()
    start = min(observed.min().date(), end - timedelta(days=days))
    # ERA5 publishes with about five days of delay.
    end = min(end, (datetime.utcnow() - timedelta(days=6)).date())
    if start >= end:
        log.warning("     rainfall window empty (gauge history is shorter than the ERA5 delay)")
        return pd.DataFrame()

    active = set(readings["station"].unique())
    rows = []
    targets = stations[stations["station"].isin(active)]
    for i, station in enumerate(targets.itertuples(), 1):
        log.info("     [%d/%d] %s", i, len(targets), station.station)
        try:
            body = sources.rainfall_hourly(
                station.latitude, station.longitude, str(start), str(end)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("       failed: %s", exc)
            continue
        hourly = body.get("hourly") or {}
        for stamp, value in zip(hourly.get("time", []), hourly.get("precipitation", [])):
            rows.append({
                "station": station.station,
                "observed_at": stamp,
                "era5_precip_mm": float(value or 0.0),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Station-hour panel
# ---------------------------------------------------------------------------


def build_station_hours(stations: pd.DataFrame, readings: pd.DataFrame,
                        rainfall: pd.DataFrame, gauge_rain_cumulative: bool) -> pd.DataFrame:
    if readings.empty:
        return pd.DataFrame()

    log.info("5/6  Building the station-hour panel...")

    units = stations.set_index("station")["source_unit"].to_dict()
    df = readings.copy()
    df["observed_at"] = pd.to_datetime(df["observed_at"])
    df["water_level_m"] = [
        None if raw is None or (isinstance(raw, float) and math.isnan(raw))
        else round(float(raw) * (0.3048 if units.get(st) == "ft" else 1.0), 4)
        for raw, st in zip(df["water_level_raw"], df["station"])
    ]
    df["rainfall_mm"] = pd.to_numeric(df["rainfall_mm"], errors="coerce")

    panels = []
    for station, group in df.groupby("station"):
        group = group.sort_values("observed_at").set_index("observed_at")

        hourly = pd.DataFrame(index=pd.date_range(
            group.index.min().floor("h"), group.index.max().ceil("h"), freq="h"
        ))
        hourly.index.name = "hour"

        # Last observation in each hour. Readings arrive every 45-90 minutes, so
        # some hours are genuinely empty; carry forward for at most 3 hours and
        # record the age, because a stale reading is not the same as a low one.
        level = group["water_level_m"].resample("h").last()
        hourly["water_level_m"] = level.reindex(hourly.index)
        hourly["level_observed"] = hourly["water_level_m"].notna().astype(int)
        hourly["water_level_m"] = hourly["water_level_m"].ffill(limit=3)

        age = hourly["level_observed"].copy().astype(float)
        counter, ages = 0, []
        for observed in hourly["level_observed"]:
            counter = 0 if observed else counter + 1
            ages.append(counter)
        hourly["level_age_hours"] = ages

        # Station rainfall. SUM or MAX depends on whether the upstream field is
        # incremental or a running daily total, which is undocumented -- see the
        # note on settings.gauge_rainfall_is_cumulative.
        rain = group["rainfall_mm"].resample("h")
        hourly["gauge_rain_mm"] = (rain.max() if gauge_rain_cumulative else rain.sum()).reindex(
            hourly.index).fillna(0.0)

        hourly["station"] = station
        panels.append(hourly.reset_index())

    panel = pd.concat(panels, ignore_index=True)
    panel = panel.merge(stations, on="station", how="left")

    if not rainfall.empty:
        era5 = rainfall.copy()
        era5["hour"] = pd.to_datetime(era5["observed_at"]).dt.floor("h")
        era5 = era5.groupby(["station", "hour"], as_index=False)["era5_precip_mm"].sum()
        panel = panel.merge(era5, on=["station", "hour"], how="left")
    else:
        panel["era5_precip_mm"] = np.nan

    panel = panel.sort_values(["station", "hour"]).reset_index(drop=True)
    panel = _add_features(panel)
    panel = _add_upstream(panel, stations)
    panel = _add_labels(panel)
    return panel


def _add_features(panel: pd.DataFrame) -> pd.DataFrame:
    grouped = panel.groupby("station", group_keys=False)

    # --- normalised level: the feature that transfers between stations -----
    span = (panel["major_flood_level_m"] - panel["alert_level_m"]).replace(0, np.nan)
    panel["level_norm"] = (panel["water_level_m"] - panel["alert_level_m"]) / span
    panel["level_over_alert"] = panel["water_level_m"] / panel["alert_level_m"].replace(0, np.nan)

    # --- movement ---------------------------------------------------------
    for lag in (1, 3, 6, 12, 24):
        panel[f"level_delta_{lag}h"] = grouped["water_level_m"].diff(lag)
    panel["level_accel"] = grouped["level_delta_3h"].diff(3)

    # --- where it has been ------------------------------------------------
    for window in (24, 48, 72):
        roll = grouped["water_level_m"].rolling(window, min_periods=2)
        panel[f"level_max_{window}h"] = roll.max().reset_index(drop=True)
        panel[f"level_min_{window}h"] = roll.min().reset_index(drop=True)
        panel[f"level_mean_{window}h"] = roll.mean().reset_index(drop=True)
    panel["level_range_24h"] = panel["level_max_24h"] - panel["level_min_24h"]

    # Percentile against this station's own history. Robust where the published
    # thresholds are stale -- and two of our sources disagree about them.
    panel["level_pct_rank"] = grouped["water_level_m"].rank(pct=True)

    # --- hours since the station last exceeded its alert level ------------
    above = (panel["water_level_m"] >= panel["alert_level_m"]).fillna(False)
    since = []
    counter = 10_000
    last_station = None
    for station, flag in zip(panel["station"], above):
        if station != last_station:
            counter, last_station = 10_000, station
        counter = 0 if flag else min(counter + 1, 10_000)
        since.append(counter)
    panel["hours_since_alert"] = since

    # --- rainfall ---------------------------------------------------------
    panel["rain_mm"] = panel["era5_precip_mm"].fillna(panel["gauge_rain_mm"]).fillna(0.0)
    panel["rain_source"] = np.where(panel["era5_precip_mm"].notna(), "era5", "gauge")
    grouped = panel.groupby("station", group_keys=False)
    for window in (1, 3, 6, 12, 24, 48, 72):
        panel[f"rain_{window}h_mm"] = (
            grouped["rain_mm"].rolling(window, min_periods=1).sum().reset_index(drop=True)
        )
    panel["rain_max_1h_in_24h"] = (
        grouped["rain_mm"].rolling(24, min_periods=1).max().reset_index(drop=True)
    )
    panel["rain_wet_hours_24h"] = (
        grouped.apply(lambda g: (g["rain_mm"] > 0.1).rolling(24, min_periods=1).sum())
        .reset_index(drop=True)
    )

    # Antecedent Precipitation Index: a decayed sum standing in for how
    # saturated the ground already is. This is why the same 50 mm floods a
    # catchment in September and drains away in February.
    k_hourly = 0.9 ** (1 / 24)
    api_values = []
    api, last_station = 0.0, None
    for station, rain in zip(panel["station"], panel["rain_mm"]):
        if station != last_station:
            api, last_station = 0.0, station
        api = api * k_hourly + float(rain or 0.0)
        api_values.append(round(api, 4))
    panel["api_mm"] = api_values

    panel["rain_is_heavy_24h"] = (panel["rain_24h_mm"] >= RAIN_HEAVY_MM).astype(int)
    panel["rain_is_very_heavy_24h"] = (panel["rain_24h_mm"] >= RAIN_VERY_HEAVY_MM).astype(int)

    # --- time -------------------------------------------------------------
    month = panel["hour"].dt.month
    panel["month"] = month
    panel["hour_of_day"] = panel["hour"].dt.hour
    panel["month_sin"] = np.sin(2 * np.pi * month / 12).round(4)
    panel["month_cos"] = np.cos(2 * np.pi * month / 12).round(4)
    panel["hour_sin"] = np.sin(2 * np.pi * panel["hour_of_day"] / 24).round(4)
    panel["hour_cos"] = np.cos(2 * np.pi * panel["hour_of_day"] / 24).round(4)
    panel["monsoon"] = np.select(
        [month.isin([12, 1, 2]), month.isin([3, 4]),
         month.isin([5, 6, 7, 8, 9]), month.isin([10, 11])],
        ["northeast", "first_inter", "southwest", "second_inter"],
        default="unknown",
    )

    # --- basin context ----------------------------------------------------
    panel["station_above_alert"] = above.astype(int)
    basin_counts = (
        panel.groupby(["basin", "hour"])["station_above_alert"].transform("sum")
    )
    panel["basin_stations_above_alert"] = basin_counts.fillna(0).astype(int)

    return panel


def _add_upstream(panel: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """Upstream station level, lagged by estimated travel time.

    The highest-value engineered feature available: water at Kithulgala reaches
    Glencourse hours later, then Hanwella, then Nagalagam Street, so an upstream
    reading is a direct physical measurement of a downstream near-future.

    Two heuristics, both stated in the data dictionary because both are
    assumptions a reviewer is entitled to challenge:
      * "upstream" = the next station in the same basin at higher elevation
      * travel time = the lag maximising cross-correlation of the two level
        series, searched over 1-12 h, defaulting to 3 h when there is not
        enough overlapping data to estimate it
    """
    panel["upstream_station"] = None
    panel["upstream_lag_hours"] = np.nan
    panel["upstream_level_m"] = np.nan
    panel["upstream_level_norm"] = np.nan
    panel["upstream_lag_estimated"] = 0

    elevations = stations.set_index("station")["elevation_m"].to_dict()
    by_station = {s: g.set_index("hour") for s, g in panel.groupby("station")}

    for basin, group in stations.groupby("basin"):
        members = [s for s in group["station"] if s in by_station]
        if len(members) < 2:
            continue
        ranked = sorted(
            members,
            key=lambda s: (elevations.get(s) if elevations.get(s) is not None else -1),
        )
        for downstream, upstream in zip(ranked, ranked[1:]):
            down = by_station[downstream]["water_level_m"]
            up = by_station[upstream]["water_level_m"]
            lag, estimated = _estimate_lag(up, down)

            shifted = up.shift(lag)
            mask = panel["station"] == downstream
            aligned = panel.loc[mask, "hour"].map(shifted)
            panel.loc[mask, "upstream_station"] = upstream
            panel.loc[mask, "upstream_lag_hours"] = lag
            panel.loc[mask, "upstream_level_m"] = aligned.values
            panel.loc[mask, "upstream_lag_estimated"] = int(estimated)

            alert = panel.loc[mask, "alert_level_m"]
            major = panel.loc[mask, "major_flood_level_m"]
            span = (major - alert).replace(0, np.nan)
            panel.loc[mask, "upstream_level_norm"] = (aligned.values - alert) / span

    return panel


def _estimate_lag(upstream: pd.Series, downstream: pd.Series,
                  default: int = 3, max_lag: int = 12) -> tuple[int, bool]:
    joined = pd.concat([upstream.rename("up"), downstream.rename("down")], axis=1).dropna()
    if len(joined) < 48:
        return default, False
    best_lag, best_corr = default, -2.0
    for lag in range(1, max_lag + 1):
        corr = joined["up"].shift(lag).corr(joined["down"])
        if corr is not None and not math.isnan(corr) and corr > best_corr:
            best_lag, best_corr = lag, corr
    return (best_lag, True) if best_corr > 0.1 else (default, False)


def _add_labels(panel: pd.DataFrame) -> pd.DataFrame:
    """Forward-looking labels. Computed from observed levels only.

    y_<threshold>_<H>h = 1 if the station reaches that threshold at any point in
    the next H hours. Note this is a FORECASTING target: the model is given the
    present and asked about the future, which is a legitimate task even though
    the label and a feature both derive from the same gauge. State that framing
    plainly in the paper rather than calling it flood detection.
    """
    grouped = panel.groupby("station", group_keys=False)
    for horizon in HORIZONS:
        future_max = grouped["water_level_m"].apply(
            lambda s, h=horizon: s.shift(-1).rolling(h, min_periods=1).max().shift(-(h - 1))
        ).reset_index(drop=True)
        panel[f"future_max_level_{horizon}h"] = future_max
        for name, column in (("alert", "alert_level_m"),
                             ("minor", "minor_flood_level_m"),
                             ("major", "major_flood_level_m")):
            panel[f"y_{name}_{horizon}h"] = (
                (future_max >= panel[column]).where(future_max.notna() & panel[column].notna())
            ).astype("Float64")
        panel[f"y_rise_{horizon}h"] = (
            (future_max - panel["water_level_m"]).where(future_max.notna())
        ).round(4)
    return panel


# ---------------------------------------------------------------------------
# Region-hour panel
# ---------------------------------------------------------------------------


def build_region_hours(panel: pd.DataFrame, stations: pd.DataFrame,
                       precision: int = 5, radius_km: float = 25.0) -> pd.DataFrame:
    """Map the station panel onto geohash cells, so it lines up with the app.

    A cell takes the features of its nearest station, plus the distance, so a
    model can learn to discount a gauge 20 km away.
    """
    if panel.empty:
        return pd.DataFrame()

    cells: dict[str, tuple[str, float, float, float]] = {}
    step = 0.045  # about 5 km
    for station in stations.itertuples():
        lat0, lon0 = station.latitude, station.longitude
        reach = int(radius_km / 5) + 1
        for i in range(-reach, reach + 1):
            for j in range(-reach, reach + 1):
                lat, lon = lat0 + i * step, lon0 + j * step
                if not geo.is_plausible_lk_coordinate(lat, lon):
                    continue
                km = geo.haversine_km(lat, lon, lat0, lon0)
                if km > radius_km:
                    continue
                cell = geo.encode(lat, lon, precision)
                if cell not in cells or km < cells[cell][1]:
                    clat, clon = geo.decode_center(cell)
                    cells[cell] = (station.station, km, clat, clon)

    mapping = pd.DataFrame(
        [{"region": c, "station": s, "station_km": round(km, 2),
          "latitude": lat, "longitude": lon}
         for c, (s, km, lat, lon) in cells.items()]
    )
    if mapping.empty:
        return pd.DataFrame()

    regions = mapping.merge(panel, on="station", how="inner")
    # Confidence decays with distance: a gauge 25 km away describes a different
    # catchment than one next door.
    regions["gauge_confidence"] = (
        1.0 - ((regions["station_km"] - 10).clip(lower=0) / 30.0)
    ).clip(0.5, 1.0).round(3)
    return regions


# ---------------------------------------------------------------------------
# Crowd sensitivity simulation
# ---------------------------------------------------------------------------


def build_crowd_scenarios(panel: pd.DataFrame, truth_column: str = "y_minor_6h") -> pd.DataFrame:
    """Simulated crowd responses, swept over a grid. NOT observations.

    Every column is prefixed `sim_` and every row carries is_simulated = 1.

    The question this answers is a power analysis, and it is a legitimate result
    to publish before a pilot: *given a panel of N participants who notice a real
    flood with probability p and report one falsely with probability q, how much
    does the crowd signal add over the physical model?* Sweeping N, p and q tells
    you what panel size the study would actually need -- which is far more
    useful, and far more honest, than pretending to have crowd observations.

    The generator is deliberately simple and stated in full: respondent counts
    are Poisson, and "yes" counts are Binomial conditioned on the true label. It
    contains no spatial correlation, no reporting delay, and no herding, all of
    which are real. Treat the result as an upper bound on how well an
    independent-reporter crowd could do.
    """
    if panel.empty or truth_column not in panel:
        return pd.DataFrame()

    rows = panel[panel[truth_column].notna()][["station", "hour", truth_column]].copy()
    if rows.empty:
        return pd.DataFrame()

    rng = np.random.default_rng(RANDOM_SEED)
    truth = rows[truth_column].astype(float).to_numpy()
    out = []

    for panel_size in CROWD_PANEL_SIZES:
        for detection in CROWD_DETECTION_RATES:
            for false_positive in CROWD_FALSE_POSITIVE_RATES:
                respondents = rng.poisson(panel_size, size=len(rows))
                probability = np.where(truth > 0.5, detection, false_positive)
                yes = rng.binomial(np.maximum(respondents, 0), probability)
                with np.errstate(invalid="ignore", divide="ignore"):
                    ratio = np.where(respondents > 0, yes / np.maximum(respondents, 1), np.nan)

                frame = rows[["station", "hour"]].copy()
                frame["scenario_id"] = f"n{panel_size}_p{detection}_q{false_positive}"
                frame["sim_panel_size"] = panel_size
                frame["sim_detection_rate"] = detection
                frame["sim_false_positive_rate"] = false_positive
                frame["sim_respondents"] = respondents
                frame["sim_yes"] = yes
                frame["sim_yes_ratio"] = np.round(ratio, 4)
                frame["sim_floor_met"] = (respondents >= 5).astype(int)
                frame["sim_crosses_75pct"] = (
                    (respondents >= 5) & (ratio >= 0.75)
                ).astype(int)
                frame["truth_label"] = truth.astype(int)
                frame["is_simulated"] = 1
                out.append(frame)

    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# Splits, docs, report
# ---------------------------------------------------------------------------


def temporal_split(panel: pd.DataFrame) -> dict:
    """70/15/15 by TIME, never at random.

    A random split puts hour 14 of a flood in training and hour 15 in test. The
    model memorises the event and reports a spectacular score that collapses in
    the field. This single mistake invalidates more flood-prediction papers than
    any other.
    """
    if panel.empty:
        return {}
    hours = panel["hour"].sort_values()
    train_end = hours.quantile(0.70)
    val_end = hours.quantile(0.85)
    return {
        "method": "temporal (never random -- see build_dataset.temporal_split)",
        "train": [str(hours.min()), str(train_end)],
        "validation": [str(train_end), str(val_end)],
        "test": [str(val_end), str(hours.max())],
        "spatial_holdout_suggestion": (
            "Also run leave-one-basin-out: train on all basins but one, test on "
            "the held-out basin. That answers whether the model generalises to a "
            "river it has never seen, which is the difference between a Kelani "
            "tool and a Sri Lanka system."
        ),
    }


def write_report(out: Path, data: dict, panel: pd.DataFrame,
                 regions: pd.DataFrame, crowd: pd.DataFrame, splits: dict) -> None:
    lines = ["# Dataset report", "",
             f"Generated {datetime.utcnow().isoformat(timespec='seconds')}Z", ""]

    readings = data["readings"]
    if not readings.empty:
        observed = pd.to_datetime(readings["observed_at"])
        span_days = (observed.max() - observed.min()).total_seconds() / 86400
        lines += [
            "## Coverage", "",
            f"- Stations: **{len(data['stations'])}**",
            f"- Gauge readings in the archive: **{len(readings):,}**",
            f"- Readings span: **{observed.min()} to {observed.max()}** "
            f"(**{span_days:.1f} days**)",
            f"- Station-hours built: **{len(panel):,}**",
            f"- Region-hours built: **{len(regions):,}**",
            "",
        ]
        if span_days < 30:
            lines += [
                "> **The gauge feed is a rolling window, not an archive.** "
                f"This run captured {span_days:.1f} days. That is not enough to train "
                "on, and no amount of feature engineering changes it. The archive at "
                "`data/raw/gauge_readings_archive.json` is append-only, and the "
                "backend's scheduler is already storing every reading it sees, so "
                "re-running this weekly grows the dataset by itself. Meanwhile ERA5 "
                "rainfall reaches back to 1940 and is the source that can give the "
                "project real multi-year features today.", "",
            ]

    if not panel.empty:
        lines += ["## Label balance", "", "| Label | Positives | Rows | Rate |", "|---|---|---|---|"]
        for horizon in HORIZONS:
            for name in ("alert", "minor", "major"):
                column = f"y_{name}_{horizon}h"
                if column in panel:
                    series = panel[column].dropna()
                    if len(series):
                        positives = int(series.sum())
                        lines.append(
                            f"| `{column}` | {positives:,} | {len(series):,} | "
                            f"{positives / len(series):.4%} |"
                        )
        lines += ["",
                  "Floods are rare, so most of these rates are small. Use "
                  "`scale_pos_weight` or subsample the negatives; an untreated model "
                  "that always predicts 'no flood' will look 99% accurate and be "
                  "worthless.", ""]

        missing = panel.isna().mean().sort_values(ascending=False)
        worst = missing[missing > 0].head(12)
        if len(worst):
            lines += ["## Most incomplete columns", "", "| Column | Missing |", "|---|---|"]
            lines += [f"| `{c}` | {v:.1%} |" for c, v in worst.items()]
            lines.append("")

    catalogue = data["catalogue"]
    if not catalogue.empty:
        lines += ["## Flood event catalogue", "",
                  f"{catalogue['event_name'].nunique()} named events, "
                  f"{len(catalogue):,} affected GN divisions.", "",
                  "| Event | GN divisions | Districts |", "|---|---|---|"]
        summary = catalogue.groupby("event_name").agg(
            gnds=("gnd_name", "count"),
            districts=("district", lambda s: s.nunique()),
        ).sort_values("gnds", ascending=False)
        lines += [f"| {name} | {row.gnds} | {row.districts} |"
                  for name, row in summary.iterrows()]
        lines += ["",
                  "**These events carry a year but not a date.** Fill in "
                  "`data/event_dates_template.csv` from DMC situation reports to turn "
                  "the catalogue into time-aligned labels. Until then they are a "
                  "spatial susceptibility feature, not a training label.", ""]

    if not crowd.empty:
        lines += ["## Simulated crowd scenarios", "",
                  f"{len(crowd):,} rows across {crowd['scenario_id'].nunique()} scenarios.", "",
                  "**Simulated, not observed.** Every column is prefixed `sim_` and "
                  "every row carries `is_simulated = 1`. This is a power analysis: it "
                  "answers what panel size and reporter reliability the study would "
                  "need, not what the crowd actually did.", ""]
        rates = crowd.groupby("scenario_id").apply(
            lambda g: pd.Series({
                "detects_when_true": g.loc[g.truth_label == 1, "sim_crosses_75pct"].mean(),
                "false_alarm_when_false": g.loc[g.truth_label == 0, "sim_crosses_75pct"].mean(),
            }), include_groups=False
        )
        lines += ["| Scenario | Fires when flooding | Fires when not |", "|---|---|---|"]
        lines += [f"| `{name}` | {row.detects_when_true:.1%} | {row.false_alarm_when_false:.1%} |"
                  for name, row in rates.iterrows()]
        lines.append("")
        lines += _interpret_crowd(crowd)

    if splits:
        lines += ["## Splits", "", "```json", json.dumps(splits, indent=2), "```", ""]

    (out / "dataset_report.md").write_text("\n".join(lines))


def _interpret_crowd(crowd: pd.DataFrame) -> list[str]:
    """State, in the report, what the sweep actually shows about the 75% rule.

    A result the sweep makes obvious and intuition does not: because the rule is
    a threshold on a PROPORTION, it can only fire reliably when the true
    detection rate exceeds the threshold itself. Below that, a larger panel is
    LESS likely to fire than a small one -- the proportion concentrates on its
    mean, while a small panel occasionally crosses by luck. Any crossing at
    p < 0.75 is therefore noise, which is precisely the argument for a
    respondent floor, and an argument the study can now make with numbers.
    """
    lines = ["### What the sweep says about the >=75% rule", ""]

    flooding = crowd[crowd["truth_label"] == 1]
    if flooding.empty:
        return []

    by_detection = flooding.groupby("sim_detection_rate")["sim_crosses_75pct"].mean()
    workable = [rate for rate, hit in by_detection.items() if hit >= 0.5]
    floor = min(workable) if workable else None

    if floor is not None:
        lines.append(
            f"- The rule fires for at least half of genuine flood hours only once "
            f"participants detect flooding with probability **>= {floor:.0%}**. "
            f"Below that it is largely silent however many people you recruit."
        )
    else:
        lines.append(
            "- The rule did not fire for half of genuine flood hours at ANY tested "
            "reliability. Either the threshold or the panel sizes need revisiting."
        )

    # The non-monotonicity, computed rather than asserted.
    weak = flooding[flooding["sim_detection_rate"] <= 0.5]
    if not weak.empty:
        small = weak[weak["sim_panel_size"] == min(CROWD_PANEL_SIZES)]["sim_crosses_75pct"].mean()
        large = weak[weak["sim_panel_size"] == max(CROWD_PANEL_SIZES)]["sim_crosses_75pct"].mean()
        if small > large:
            lines.append(
                f"- **Counter-intuitive, and worth a paragraph in the paper:** when "
                f"reliability is poor (p = 0.5), a panel of {min(CROWD_PANEL_SIZES)} "
                f"crosses the 75% threshold **{small:.1%}** of the time but a panel of "
                f"{max(CROWD_PANEL_SIZES)} crosses it **{large:.1%}**. A bigger panel is "
                f"*less* likely to fire, because the proportion concentrates on its true "
                f"mean while a small sample crosses by luck. Every such crossing is "
                f"noise. This is the quantitative case for the respondent floor."
            )

    lines.append(
        "- Implication for the design: a fixed >=75% cut is a blunt instrument. "
        "Consider testing the observed proportion against a null reporting rate "
        "(a one-sided binomial test) instead, which uses panel size as evidence "
        "rather than discarding it."
    )
    lines.append("")
    return lines


DICTIONARY = """# Data dictionary

Provenance codes:

| Code | Meaning |
|---|---|
| **M** | Measured. Straight from the source, unmodified except unit conversion. |
| **D** | Derived. Computed from measured values only. No simulation. |
| **S** | Simulated. Appears only in `crowd_scenarios.csv`, always prefixed `sim_`. |
| **A** | Assumption. Derived using a documented heuristic a reviewer may challenge. |

---

## stations.csv

| Column | Prov. | Meaning |
|---|---|---|
| `station` | M | Station name, the join key everywhere |
| `basin` / `tributary` | M | River system |
| `latitude` / `longitude` | M | WGS84 |
| `alert_level_m`, `minor_flood_level_m`, `major_flood_level_m` | M | Official thresholds, **converted to metres** |
| `elevation_m` | M | Station elevation, m MSL |
| `source_unit` | M | `m` or `ft` — what the source published before conversion |

## gauge_readings.csv

Append-only archive. Grows every run; the upstream feed does not.

| Column | Prov. | Meaning |
|---|---|---|
| `station`, `basin` | M | |
| `observed_at` | M | Publication time of the reading (UTC). Not necessarily the instant of measurement — do not claim otherwise |
| `water_level_raw` | M | As published, in `source_unit` |
| `rainfall_mm` | M | As published. **Undocumented upstream whether incremental or a daily total** |

## station_hours.csv — the training table

One row per station per hour.

### Identity
| Column | Prov. | Meaning |
|---|---|---|
| `station`, `hour` | M | Join key. `hour` is UTC, floored |
| `level_observed` | D | 1 if a real reading fell in this hour, 0 if carried forward |
| `level_age_hours` | D | Hours since the last real reading. **A stale gauge is not a low gauge** |

### River state
| Column | Prov. | Meaning |
|---|---|---|
| `water_level_m` | M | Metres, converted |
| `level_norm` | D | `(level − alert) / (major − alert)`. The feature that transfers between stations |
| `level_over_alert` | D | Ratio to alert level |
| `level_delta_{1,3,6,12,24}h` | D | Rate of change |
| `level_accel` | D | Whether the rise is speeding up |
| `level_{max,min,mean}_{24,48,72}h` | D | Rolling context |
| `level_range_24h` | D | Daily swing |
| `level_pct_rank` | D | Percentile against this station's own history |
| `hours_since_alert` | D | Since the alert level was last exceeded. 10000 = never |
| `station_above_alert` | D | Binary |
| `basin_stations_above_alert` | D | Whole-basin event vs single tributary |

### Upstream
| Column | Prov. | Meaning |
|---|---|---|
| `upstream_station` | A | Next station in the basin at higher elevation |
| `upstream_lag_hours` | A | Travel time, from peak cross-correlation over 1–12 h |
| `upstream_lag_estimated` | D | 1 if estimated from data, 0 if the 3 h default was used |
| `upstream_level_m`, `upstream_level_norm` | D | Upstream level shifted by that lag |

### Rainfall
| Column | Prov. | Meaning |
|---|---|---|
| `era5_precip_mm` | M | ERA5 reanalysis, hourly |
| `gauge_rain_mm` | M | Station-reported rainfall |
| `rain_mm` | D | ERA5 where available, else gauge |
| `rain_source` | D | Which of the two was used |
| `rain_{1,3,6,12,24,48,72}h_mm` | D | Accumulations |
| `rain_max_1h_in_24h` | D | Peak intensity — capacity is what drains overwhelm |
| `rain_wet_hours_24h` | D | Duration, independent of total |
| `api_mm` | D | Antecedent Precipitation Index, k=0.9/day. Soil-saturation proxy |
| `rain_is_heavy_24h`, `rain_is_very_heavy_24h` | D | Met Department thresholds (75 / 150 mm) |

### Time
| Column | Prov. | Meaning |
|---|---|---|
| `month`, `hour_of_day` | D | |
| `month_sin/cos`, `hour_sin/cos` | D | Cyclical, so December sits beside January |
| `monsoon` | D | `northeast` / `first_inter` / `southwest` / `second_inter` |

### Labels — forward-looking, computed from observed levels only
| Column | Prov. | Meaning |
|---|---|---|
| `future_max_level_6h`, `future_max_level_24h` | D | Highest level reached in the next H hours |
| `y_alert_6h`, `y_alert_24h` | D | 1 if the alert level is reached within H hours |
| `y_minor_6h`, `y_minor_24h` | D | 1 if the minor flood level is reached within H hours |
| `y_major_6h`, `y_major_24h` | D | 1 if the major flood level is reached within H hours |
| `y_rise_6h`, `y_rise_24h` | D | Regression target: metres of rise still to come |

Start with `y_minor_6h` as the primary target: it is the threshold at which the
Irrigation Department considers flooding to have begun, and six hours is a
horizon someone can actually act on.

> **Framing.** These are FORECASTING targets: the model sees the present and is
> asked about the future. Both a feature and the label derive from the same
> gauge, which is legitimate for river-stage forecasting — but say so plainly.
> Do not describe it as flood detection.

## region_hours.csv

`station_hours.csv` mapped onto geohash-5 cells (~4.9 km), matching the backend.

| Column | Prov. | Meaning |
|---|---|---|
| `region` | D | Geohash-5 cell |
| `station_km` | D | Distance to the nearest station |
| `gauge_confidence` | A | Distance discount, 1.0 within 10 km falling to 0.5 at 40 km |

## crowd_scenarios.csv — SIMULATED

| Column | Prov. | Meaning |
|---|---|---|
| `scenario_id` | S | `n{panel}_p{detection}_q{false positive}` |
| `sim_panel_size` | S | Mean respondents per station-hour (Poisson) |
| `sim_detection_rate` | S | P(reports flooding \\| flooding) |
| `sim_false_positive_rate` | S | P(reports flooding \\| no flooding) |
| `sim_respondents`, `sim_yes`, `sim_yes_ratio` | S | Generated responses |
| `sim_floor_met` | S | ≥5 respondents |
| `sim_crosses_75pct` | S | Floor met and ratio ≥ 0.75 — would have contributed |
| `truth_label` | D | The real label being conditioned on |
| `is_simulated` | S | Always 1 |

## flood_events.csv

| Column | Prov. | Meaning |
|---|---|---|
| `event_name` | M | e.g. `Nilwala Ganga Flood 2017` |
| `gnd_name`, `dsd_name`, `district`, `province` | M | Affected administrative units |
| `latitude`, `longitude` | M | Centroid of the affected division |

Carries a year but no date. Complete `event_dates_template.csv` from DMC
situation reports to turn these into time-aligned labels.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="data", help="output directory (default: data)")
    parser.add_argument("--offline", action="store_true", help="rebuild from cache only")
    parser.add_argument("--no-rainfall", action="store_true", help="skip the ERA5 fetch")
    parser.add_argument("--rainfall-days", type=int, default=120)
    parser.add_argument("--max-pages", type=int, default=12,
                        help="gauge reading pages of 1000 to pull")
    parser.add_argument("--gauge-rain-cumulative", action="store_true",
                        help="treat the station rain_fall field as a running daily total "
                             "rather than incremental. CONFIRM THIS WITH THE IRRIGATION "
                             "DEPARTMENT -- it changes 24h rainfall by an order of magnitude")
    parser.add_argument("--no-crowd", action="store_true", help="skip the crowd simulation")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sources = Sources(out / "raw", offline=args.offline)

    try:
        data = extract(sources, out, not args.no_rainfall, args.rainfall_days, args.max_pages)
    finally:
        sources.close()

    panel = build_station_hours(
        data["stations"], data["readings"], data["rainfall"], args.gauge_rain_cumulative
    )
    regions = build_region_hours(panel, data["stations"])
    crowd = pd.DataFrame() if args.no_crowd else build_crowd_scenarios(panel)

    log.info("6/6  Writing outputs to %s/ ...", out)
    data["stations"].to_csv(out / "stations.csv", index=False)
    data["readings"].to_csv(out / "gauge_readings.csv", index=False)
    if not data["catalogue"].empty:
        data["catalogue"].to_csv(out / "flood_events.csv", index=False)
        template = (
            data["catalogue"][["event_name"]].drop_duplicates().sort_values("event_name")
        )
        template["start_date"] = ""
        template["end_date"] = ""
        template["peak_date"] = ""
        template["source_citation"] = ""
        template["confidence"] = ""
        template.to_csv(out / "event_dates_template.csv", index=False)
    if not panel.empty:
        panel.to_csv(out / "station_hours.csv", index=False)
    if not regions.empty:
        regions.to_csv(out / "region_hours.csv", index=False)
    if not crowd.empty:
        crowd.to_csv(out / "crowd_scenarios.csv", index=False)

    splits = temporal_split(panel)
    if splits:
        (out / "splits.json").write_text(json.dumps(splits, indent=2))
    (out / "DATA_DICTIONARY.md").write_text(DICTIONARY)
    write_report(out, data, panel, regions, crowd, splits)

    log.info("")
    log.info("Done. %d network calls, %d served from cache.", sources.calls, sources.cache_hits)
    log.info("Read %s/dataset_report.md first -- it says what you actually got.", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
