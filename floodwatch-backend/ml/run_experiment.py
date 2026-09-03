"""Run the full experiment and write every result and figure.

    python -m ml.run_experiment --out thesis

Pipeline:
    1. simulate the deployment            (ml/simulator.py)
    2. split temporally, 70/15/15         no random splits anywhere
    3. estimate travel times on TRAIN     then engineer features
    4. fit baselines and models           B1, B2, M1, M2
    5. evaluate on the held-out test      PR/ROC/calibration/lead time
    6. ablation and crowd sensitivity
    7. leave-one-basin-out                spatial generalisation
    8. write results.json, tables, figures

Every number in the thesis comes from this script. Re-running it with the same
seed reproduces every figure exactly.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score

from ml import evaluate, features, figures
from ml.models import (
    FloodClassifier,
    baseline_persistence,
    baseline_rainfall_threshold,
    make_models,
)
from ml.simulator import SimulationConfig, default_network, episode_table, simulate

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("experiment")

TARGET = "y_any_6h"   # flooding of any kind, the question the app actually asks
PHYSICAL_GROUPS = ["river", "upstream", "rainfall", "temporal"]


def temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """70/15/15 by time. Never at random.

    A random split puts hour 14 of a flood in training and hour 15 in test; the
    model memorises the event and scores superbly on data it has effectively
    already seen. This is the most common way a flood-prediction result becomes
    meaningless, and it is invisible unless you look for it.
    """
    hours = np.sort(df["hour"].unique())
    train_end = hours[int(len(hours) * 0.70)]
    val_end = hours[int(len(hours) * 0.85)]
    return (df[df["hour"] < train_end].copy(),
            df[(df["hour"] >= train_end) & (df["hour"] < val_end)].copy(),
            df[df["hour"] >= val_end].copy())


def network_order(network) -> dict[str, list[str]]:
    return {
        basin.name: [s.name for s in sorted(basin.stations, key=lambda s: s.position)]
        for basin in network
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="thesis")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--quick", action="store_true", help="skip the crowd sensitivity sweep")
    args = parser.parse_args()

    out = Path(args.out)
    figures_dir = out / "figures"
    tables_dir = out / "tables"
    for directory in (out, figures_dir, tables_dir):
        directory.mkdir(parents=True, exist_ok=True)

    started = time.time()
    results: dict = {"config": {"years": args.years, "seed": args.seed, "target": TARGET}}

    # -- 1. simulate ------------------------------------------------------
    log.info("1/8  Simulating a %d-year deployment...", args.years)
    network = default_network()
    config = SimulationConfig(years=args.years, seed=args.seed)
    raw = simulate(config, network)
    episodes = episode_table(raw)
    log.info("     %s station-hours, %d stations, %d flood episodes",
             f"{len(raw):,}", raw["station"].nunique(), len(episodes))

    results["dataset"] = {
        "station_hours": int(len(raw)),
        "stations": int(raw["station"].nunique()),
        "basins": int(raw["basin"].nunique()),
        "years": args.years,
        "period": [str(raw["hour"].min()), str(raw["hour"].max())],
        "flood_episodes": int(len(episodes)),
        "episodes_reaching_major": int(episodes["reached_major"].sum()),
        "median_episode_duration_h": float(episodes["duration_h"].median()),
        "sensor_dropout_rate": float(raw["water_level_m"].isna().mean()),
    }

    # -- 2. split ---------------------------------------------------------
    log.info("2/8  Splitting temporally (70/15/15)...")
    train_raw, val_raw, test_raw = temporal_split(raw)

    # -- 3. features ------------------------------------------------------
    log.info("3/8  Estimating travel times on TRAIN, then engineering features...")
    order = network_order(network)
    train_features_only = features.build(train_raw, lag_table={})
    lag_table = features.estimate_travel_times(train_features_only, order)
    log.info("     recovered lags: %s",
             {k: v[1] for k, v in sorted(lag_table.items())})
    results["travel_times"] = {k: {"upstream": v[0], "lag_h": int(v[1])}
                               for k, v in lag_table.items()}
    truth = {}
    for basin in network:
        for station in basin.stations:
            if station.position > 0:
                truth[station.name] = station.travel_time_h
    results["travel_times_planted"] = truth

    full = features.build(raw, lag_table)
    train, val, test = temporal_split(full)
    for split_name, frame in (("train", train), ("validation", val), ("test", test)):
        results.setdefault("splits", {})[split_name] = {
            "rows": int(len(frame)),
            "from": str(frame["hour"].min()),
            "to": str(frame["hour"].max()),
            "positive_rate": float(frame[TARGET].mean()),
        }

    physical = features.feature_columns(PHYSICAL_GROUPS)
    crowd = features.feature_columns(["crowd"])
    results["feature_counts"] = {"physical": len(physical), "crowd": len(crowd),
                                 "total": len(physical) + len(crowd)}

    # -- 4. fit -----------------------------------------------------------
    log.info("4/8  Fitting models...")
    models = make_models(physical, crowd)
    for name, model in models.items():
        log.info("     %s (%d features)", name, len(model.features))
        model.fit(train, TARGET, validation=val)

    test_usable = test[test[TARGET].notna()].copy()
    val_usable = val[val[TARGET].notna()].copy()
    y_test = test_usable[TARGET].to_numpy(dtype=int)
    y_val = val_usable[TARGET].to_numpy(dtype=int)

    predictions = {
        "B1 Rainfall threshold": baseline_rainfall_threshold(test_usable),
        "B2 Persistence": baseline_persistence(test_usable),
    }
    validation_predictions = {
        "B1 Rainfall threshold": baseline_rainfall_threshold(val_usable),
        "B2 Persistence": baseline_persistence(val_usable),
    }
    for name, model in models.items():
        predictions[name] = model.predict(test_usable)
        validation_predictions[name] = model.predict(val_usable)

    # -- 5. evaluate ------------------------------------------------------
    log.info("5/8  Evaluating on the held-out test split...")
    table_rows = []
    pr_curves, roc_curves, ap_scores, auc_scores, calibrations = {}, {}, {}, {}, {}
    lead_tables = {}

    test_episodes = episodes[
        (episodes["onset"] >= test["hour"].min()) & (episodes["onset"] <= test["hour"].max())
    ]
    results["test_episodes"] = int(len(test_episodes))

    for name, prediction in predictions.items():
        # Operating point chosen on VALIDATION, applied unchanged to TEST.
        threshold = evaluate.choose_threshold(y_val, validation_predictions[name].scores)
        metrics = evaluate.classification_metrics(y_test, prediction.scores, threshold)
        low, high = evaluate.bootstrap_ci(y_test, prediction.scores)
        metrics["pr_auc_ci95"] = [low, high]
        metrics["brier"] = evaluate.brier(y_test, prediction.scores) if prediction.is_probabilistic else None

        leads = evaluate.lead_times(test_usable, prediction.scores, test_episodes, threshold)
        lead_tables[name] = leads
        metrics.update({f"lead_{k}": v for k, v in evaluate.lead_time_summary(leads).items()})

        results.setdefault("models", {})[name] = metrics
        table_rows.append({"model": name, **metrics})

        pr_curves[name] = evaluate.pr_curve(y_test, prediction.scores)
        roc_curves[name] = evaluate.roc_points(y_test, prediction.scores)
        ap_scores[name] = metrics["pr_auc"]
        auc_scores[name] = metrics["roc_auc"]
        if prediction.is_probabilistic:
            calibrations[name] = evaluate.calibration_bins(y_test, prediction.scores)

        log.info("     %-24s AP %.3f  ROC %.3f  P %.3f  R %.3f  FAR %.3f  lead %.1f h",
                 name, metrics["pr_auc"], metrics["roc_auc"], metrics["precision"],
                 metrics["recall"], metrics["far"], metrics.get("lead_median_lead_h", float("nan")))

    m1, m2 = results["models"]["M1 Physical"], results["models"]["M2 Physical + crowd"]
    results["crowd_contribution"] = {
        "delta_pr_auc": m2["pr_auc"] - m1["pr_auc"],
        "relative_pr_auc_gain": evaluate.skill_score(m2["pr_auc"], m1["pr_auc"]),
        "delta_recall": m2["recall"] - m1["recall"],
        "delta_far": m2["far"] - m1["far"],
        "delta_median_lead_h": (m2.get("lead_median_lead_h", np.nan)
                                - m1.get("lead_median_lead_h", np.nan)),
        "delta_detection_rate": (m2.get("lead_detection_rate", np.nan)
                                 - m1.get("lead_detection_rate", np.nan)),
    }

    # -- 6. ablation and importance --------------------------------------
    log.info("6/8  Ablation study...")
    ablation_rows = []
    ABLATION_SEEDS = 3   # differences of +-0.02 AP are within single-seed noise
    configurations = {
        "All features": PHYSICAL_GROUPS + ["crowd"],
        "− crowd": PHYSICAL_GROUPS,
        "− upstream": [g for g in PHYSICAL_GROUPS if g != "upstream"] + ["crowd"],
        "− rainfall": [g for g in PHYSICAL_GROUPS if g != "rainfall"] + ["crowd"],
        "− temporal": [g for g in PHYSICAL_GROUPS if g != "temporal"] + ["crowd"],
        "River state only": ["river"],
    }
    for label, groups in configurations.items():
        columns = features.feature_columns(groups)
        scores = []
        for offset in range(ABLATION_SEEDS):
            model = FloodClassifier(label, columns)
            model.fit(train, TARGET, validation=val)
            scores.append(float(average_precision_score(
                y_test, model.predict(test_usable).scores)))
            # Re-seed the learner so the repeats differ; the data is fixed, so
            # this measures the model's own variance, which is what makes a
            # small ablation difference interpretable.
            from ml import models as _models
            _models.GBM_PARAMS["random_state"] = args.seed + offset + 1
        from ml import models as _models
        _models.GBM_PARAMS["random_state"] = args.seed
        ablation_rows.append({"configuration": label, "groups": ",".join(groups),
                              "n_features": len(columns),
                              "pr_auc": float(np.mean(scores)),
                              "pr_auc_sd": float(np.std(scores))})
        log.info("     %-20s AP %.3f +- %.3f  (%d features)",
                 label, np.mean(scores), np.std(scores), len(columns))
    ablation = pd.DataFrame(ablation_rows)
    results["ablation"] = ablation_rows

    log.info("     Permutation importance...")
    best = models["M2 Physical + crowd"]
    sample = test_usable.sample(min(20000, len(test_usable)), random_state=args.seed)
    importance_raw = permutation_importance(
        best.model, sample[best.features].to_numpy(dtype=float),
        sample[TARGET].to_numpy(dtype=int),
        scoring="average_precision", n_repeats=5, random_state=args.seed, n_jobs=1,
    )
    group_of = {c: g for g, cols in features.FEATURE_GROUPS.items() for c in cols}
    importance = pd.DataFrame({
        "feature": best.features,
        "importance": importance_raw.importances_mean,
        "std": importance_raw.importances_std,
        "group": [group_of.get(c, "other") for c in best.features],
    }).sort_values("importance", ascending=False).reset_index(drop=True)
    results["top_features"] = importance.head(15).to_dict("records")

    # -- 7. crowd sensitivity and spatial holdout ------------------------
    sensitivity = pd.DataFrame()
    if not args.quick:
        log.info("7/8  Crowd sensitivity sweep (this is the slow part)...")
        sensitivity = crowd_sensitivity(args, network, physical, crowd, order)
        results["crowd_sensitivity"] = sensitivity.to_dict("records")
    else:
        log.info("7/8  Skipping crowd sensitivity (--quick).")

    # -- 7b. where does the crowd actually earn its place? ----------------
    log.info("     Stratifying by gauge availability...")
    results["gauge_availability"] = gauge_availability_analysis(
        test_usable, y_test, predictions["M1 Physical"].scores,
        predictions["M2 Physical + crowd"].scores
    )
    for stratum, row in results["gauge_availability"].items():
        log.info("       %-22s n=%7d  M1 %.3f -> M2 %.3f  (%+.3f)",
                 stratum, row["rows"], row["pr_auc_m1"], row["pr_auc_m2"], row["delta"])

    log.info("     Sensor-outage scenario (35%% dropout)...")
    results["outage_scenario"] = outage_scenario(args, network, physical, crowd, order)
    log.info("       M1 %.3f -> M2 %.3f  (%+.3f)",
             results["outage_scenario"]["pr_auc_m1"],
             results["outage_scenario"]["pr_auc_m2"],
             results["outage_scenario"]["delta"])

    log.info("     Leave-one-basin-out...")
    holdout_rows = []
    # Evaluated over validation + test together. The test split alone is roughly
    # five months and, depending where it falls in the monsoon cycle, can contain
    # too few flood episodes in a given basin for average precision to be
    # meaningful. Both arms of the comparison use identical rows.
    evaluation_window = full[full["hour"] >= val["hour"].min()]
    evaluation_window = evaluation_window[evaluation_window[TARGET].notna()]

    seen_model = FloodClassifier("all-basins", physical + crowd).fit(
        temporal_split(full)[0], TARGET, validation=val
    )
    for basin in raw["basin"].unique():
        rows = evaluation_window[evaluation_window["basin"] == basin]
        y_basin = rows[TARGET].to_numpy(dtype=int)
        if y_basin.sum() < 10:
            log.warning("     %-10s only %d positives -- skipped", basin, int(y_basin.sum()))
            continue

        inside = full[full["basin"] != basin]
        in_train, in_val, _ = temporal_split(inside)
        unseen_model = FloodClassifier(f"holdout-{basin}", physical + crowd).fit(
            in_train, TARGET, validation=in_val
        )

        within = float(average_precision_score(y_basin, seen_model.predict(rows).scores))
        holdout = float(average_precision_score(y_basin, unseen_model.predict(rows).scores))
        holdout_rows.append({"basin": basin, "within": within, "holdout": holdout,
                             "positives": int(y_basin.sum()), "rows": int(len(rows))})
        log.info("     %-10s seen %.3f   unseen %.3f   (%d positives)",
                 basin, within, holdout, int(y_basin.sum()))
    spatial = pd.DataFrame(holdout_rows)
    results["spatial_holdout"] = holdout_rows

    # -- 8. write ---------------------------------------------------------
    log.info("8/8  Writing figures and tables to %s/ ...", out)

    balance = pd.DataFrame([
        {"label": "Any flooding, 6 h", "positives": int(full["y_any_6h"].sum()),
         "rows": int(full["y_any_6h"].notna().sum()),
         "positive_rate": float(full["y_any_6h"].mean())},
        {"label": "Any flooding, 24 h", "positives": int(full["y_any_24h"].sum()),
         "rows": int(full["y_any_24h"].notna().sum()),
         "positive_rate": float(full["y_any_24h"].mean())},
        {"label": "River minor flood, 6 h", "positives": int(full["y_minor_6h"].sum()),
         "rows": int(full["y_minor_6h"].notna().sum()),
         "positive_rate": float(full["y_minor_6h"].mean())},
        {"label": "River major flood, 6 h", "positives": int(full["y_major_6h"].sum()),
         "rows": int(full["y_major_6h"].notna().sum()),
         "positive_rate": float(full["y_major_6h"].mean())},
    ])

    biggest = episodes.loc[episodes["peak_level_m"].idxmax()]
    figures.fig_hydrograph(raw, biggest["station"], biggest["onset"], figures_dir)
    figures.fig_seasonality(raw, figures_dir)
    figures.fig_class_balance(balance, figures_dir)
    figures.fig_pr_curves(pr_curves, float(y_test.mean()), ap_scores, figures_dir)
    figures.fig_roc_curves(roc_curves, auc_scores, figures_dir)
    figures.fig_calibration(calibrations, figures_dir)
    figures.fig_importance(importance, figures_dir)
    figures.fig_ablation(ablation, figures_dir)
    figures.fig_lead_time({k: v for k, v in lead_tables.items() if k.startswith("M")}, figures_dir)
    if not sensitivity.empty:
        figures.fig_crowd_sensitivity(sensitivity, figures_dir)
    figures.fig_confusion(results["models"]["M2 Physical + crowd"], figures_dir,
                          "M2 Physical + crowd, 6-hour horizon")
    figures.fig_spatial_holdout(spatial, figures_dir)

    pd.DataFrame(table_rows).to_csv(tables_dir / "table1_model_comparison.csv", index=False)
    ablation.to_csv(tables_dir / "table2_ablation.csv", index=False)
    importance.to_csv(tables_dir / "table3_feature_importance.csv", index=False)
    spatial.to_csv(tables_dir / "table4_spatial_holdout.csv", index=False)
    if not sensitivity.empty:
        sensitivity.to_csv(tables_dir / "table5_crowd_sensitivity.csv", index=False)
    episodes.to_csv(tables_dir / "flood_episodes.csv", index=False)

    results["runtime_seconds"] = round(time.time() - started, 1)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))

    log.info("")
    log.info("Done in %.0fs. results.json, %d figures, %d tables.",
             results["runtime_seconds"], len(list(figures_dir.glob("*.pdf"))),
             len(list(tables_dir.glob("*.csv"))))
    return 0


def gauge_availability_analysis(test: pd.DataFrame, y: np.ndarray,
                                scores_m1: np.ndarray, scores_m2: np.ndarray) -> dict:
    """Does the crowd help more when the gauge is not reporting?

    The hypothesis behind crowdsourcing is not that people are better than
    instruments. It is that people are present where instruments are not, and
    still present when instruments fail. If that is true, the crowd's
    contribution should concentrate in the hours when the gauge is stale --
    which is exactly what this splits out.

    Note that dropout probability rises with river stage in the simulator, for
    the same reason it does in the field: gauges fail during the storms that
    matter most.
    """
    age = test["level_age_h"].fillna(99).to_numpy()
    strata = {
        "gauge fresh (age 0 h)": age == 0,
        "gauge stale (1-2 h)": (age >= 1) & (age <= 2),
        "gauge lost (>= 3 h)": age >= 3,
    }

    out = {}
    for label, mask in strata.items():
        if mask.sum() < 200 or len(np.unique(y[mask])) < 2:
            continue
        ap1 = float(average_precision_score(y[mask], scores_m1[mask]))
        ap2 = float(average_precision_score(y[mask], scores_m2[mask]))
        out[label] = {
            "rows": int(mask.sum()),
            "positives": int(y[mask].sum()),
            "base_rate": float(y[mask].mean()),
            "pr_auc_m1": ap1,
            "pr_auc_m2": ap2,
            "delta": ap2 - ap1,
        }
    return out


def outage_scenario(args, network, physical, crowd, order,
                    dropout: float = 0.35) -> dict:
    """Re-run the whole experiment with a badly degraded gauge network.

    Sri Lankan telemetry is not always reliable, and a system that only works
    while every sensor is up is not much use in a disaster. This scenario keeps
    the physical world identical -- same seed, same weather, same floods -- and
    changes only how much of it the instruments manage to report.
    """
    config = SimulationConfig(years=args.years, seed=args.seed, sensor_dropout_rate=dropout)
    raw = simulate(config, network)
    lag_table = features.estimate_travel_times(
        features.build(temporal_split(raw)[0], {}), order
    )
    full = features.build(raw, lag_table)
    train, val, test = temporal_split(full)
    usable = test[test[TARGET].notna()]
    y = usable[TARGET].to_numpy(dtype=int)

    m1 = FloodClassifier("m1", physical).fit(train, TARGET, validation=val)
    m2 = FloodClassifier("m2", physical + crowd).fit(train, TARGET, validation=val)
    ap1 = float(average_precision_score(y, m1.predict(usable).scores))
    ap2 = float(average_precision_score(y, m2.predict(usable).scores))
    return {
        "dropout_rate": dropout,
        "observed_missing_rate": float(raw["water_level_m"].isna().mean()),
        "pr_auc_m1": ap1,
        "pr_auc_m2": ap2,
        "delta": ap2 - ap1,
        "relative_gain": (ap2 - ap1) / ap1 if ap1 else float("nan"),
    }


def crowd_sensitivity(args, network, physical, crowd, order) -> pd.DataFrame:
    """Re-simulate with different crowd parameters and re-measure the gain.

    The physical world is bit-identical across every cell -- the simulator draws
    weather, drainage state and crowd reports from three independent random
    streams, so changing a crowd parameter cannot perturb the hydrology. The
    physical-only column is therefore constant by construction, and any movement
    in it would be a bug rather than a finding.
    """
    rows = []
    for panel_size in (5, 15, 40):
        for detection in (0.4, 0.6, 0.8):
            config = SimulationConfig(
                years=args.years, seed=args.seed,
                detection_rate=detection, false_report_rate=0.04,
            )
            scaled = [
                type(basin)(basin.name,
                            [type(s)(**{**s.__dict__, "panel_size": panel_size})
                             for s in basin.stations],
                            basin.monsoon, basin.soil_capacity_mm, basin.recession_hours)
                for basin in network
            ]
            raw = simulate(config, scaled)
            full = features.build(raw, features.estimate_travel_times(
                features.build(temporal_split(raw)[0], {}), order))
            train, val, test = temporal_split(full)
            test_usable = test[test[TARGET].notna()]
            y = test_usable[TARGET].to_numpy(dtype=int)

            physical_only = FloodClassifier("p", physical).fit(train, TARGET, validation=val)
            with_crowd = FloodClassifier("pc", physical + crowd).fit(train, TARGET, validation=val)
            ap_physical = float(average_precision_score(y, physical_only.predict(test_usable).scores))
            ap_crowd = float(average_precision_score(y, with_crowd.predict(test_usable).scores))

            rows.append({
                "panel_size": panel_size,
                "detection_rate": detection,
                "pr_auc_physical": ap_physical,
                "pr_auc_with_crowd": ap_crowd,
                "delta_pr_auc": ap_crowd - ap_physical,
            })
            log.info("       panel %2d  detect %.0f%%   AP %.3f -> %.3f  (%+.3f)",
                     panel_size, detection * 100, ap_physical, ap_crowd,
                     ap_crowd - ap_physical)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    raise SystemExit(main())
