"""Synthetic deployment generator for the Flood Alerts LK evaluation.

WHAT THIS IS
This module generates a simulated multi-year deployment: rainfall, catchment
response, river stage at a network of gauges, and crowdsourced flood reports from
a panel of participants. It is a SIMULATOR. Every value it produces is generated
by the process described below, not observed in the field. The experiment that
consumes it (ml/run_experiment.py) is a simulation study, and the thesis sections
in thesis/ describe it as such throughout.

WHY A SIMULATOR IS THE RIGHT INSTRUMENT HERE
Three properties a field dataset cannot offer at this stage of the project:

  1. Ground truth is exact. Flood onset is known to the hour, so lead time can be
     measured without inheriting the timing error of a situation report published
     hours after the event.
  2. The crowd is controllable. Participant panel size, detection reliability and
     false-report rate are free parameters, so the study can ask what the
     crowdsourcing layer would contribute across the whole plausible range rather
     than at the single operating point a small pilot happens to land on.
  3. Rare events are sufficient. Floods are perhaps 0.5% of station-hours; a
     three-year simulated record contains enough episodes for the confidence
     intervals to mean something.

THE MODEL
A conceptual rainfall-runoff-routing chain, standard in operational hydrology:

    rainfall  ->  soil moisture store  ->  runoff  ->  channel routing  ->  stage

  Rainfall      Poisson storm arrivals whose rate follows the Sri Lankan
                bimodal monsoon cycle (south-west May-September, north-east
                December-February), with gamma-distributed storm depths and
                within-basin spatial correlation.
  Soil store    A single bucket per catchment. Rain fills it; evapotranspiration
                drains it; runoff is generated once it passes field capacity.
                This is what makes antecedent conditions matter -- the same storm
                produces very different runoff on wet and dry ground.
  Routing       A linear reservoir cascade down each river, so a flood wave takes
                a realistic and *station-pair-specific* number of hours to travel
                downstream. This is the structure the upstream-lag feature is
                meant to exploit, and it is planted here so that whether the
                model finds it is a genuine test.
  Stage         A power-law rating curve converts discharge to water level, then
                observation noise and realistic sensor dropouts are applied.

  Local flood   Separately from the river, a cell floods when short-duration
                rainfall exceeds the local drainage network's capacity. Capacity
                is a LATENT, slowly varying quantity -- drains silt up over
                weeks and are occasionally cleared -- and it is never observable
                to the model. This is deliberate and it is the crux of the
                study: localised urban and drain-blockage flooding is most of
                Colombo's actual flood experience, no river gauge can see it,
                and its immediate driver is unobserved. It is precisely the
                condition under which a crowd should be able to tell you
                something instruments cannot.

  Crowd         At each station-hour a Poisson number of participants is
                available, modulated by time of day (few reports at 3am). Each
                reports flooding with probability `detection_rate` when the cell
                is genuinely flooding -- from the river OR locally -- and with
                probability `false_report_rate` otherwise.

PARAMETERS ARE PLAUSIBLE, NOT CALIBRATED
Catchment areas, response times and flood thresholds are set to values typical of
the Kelani, Kalu and Gin basins. They are not fitted to observed Sri Lankan
records. The study's claims are therefore about the relative performance of
model architectures under a known data-generating process -- not about absolute
forecast skill on those rivers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 8760


@dataclass
class StationSpec:
    """One gauge. `position` is its order down the river, 0 = headwater."""

    name: str
    basin: str
    position: int
    latitude: float
    longitude: float
    alert_level_m: float
    minor_flood_level_m: float
    major_flood_level_m: float
    catchment_km2: float
    travel_time_h: int          # hours for a wave to arrive from the station above
    baseline_level_m: float
    rating_exponent: float = 0.55
    panel_size: float = 12.0    # mean participants present in this station's cell
    # Urbanisation, 0-1. Drives localised flooding: concrete does not absorb, so
    # a built-up cell floods from a downpour the river never notices.
    urbanisation: float = 0.3
    # Median drainage capacity, mm of rain in 3 hours the local network can carry
    # before water stands in the streets.
    drainage_capacity_mm: float = 42.0


@dataclass
class BasinSpec:
    name: str
    stations: list[StationSpec]
    # Monsoon weighting: south-west basins peak May-September, north-east
    # basins December-February. Sri Lanka's wet zone is south-western.
    monsoon: str = "southwest"
    soil_capacity_mm: float = 120.0
    recession_hours: float = 48.0


@dataclass
class SimulationConfig:
    years: int = 3
    start: str = "2022-01-01"
    seed: int = 20260831
    detection_rate: float = 0.75      # P(participant reports | genuinely flooding)
    false_report_rate: float = 0.04   # P(participant reports | not flooding)
    sensor_dropout_rate: float = 0.03 # fraction of hours with no reading
    level_noise_m: float = 0.04
    rain_gauge_noise: float = 0.15
    # Design flood frequency: the discharge quantile mapped to the minor flood
    # level. 0.993 puts a station at or above minor flood for roughly 0.7% of
    # hours, which is the order of magnitude Sri Lankan wet-zone gauges show.
    flood_quantile: float = 0.993


def default_network() -> list[BasinSpec]:
    """Twelve stations across three basins, four stations down each river.

    Geometry, thresholds and travel times are of the same order as the real
    Kelani, Kalu and Gin networks. Station names are generic on purpose: these
    are simulated gauges, and naming them after real stations would invite the
    reader to treat simulated output as observations of those rivers.
    """
    return [
        BasinSpec("Basin-A", monsoon="southwest", soil_capacity_mm=110, recession_hours=42,
                  stations=[
                      StationSpec("A1-Headwater", "Basin-A", 0, 6.99, 80.42, 3.0, 4.0, 6.0, 155, 0, 1.10),
                      StationSpec("A2-Upper", "Basin-A", 1, 6.98, 80.19, 15.0, 16.0, 19.0, 620, 4, 9.20),
                      StationSpec("A3-Middle", "Basin-A", 2, 6.91, 80.08, 6.5, 8.0, 10.0, 1120, 5, 3.60,
                                   urbanisation=0.55, drainage_capacity_mm=45.0),
                      StationSpec("A4-Lower", "Basin-A", 3, 6.96, 79.88, 1.22, 1.52, 2.13, 2230, 6, 0.72,
                                   urbanisation=0.85, drainage_capacity_mm=38.0),
                  ]),
        BasinSpec("Basin-B", monsoon="southwest", soil_capacity_mm=135, recession_hours=54,
                  stations=[
                      StationSpec("B1-Headwater", "Basin-B", 0, 6.75, 80.62, 2.5, 3.5, 5.0, 130, 0, 0.95),
                      StationSpec("B2-Upper", "Basin-B", 1, 6.70, 80.40, 8.0, 9.5, 12.0, 540, 5, 4.40),
                      StationSpec("B3-Middle", "Basin-B", 2, 6.73, 80.22, 10.0, 10.7, 12.2, 1350, 6, 5.10),
                      StationSpec("B4-Lower", "Basin-B", 3, 6.58, 79.98, 2.0, 2.6, 3.4, 2600, 8, 1.05,
                                   urbanisation=0.70, drainage_capacity_mm=42.0),
                  ]),
        BasinSpec("Basin-C", monsoon="northeast", soil_capacity_mm=95, recession_hours=36,
                  stations=[
                      StationSpec("C1-Headwater", "Basin-C", 0, 6.42, 80.70, 2.2, 3.0, 4.2, 95, 0, 0.80),
                      StationSpec("C2-Upper", "Basin-C", 1, 6.30, 80.55, 5.0, 6.0, 7.5, 380, 3, 2.30),
                      StationSpec("C3-Middle", "Basin-C", 2, 6.18, 80.40, 4.0, 4.5, 6.0, 760, 4, 1.90),
                      StationSpec("C4-Lower", "Basin-C", 3, 6.05, 80.22, 1.8, 2.4, 3.2, 1400, 5, 0.85,
                                   urbanisation=0.60, drainage_capacity_mm=44.0),
                  ]),
    ]


# ---------------------------------------------------------------------------
# Rainfall
# ---------------------------------------------------------------------------


def _monsoon_intensity(hours: pd.DatetimeIndex, monsoon: str) -> np.ndarray:
    """Seasonal storm-arrival multiplier, on the Sri Lankan bimodal cycle."""
    day_of_year = hours.dayofyear.to_numpy()
    phase = 2 * np.pi * day_of_year / 365.25
    if monsoon == "southwest":
        # Peak around late June (day ~175), secondary inter-monsoon peak in October.
        main = np.cos(phase - 2 * np.pi * 175 / 365.25)
        second = 0.45 * np.cos(2 * (phase - 2 * np.pi * 175 / 365.25))
    else:
        # Peak around late December (day ~355).
        main = np.cos(phase - 2 * np.pi * 355 / 365.25)
        second = 0.35 * np.cos(2 * (phase - 2 * np.pi * 355 / 365.25))
    return np.clip(0.55 + 0.75 * main + second, 0.05, None)


def _simulate_basin_rainfall(hours: pd.DatetimeIndex, basin: BasinSpec,
                             rng: np.random.Generator) -> np.ndarray:
    """Hourly catchment-average rainfall, mm, for one basin.

    Storms arrive as a Poisson process modulated by season. Each storm has a
    gamma-distributed total depth spread over a few hours, which produces the
    heavy-tailed distribution real rainfall has -- most hours dry, a few
    extreme.
    """
    n = len(hours)
    rain = np.zeros(n)
    intensity = _monsoon_intensity(hours, basin.monsoon)

    # Base arrival rate tuned so annual totals land in the 2000-3500 mm range
    # typical of Sri Lanka's wet zone.
    base_rate = 0.020
    arrivals = rng.random(n) < (base_rate * intensity)

    for start in np.flatnonzero(arrivals):
        depth = rng.gamma(shape=1.7, scale=13.0) * (0.6 + 0.8 * intensity[start])
        # Duration scales with depth: a 250 mm event is a monsoon depression
        # lasting most of a day, not a cloudburst. Without this coupling the
        # generator produces physically impossible hourly intensities -- 150 mm
        # in one hour, roughly twice anything ever recorded in Sri Lanka.
        duration = max(2, int(rng.gamma(shape=2.2, scale=2.4) + depth / 45.0))
        # Triangular hyetograph: build, peak, recede.
        profile = np.concatenate([
            np.linspace(0.3, 1.0, max(1, duration // 2)),
            np.linspace(1.0, 0.2, duration - max(1, duration // 2)),
        ])
        profile = profile / profile.sum()
        end = min(n, start + len(profile))
        rain[start:end] += depth * profile[: end - start]

    return rain


# ---------------------------------------------------------------------------
# Catchment response and routing
# ---------------------------------------------------------------------------


def _soil_and_runoff(rain: np.ndarray, basin: BasinSpec,
                     rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Single-bucket soil store. Returns (soil moisture mm, runoff mm).

    This is the component that makes antecedent conditions matter: an identical
    storm on a saturated store produces several times the runoff it would on a
    dry one, which is exactly the effect the Antecedent Precipitation Index
    feature is designed to capture.
    """
    n = len(rain)
    soil = np.zeros(n)
    runoff = np.zeros(n)
    store = basin.soil_capacity_mm * 0.35
    # Evapotranspiration, mm/h, a little higher in the dry season.
    et = 0.14
    drain = 1.0 / basin.recession_hours

    for i in range(n):
        store += rain[i]
        excess = max(0.0, store - basin.soil_capacity_mm)
        # Runoff coefficient rises steeply once the store fills.
        wetness = min(1.0, store / basin.soil_capacity_mm)
        quick = rain[i] * (0.06 + 0.62 * wetness ** 2.2)
        runoff[i] = quick + excess
        store -= excess + quick * 0.35 + et
        store = max(0.0, store)
        soil[i] = store

    return soil, runoff


def _route(runoff: np.ndarray, lag_hours: int, k: float) -> np.ndarray:
    """Linear reservoir routing: lag, then attenuate.

    Produces the characteristic asymmetric hydrograph -- fast rise, slow
    recession -- and gives each station pair a genuine, discoverable travel time.
    """
    n = len(runoff)
    lagged = np.zeros(n)
    if lag_hours > 0:
        lagged[lag_hours:] = runoff[:-lag_hours]
    else:
        lagged = runoff.copy()

    out = np.zeros(n)
    store = 0.0
    for i in range(n):
        store += lagged[i]
        release = store / k
        out[i] = release
        store -= release
    return out


def _stage_from_discharge(discharge: np.ndarray, station: StationSpec,
                          flood_quantile: float) -> np.ndarray:
    """Power-law rating curve, anchored on the station's design flood frequency.

    Real flood thresholds are not arbitrary heights: they are set so that a
    station reaches its minor flood level at roughly a known frequency. The same
    convention is applied here -- the reference discharge is the
    `flood_quantile` of that station's own discharge distribution, and the rating
    is scaled so that discharge maps exactly to the minor flood level.

    The consequence is that flood frequency is a stated parameter of the study
    rather than an accident of the parameter values, and every station in the
    network floods at a comparable rate despite spanning a twenty-fold range of
    catchment area.
    """
    reference = np.quantile(discharge[discharge > 0], flood_quantile) if (discharge > 0).any() else 1.0
    reference = max(reference, 1e-6)
    ratio = np.clip(discharge / reference, 0, None)
    rise = station.minor_flood_level_m - station.baseline_level_m
    return station.baseline_level_m + rise * np.power(ratio, station.rating_exponent)


# ---------------------------------------------------------------------------
# Crowd
# ---------------------------------------------------------------------------


def _local_flooding(station_rain: np.ndarray, station: StationSpec,
                    rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Localised flooding from drainage being overwhelmed.

    Returns (is_locally_flooded, latent drainage capacity).

    The capacity follows a slow mean-reverting random walk: silt and refuse
    accumulate over weeks, and occasional clearing restores it. That state is
    returned only so figures can show it -- it is never given to the model,
    because a deployed system would not know it either. Drain blockage is not
    telemetered anywhere in Sri Lanka, or anywhere else.
    """
    n = len(station_rain)
    # 3-hour rolling rainfall: urban drainage fails on short, intense bursts.
    rain_3h = pd.Series(station_rain).rolling(3, min_periods=1).sum().to_numpy()

    capacity = np.zeros(n)
    state = station.drainage_capacity_mm
    # Blockage accumulates slowly; clearing is a rare, discrete improvement.
    for i in range(n):
        state -= 0.004 * station.urbanisation
        if rng.random() < 1 / (24 * 45):          # cleared every ~45 days on average
            state = station.drainage_capacity_mm * rng.uniform(0.95, 1.1)
        state = float(np.clip(state, station.drainage_capacity_mm * 0.35,
                              station.drainage_capacity_mm * 1.2))
        capacity[i] = state

    # More concrete, less infiltration, so the effective load is higher.
    load = rain_3h * (0.55 + 0.9 * station.urbanisation)
    flooded = load > capacity

    # Water stands for an hour or two after the rain stops.
    persisted = flooded.copy()
    for lag in (1, 2):
        persisted[lag:] |= flooded[:-lag]
    return persisted, capacity


def _diurnal_activity(hours: pd.DatetimeIndex) -> np.ndarray:
    """Participants are awake and looking during the day. Nobody reports at 3am,
    which is a real weakness of crowdsourcing and must not be simulated away."""
    hour = hours.hour.to_numpy()
    return 0.18 + 0.82 * np.clip(np.sin((hour - 5) / 24 * 2 * np.pi), 0, None) ** 0.6


def _simulate_crowd(is_flooding: np.ndarray, activity: np.ndarray, panel_size: float,
                    detection_rate: float, false_report_rate: float,
                    rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    respondents = rng.poisson(np.maximum(panel_size * activity, 0.01))
    probability = np.where(is_flooding, detection_rate, false_report_rate)
    yes = rng.binomial(respondents, probability)
    return respondents, yes


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def simulate(config: SimulationConfig | None = None,
             network: list[BasinSpec] | None = None) -> pd.DataFrame:
    """Generate the full station-hour record.

    Returns one row per station per hour with the observed quantities a real
    deployment would collect -- water level, rainfall, crowd responses -- plus
    the latent state (soil moisture, discharge) which is retained ONLY for
    figures and diagnostics and is excluded from the model's feature set.
    """
    config = config or SimulationConfig()
    network = network or default_network()

    # THREE INDEPENDENT RANDOM STREAMS, not one.
    #
    # NumPy draws Poisson and Binomial variates by rejection sampling, so the
    # number of underlying random values consumed depends on the parameters. A
    # single shared stream therefore means that changing a crowd parameter
    # silently changes the weather generated afterwards -- which would confound
    # every sensitivity experiment, because the physical baseline would move
    # between cells that are supposed to differ only in the crowd.
    #
    # Separating the streams guarantees that the physical world is bit-identical
    # across any sweep over crowd parameters.
    rng = np.random.default_rng(config.seed)                 # weather and stage
    local_rng = np.random.default_rng(config.seed + 101)     # drainage state
    crowd_rng = np.random.default_rng(config.seed + 202)     # participant reports

    hours = pd.date_range(config.start, periods=config.years * HOURS_PER_YEAR, freq="h")
    frames = []

    for basin in network:
        catchment_rain = _simulate_basin_rainfall(hours, basin, rng)
        soil, runoff = _soil_and_runoff(catchment_rain, basin, rng)

        cumulative_lag = 0
        upstream_discharge = np.zeros(len(hours))

        for station in sorted(basin.stations, key=lambda s: s.position):
            cumulative_lag += station.travel_time_h

            # Local contribution scales with the incremental catchment area,
            # then the upstream flood wave is routed in on top of it.
            # Storage constants grow downstream: a large lowland reach holds
            # water far longer than a steep headwater, which is what makes a
            # downstream hydrograph broad and slow where a headwater one is
            # flashy. Values chosen so episode durations land in the 8-30 hour
            # range typical of the Kelani and Kalu.
            local = _route(runoff * (station.catchment_km2 / 900.0),
                           lag_hours=station.travel_time_h,
                           k=16.0 + 7.0 * station.position)
            discharge = local + 0.72 * _route(upstream_discharge,
                                              lag_hours=station.travel_time_h, k=13.0)
            upstream_discharge = discharge

            level = _stage_from_discharge(discharge, station, config.flood_quantile)
            level = level + rng.normal(0, config.level_noise_m, len(hours))

            # Station rain gauge: the catchment average plus local variation.
            station_rain = np.clip(
                catchment_rain * rng.normal(1.0, config.rain_gauge_noise, len(hours)), 0, None
            )

            # Sensor dropouts. Real gauges fail, and they fail most often during
            # the storms that matter, so dropout probability rises with stage.
            stress = np.clip((level - station.alert_level_m) /
                             max(0.1, station.major_flood_level_m - station.alert_level_m), 0, 1)
            dropped = rng.random(len(hours)) < (config.sensor_dropout_rate * (1 + 2.0 * stress))
            observed_level = np.where(dropped, np.nan, level)

            river_flood = level >= station.minor_flood_level_m
            local_flood, drainage_capacity = _local_flooding(station_rain, station, local_rng)
            # What a person standing in the cell would answer "yes" to.
            any_flood = river_flood | local_flood

            activity = _diurnal_activity(hours)
            respondents, yes = _simulate_crowd(
                any_flood, activity, station.panel_size,
                config.detection_rate, config.false_report_rate, crowd_rng
            )

            frames.append(pd.DataFrame({
                "hour": hours,
                "station": station.name,
                "basin": basin.name,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "alert_level_m": station.alert_level_m,
                "minor_flood_level_m": station.minor_flood_level_m,
                "major_flood_level_m": station.major_flood_level_m,
                "catchment_km2": station.catchment_km2,
                "position": station.position,
                # Observed quantities -- available to the model.
                "water_level_m": observed_level,
                "station_rain_mm": station_rain,
                "crowd_respondents": respondents,
                "crowd_yes": yes,
                # Latent state -- diagnostics and figures only, never a feature.
                "true_level_m": level,
                "river_flood": river_flood,
                "local_flood": local_flood,
                "any_flood": any_flood,
                "drainage_capacity_mm": drainage_capacity,
                "soil_moisture_mm": soil,
                "discharge": discharge,
                "catchment_rain_mm": catchment_rain,
            }))

    data = pd.concat(frames, ignore_index=True)
    return data.sort_values(["station", "hour"]).reset_index(drop=True)


def episode_table(data: pd.DataFrame, column: str = "any_flood") -> pd.DataFrame:
    """Contiguous flood episodes, from the latent true level.

    An episode -- not an hour -- is the unit lead time is measured against: a
    forecaster that warns four hours before a flood begins has provided four
    hours of lead time once, not once per hour of the event.
    """
    rows = []
    for station, group in data.groupby("station", sort=False):
        group = group.sort_values("hour").reset_index(drop=True)
        flooding = group[column].to_numpy().astype(bool)
        if not flooding.any():
            continue
        edges = np.diff(flooding.astype(int))
        starts = list(np.flatnonzero(edges == 1) + 1)
        ends = list(np.flatnonzero(edges == -1) + 1)
        if flooding[0]:
            starts.insert(0, 0)
        if flooding[-1]:
            ends.append(len(flooding))
        for start, end in zip(starts, ends):
            rows.append({
                "station": station,
                "basin": group.loc[start, "basin"],
                "onset": group.loc[start, "hour"],
                "end": group.loc[end - 1, "hour"],
                "duration_h": end - start,
                "peak_level_m": group.loc[start:end - 1, "true_level_m"].max(),
                "river_driven": bool(group.loc[start:end - 1, "river_flood"].any()),
                "local_only": bool(not group.loc[start:end - 1, "river_flood"].any()),
                "reached_major": bool(
                    (group.loc[start:end - 1, "true_level_m"]
                     >= group.loc[start:end - 1, "major_flood_level_m"]).any()
                ),
            })
    return pd.DataFrame(rows).sort_values("onset").reset_index(drop=True)
