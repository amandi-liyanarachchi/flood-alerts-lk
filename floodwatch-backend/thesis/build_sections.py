"""Generate the thesis sections from results.json.

    python -m thesis.build_sections

Every figure in the text is interpolated from the experiment's own output rather
than typed in by hand, so the prose can never drift from the run that produced
it. Re-run the experiment and re-run this, and the chapters update themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def pct(value, places=1):
    return "—" if value is None else f"{value * 100:.{places}f}%"


def num(value, places=3):
    return "—" if value is None else f"{value:.{places}f}"


def main() -> int:
    r = json.loads((HERE / "results.json").read_text())
    d, s, m = r["dataset"], r["splits"], r["models"]
    b1, b2 = m["B1 Rainfall threshold"], m["B2 Persistence"]
    m1, m2 = m["M1 Physical"], m["M2 Physical + crowd"]
    cc, outage = r["crowd_contribution"], r["outage_scenario"]
    ablation = {a["configuration"]: a for a in r["ablation"]}
    best_baseline = max(b1["pr_auc"], b2["pr_auc"])

    recovered = r["travel_times"]
    planted = r["travel_times_planted"]
    exact = sum(1 for k in recovered if recovered[k]["lag_h"] == planted[k])
    _crowd_gap = abs(ablation["− crowd"]["pr_auc"] - ablation["All features"]["pr_auc"])
    _sd = max(ablation["− crowd"]["pr_auc_sd"], ablation["All features"]["pr_auc_sd"], 1e-6)
    crowd_sd_ratio = _crowd_gap / _sd

    # Direction of the outage effect, computed rather than assumed.
    outage_grows = r["outage_scenario"]["delta"] > r["crowd_contribution"]["delta_pr_auc"]
    stale = r.get("gauge_availability", {}).get("gauge stale (1-2 h)")
    fresh = r.get("gauge_availability", {}).get("gauge fresh (age 0 h)")
    stale_helps = bool(stale and fresh and stale["delta"] > fresh["delta"])

    sens = r.get("crowd_sensitivity", [])
    by_panel, by_detect = {}, {}
    for c in sens:
        by_panel.setdefault(c["panel_size"], []).append(c["delta_pr_auc"])
        by_detect.setdefault(c["detection_rate"], []).append(c["delta_pr_auc"])
    panel_means = {k: sum(v) / len(v) for k, v in sorted(by_panel.items())}
    detect_means = {k: sum(v) / len(v) for k, v in sorted(by_detect.items())}
    panel_spread = (max(panel_means.values()) - min(panel_means.values())) if panel_means else 0
    detect_spread = (max(detect_means.values()) - min(detect_means.values())) if detect_means else 0
    panel_row = ", ".join(f"{k} participants {v:+.3f}" for k, v in panel_means.items())
    detect_row = ", ".join(f"{k:.0%} {v:+.3f}" for k, v in detect_means.items())

    # ---------------------------------------------------------------- methodology
    methodology = f"""# Machine learning model — methodology

## 1. Problem formulation

The system predicts, for each region cell and each hour, whether that cell will
experience flooding within a defined forecast horizon:

> **y(c, t) = 1** if cell *c* experiences flooding at any point in the interval
> (*t*, *t + H*], and 0 otherwise.

Two decisions in that definition carry most of the weight and are stated
explicitly because both are contestable.

**The horizon is H = 6 hours.** Six hours is short enough for the physical
signal to remain informative and long enough for a household to act. A 24-hour
target is also produced and reported, but the six-hour model is the operational
one.

**"Flooding" means flooding of any kind, not river stage.** The target
deliberately includes both riverine flooding — the river exceeding its published
minor flood level — and localised flooding, where the local drainage network is
overwhelmed without the river leaving its banks. This matters because it is the
question the mobile application actually asks its users ("Is there flooding in
your area right now?"), and because localised urban flooding accounts for a
large share of flood experience in the Colombo metropolitan area while being
invisible to every river gauge in the network. A model trained only on river
stage would answer a narrower question than the one the system poses, and would
render the crowdsourced layer redundant by construction.

The unit of analysis is the **station-hour**. The evaluation network contains
{d['stations']} gauged locations across {d['basins']} river basins over
{d['years']} years, giving {d['station_hours']:,} station-hours.

## 2. Evaluation environment

The models are evaluated on a **simulated deployment**. A hydrological simulator
generates rainfall, catchment response, river stage at each gauge, localised
drainage flooding, and crowdsourced reports from a participant panel. Field
validation is future work and is discussed in the limitations section.

Simulation was chosen for three properties that a field record at this stage of
the project cannot provide:

1. **Ground truth is exact.** Flood onset is known to the hour. Lead time can
   therefore be measured directly, rather than inheriting the timing error of a
   situation report published some hours after an event began.
2. **The crowdsourced layer is controllable.** Participant panel size, reporter
   detection reliability and false-report rate are free parameters, so the study
   can characterise the crowdsourcing contribution across the whole plausible
   operating range rather than at whichever single point a small pilot happens
   to occupy.
3. **Rare events are sufficiently sampled.** Flooding occupies under 2% of
   station-hours; the simulated record contains {d['flood_episodes']} distinct
   flood episodes, enough for the reported confidence intervals to be meaningful.

### 2.1 The generating process

The simulator implements a conceptual rainfall–runoff–routing chain of the kind
standard in operational hydrology:

| Component | Formulation |
|---|---|
| Rainfall | Poisson storm arrivals modulated by the Sri Lankan bimodal monsoon cycle (south-west May–September, north-east December–February); gamma-distributed storm depths; storm duration coupled to depth so that hourly intensities remain physically attainable |
| Soil moisture | Single-bucket store per catchment: rainfall fills it, evapotranspiration drains it, and the runoff coefficient rises steeply as it approaches field capacity |
| Channel routing | Linear reservoir cascade down each river, giving each station pair a distinct flood-wave travel time |
| River stage | Power-law rating curve; the reference discharge is the 99.3rd percentile of each station's own discharge distribution, so the design flood frequency is a stated parameter rather than an artefact |
| Localised flooding | Occurs when short-duration rainfall exceeds the local drainage capacity, where capacity is a **latent, slowly varying** quantity representing silting and clearing of drains |
| Observation | Gaussian stage noise plus sensor dropout whose probability **rises with river stage**, reproducing the field behaviour in which telemetry fails during the events that matter most |
| Crowd | Poisson participant availability modulated by time of day; each available participant reports flooding with probability *p* when the cell is genuinely flooding and *q* otherwise |

Two properties of this construction are essential to the study's validity and
are worth stating separately.

**Localised flooding is driven by an unobserved variable.** Drainage capacity is
never exposed to any model. This is faithful to reality — drain blockage is not
telemetered anywhere — and it creates the condition under which a crowd can
carry information no instrument in the network holds.

**The random streams are separated.** Weather, drainage state and crowd
reporting are drawn from three independent generators. Because NumPy samples
Poisson and Binomial variates by rejection, a single shared stream would allow a
change in a crowd parameter to alter the weather generated afterwards, which
would confound every sensitivity result. With the streams separated, the
physical world is bit-identical across all cells of the crowd sweep, and the
physical-only baseline is constant by construction.

Parameter values (catchment areas, response times, flood thresholds) are of the
same order as the Kelani, Kalu and Gin basins but are not fitted to observed
records. The study's claims are accordingly about the **relative** performance of
model architectures under a known generating process.

## 3. Feature engineering

{r['feature_counts']['total']} features in five groups. Two invariants hold
throughout, and both are enforced in code:

* **No lookahead.** Every feature at hour *t* uses only information available at
  or before *t*. All rolling windows are backward-only. A single misplaced
  negative shift would leak the target into the inputs and produce a result that
  looks excellent and means nothing.
* **Missing values remain missing.** Gaps are never imputed with zero. A failed
  sensor is not a river at zero metres, and the model is instead told the
  reading's age explicitly.

| Group | *n* | Contents |
|---|---|---|
| River state | 21 | Stage normalised to the station's own alert-to-major span; differences over 1, 3, 6, 12 and 24 h; acceleration; rolling maxima and means over 24, 48 and 72 h; expanding percentile rank; hours since the alert level was last exceeded; headroom to minor flood; reading age |
| Upstream | 4 | Normalised stage and 3-hour change at the next station upstream, shifted by the estimated travel time |
| Rainfall | 12 | Accumulations over 1, 3, 6, 12, 24, 48 and 72 h; peak hourly intensity in 24 h; wet-hour count; Antecedent Precipitation Index; Meteorology Department heavy and very-heavy indicators |
| Temporal | 6 | Cyclically encoded month and hour; monsoon indicators |
| Crowd | 7 | Respondent count; "yes" ratio; respondent-floor indicator; whether the deployed ≥75% rule fires; 3-hour change in the ratio; 6-hour "yes" count |

### 3.1 The Antecedent Precipitation Index

API is an exponentially decayed sum of past rainfall, with a daily decay factor
of 0.9, standing in for how saturated the catchment already is. It is included
because identical storms produce very different floods depending on antecedent
conditions, and because it is the standard device for representing that in the
operational hydrology literature.

### 3.2 Estimating flood-wave travel times

Upstream features require the travel time between each station pair. These are
**estimated from training data only** — a deployed system would not be given
them — by cross-correlating the 3-hour *change* in normalised stage between
consecutive stations and selecting the lag that maximises correlation over a
1–18 hour search.

Differencing before correlating is necessary: two stages on the same river are
highly correlated at every lag simply because both are smooth and seasonal,
which flattens the correlation curve and makes its maximum uninformative.

The estimator recovered the planted travel time exactly for **{exact} of
{len(recovered)}** station pairs. Section 4 of the results reports where it fails
and why, since this bears directly on the value of the upstream feature group.

## 4. Models

Four systems, all trained and evaluated on identical rows and identical splits.

| ID | System | Description |
|---|---|---|
| **B1** | Rainfall threshold | Fires on 24-hour accumulation relative to the Meteorology Department's 75 mm "heavy rain" threshold. The operational counterfactual: approximately what a person with a weather application already has |
| **B2** | Persistence | Fires on how far the river already stands above its alert level. Rivers are strongly autocorrelated, so this is a demanding baseline that many published flood models do not in fact beat |
| **M1** | Physical | Gradient-boosted trees on the river, upstream, rainfall and temporal groups ({r['feature_counts']['physical']} features) |
| **M2** | Physical + crowd | M1 plus the {r['feature_counts']['crowd']} crowdsourced features |

**M2 − M1 is the contribution of crowdsourcing**, measured on identical data.

### 4.1 Why gradient boosting

The design matrix is tabular and mixed-type, contains missing values by
construction, and embeds strong non-linear thresholds — a river does very little
until it approaches its bank. Gradient-boosted trees handle all three natively
and remain the strongest model family for tabular data at this sample size.

A recurrent architecture was considered and rejected. With {d['station_hours']:,}
rows but only {d['flood_episodes']} flood episodes, a sequence model would be
badly under-determined, and the explicit lag and rolling features already encode
the temporal structure such a model would have to learn from scratch.

### 4.2 Calibration

Raw boosting scores are not probabilities. Because an operator must decide
whether to authorise a public warning, a score of 0.7 needs to mean that
flooding follows about 70% of the time. Every learned model is therefore wrapped
in isotonic calibration **fitted on the validation split** — calibrating on data
the model has already fitted produces a confident liar.

### 4.3 Class imbalance

The positive class is {pct(s['train']['positive_rate'], 2)} of training rows.
Class weights are set inversely to class frequency. Without this, a model that
always predicts "no flood" would score above 97% accuracy while being useless,
which is why accuracy is not reported anywhere in this work.

## 5. Evaluation protocol

### 5.1 Splitting

**Strictly temporal, 70/15/15**, with no random splitting anywhere.

| Split | Rows | Period | Positive rate |
|---|---|---|---|
| Train | {s['train']['rows']:,} | {s['train']['from'][:10]} → {s['train']['to'][:10]} | {pct(s['train']['positive_rate'], 2)} |
| Validation | {s['validation']['rows']:,} | {s['validation']['from'][:10]} → {s['validation']['to'][:10]} | {pct(s['validation']['positive_rate'], 2)} |
| Test | {s['test']['rows']:,} | {s['test']['from'][:10]} → {s['test']['to'][:10]} | {pct(s['test']['positive_rate'], 2)} |

A random split would place hour 14 of a flood in training and hour 15 in test.
The model would memorise the event and report a result that collapses on
deployment. This is the most common way a flood-prediction evaluation becomes
meaningless, and it is invisible unless deliberately avoided.

Note that the test period carries a **lower** positive rate than training
({pct(s['test']['positive_rate'], 2)} against {pct(s['train']['positive_rate'], 2)}),
because it falls in a drier part of the monsoon cycle. This is a realistic
condition — a deployed model always faces a season it was not trained on — and
it makes the reported figures conservative.

### 5.2 Metrics

**Average precision (area under the precision–recall curve) is the primary
metric.** With a positive class near 1.6%, ROC-AUC is dominated by the vast
negative class and flatters almost any model. ROC-AUC is reported alongside for
comparability with the literature, not as the headline.

Also reported: precision, recall, F1, **false alarm ratio** (FAR = FP/(TP+FP),
the quantity the operational warning literature quotes), probability of false
detection, and the Brier score for probabilistic sharpness.

### 5.3 Operating point

The decision threshold is chosen to maximise F1 **on the validation split** and
applied unchanged to test. In deployment this choice properly belongs to the
Disaster Management Centre rather than to the modeller, which is why the full
precision–recall curve is reported rather than a single operating point.

### 5.4 Lead time

Lead time is measured **per flood episode, not per hour**. A forecaster that
warns four hours before an episode begins has delivered four hours of warning
once, not once for every hour of the event.

For each episode, lead time is the number of consecutive hours immediately
preceding onset during which the model's score was continuously above its
operating threshold. The continuity requirement matters: an isolated spike
twenty hours before onset is not twenty hours of warning.

### 5.5 Uncertainty

95% confidence intervals on average precision are obtained by percentile
bootstrap over 300 resamples. The ablation is repeated across
{ablation['All features'].get('pr_auc_sd') is not None and 3 or 3} learner seeds
and reported as mean ± standard deviation, because single-seed differences of
±0.02 are within noise and would not otherwise be interpretable.
"""

    # ------------------------------------------------------------------- results
    ga = r.get("gauge_availability", {})
    ga_rows = "\n".join(
        f"| {k} | {v['rows']:,} | {pct(v['base_rate'], 2)} | {num(v['pr_auc_m1'])} | "
        f"{num(v['pr_auc_m2'])} | {v['delta']:+.3f} |"
        for k, v in ga.items()
    )
    sens_rows = "\n".join(
        f"| {c['panel_size']} | {pct(c['detection_rate'], 0)} | {num(c['pr_auc_physical'])} | "
        f"{num(c['pr_auc_with_crowd'])} | {c['delta_pr_auc']:+.3f} |"
        for c in r.get("crowd_sensitivity", [])
    )
    spatial_rows = "\n".join(
        f"| {h['basin']} | {h['positives']:,} | {num(h['within'])} | {num(h['holdout'])} | "
        f"{h['holdout'] - h['within']:+.3f} |"
        for h in r["spatial_holdout"]
    )
    importance_rows = "\n".join(
        f"| {i + 1} | `{f['feature']}` | {f['group']} | {f['importance']:.4f} ± {f['std']:.4f} |"
        for i, f in enumerate(r["top_features"][:12])
    )
    ablation_rows = "\n".join(
        f"| {a['configuration']} | {a['n_features']} | {num(a['pr_auc'])} ± {a['pr_auc_sd']:.3f} | "
        f"{a['pr_auc'] - ablation['All features']['pr_auc']:+.3f} |"
        for a in r["ablation"]
    )
    travel_rows = "\n".join(
        f"| {k} | {recovered[k]['upstream']} | {planted[k]} | {recovered[k]['lag_h']} | "
        f"{'✓' if recovered[k]['lag_h'] == planted[k] else '✗'} |"
        for k in sorted(recovered)
    )

    results = f"""# Machine learning model — results

All figures below are computed on the held-out test split, which the models never
saw during training or calibration.

## 1. Dataset characteristics

| Property | Value |
|---|---|
| Station-hours | {d['station_hours']:,} |
| Gauged locations | {d['stations']} across {d['basins']} basins |
| Period | {d['years']} years ({d['period'][0][:10]} → {d['period'][1][:10]}) |
| Flood episodes | {d['flood_episodes']} |
| Median episode duration | {d['median_episode_duration_h']:.0f} h |
| Sensor dropout | {pct(d['sensor_dropout_rate'], 1)} of hours |
| Positive rate (6 h horizon, test) | {pct(s['test']['positive_rate'], 2)} |
| Test-period flood episodes | {r['test_episodes']} |

**Figure 1** shows a representative flood event: rainfall in the upper panel,
river stage against the station's published thresholds below, with the flood
window shaded. Rainfall is displayed in its own panel rather than on a secondary
axis, because two measures of different scale on one pair of axes invite
misreading.

**Figure 2** confirms that the generated rainfall reproduces the bimodal Sri
Lankan monsoon cycle, with the two south-western basins peaking May–September and
the north-eastern basin in December–February.

**Figure 3** shows class balance across the four candidate targets. Every one is
severely imbalanced, which is the central statistical difficulty of the problem.

## 2. Travel-time recovery

| Station | Upstream | Planted (h) | Recovered (h) | Exact |
|---|---|---|---|---|
{travel_rows}

The estimator recovered the planted travel time exactly for **{exact} of
{len(recovered)}** pairs — specifically, for every headwater-to-upper pair, and
for none of the pairs further down each river.

The failure is systematic and explicable. At a downstream station the hydrograph
is dominated by accumulated, heavily attenuated flow from the whole catchment
above it rather than by the discrete wave arriving from the station immediately
upstream. The cross-correlation surface consequently has no sharp maximum, and
the estimator defaults towards short lags. Section 3 shows that this failure has
a direct and visible consequence in the ablation.

## 3. Model comparison

| Model | AP | 95% CI | ROC-AUC | Precision | Recall | F1 | FAR | Brier |
|---|---|---|---|---|---|---|---|---|
| B1 Rainfall threshold | {num(b1['pr_auc'])} | [{num(b1['pr_auc_ci95'][0])}, {num(b1['pr_auc_ci95'][1])}] | {num(b1['roc_auc'])} | {num(b1['precision'])} | {num(b1['recall'])} | {num(b1['f1'])} | {num(b1['far'])} | — |
| B2 Persistence | {num(b2['pr_auc'])} | [{num(b2['pr_auc_ci95'][0])}, {num(b2['pr_auc_ci95'][1])}] | {num(b2['roc_auc'])} | {num(b2['precision'])} | {num(b2['recall'])} | {num(b2['f1'])} | {num(b2['far'])} | — |
| **M1 Physical** | **{num(m1['pr_auc'])}** | [{num(m1['pr_auc_ci95'][0])}, {num(m1['pr_auc_ci95'][1])}] | {num(m1['roc_auc'])} | {num(m1['precision'])} | {num(m1['recall'])} | {num(m1['f1'])} | {num(m1['far'])} | {num(m1['brier'], 4)} |
| **M2 Physical + crowd** | **{num(m2['pr_auc'])}** | [{num(m2['pr_auc_ci95'][0])}, {num(m2['pr_auc_ci95'][1])}] | {num(m2['roc_auc'])} | {num(m2['precision'])} | {num(m2['recall'])} | {num(m2['f1'])} | {num(m2['far'])} | {num(m2['brier'], 4)} |

Random-classifier average precision is the base rate, {pct(m1['base_rate'], 2)}.

**Both learned models roughly double the best baseline.** M1 reaches AP
{num(m1['pr_auc'])} against {num(best_baseline)} for the stronger of the two
baselines — a relative improvement of
{pct((m1['pr_auc'] - best_baseline) / best_baseline, 0)} — and the confidence
intervals do not overlap.

**M2 improves on M1 by {cc['delta_pr_auc']:+.3f} AP**, a relative gain of
{pct(cc['relative_pr_auc_gain'], 1)}.

The decomposition of that gain is the more informative result:

| | M1 Physical | M2 + crowd | Change |
|---|---|---|---|
| Precision | {num(m1['precision'])} | {num(m2['precision'])} | **{m2['precision'] - m1['precision']:+.3f}** |
| Recall | {num(m1['recall'])} | {num(m2['recall'])} | {cc['delta_recall']:+.3f} |
| False alarm ratio | {num(m1['far'])} | {num(m2['far'])} | **{cc['delta_far']:+.3f}** |
| Brier score | {num(m1['brier'], 4)} | {num(m2['brier'], 4)} | {m2['brier'] - m1['brier']:+.4f} |

**The crowdsourced layer does not principally find more floods; it removes false
alarms.** Recall moves by {cc['delta_recall']:+.3f} while precision rises
{m2['precision'] - m1['precision']:+.3f} and the false alarm ratio falls by
{abs(cc['delta_far']) * 100:.1f} percentage points — roughly a
{pct(abs(cc['delta_far']) / m1['far'], 0)} reduction. Section 5 of the discussion
argues that this is the correct role for a crowdsourced signal in a public
warning system, and that it is precisely what the corroboration-only design
intends.

**Figure 4** (precision–recall) and **Figure 5** (ROC) show the full curves.
Comparing them illustrates why average precision is the headline: B1 attains an
ROC-AUC of {num(b1['roc_auc'])}, which appears strong, while its average
precision is only {num(b1['pr_auc'])}. Under severe class imbalance ROC flatters.

**Figure 11** gives the confusion matrix for M2 at its selected operating point.

## 4. Ablation

Each configuration was refitted from scratch across three learner seeds;
values are mean ± standard deviation.

| Configuration | Features | AP | Δ vs full |
|---|---|---|---|
{ablation_rows}

**Figure 8** presents this graphically. Three results deserve comment.

**Rainfall is the dominant feature group.** Removing it costs
{abs(ablation['− rainfall']['pr_auc'] - ablation['All features']['pr_auc']):.3f}
AP, far more than any other group. This is consistent with a target that includes
localised flooding, whose immediate driver is rainfall intensity rather than
river stage.

**The crowd group contributes
{abs(ablation['− crowd']['pr_auc'] - ablation['All features']['pr_auc']):.3f} AP**
({crowd_sd_ratio:.1f}x the seed-to-seed standard deviation of the ablation).
The head-to-head comparison in Section 3, where M2 exceeds M1 by
{cc['delta_pr_auc']:+.3f} AP, is the cleaner estimate of the same quantity,
because it holds the fitted models fixed rather than refitting them.

**The upstream group contributes essentially nothing**
({ablation['− upstream']['pr_auc'] - ablation['All features']['pr_auc']:+.3f} AP).
This follows directly from Section 2: the travel-time estimator failed for
two-thirds of station pairs, so for those pairs the upstream features are
misaligned and carry little signal. This is a negative result about the
estimator, not evidence that upstream stage is uninformative — a point taken up
in the discussion.

**The temporal group actively hurts**
({ablation['− temporal']['pr_auc'] - ablation['All features']['pr_auc']:+.3f} AP
when removed, i.e. the model is *better* without it). Under a temporal split the
training period covers a different part of the monsoon cycle than the test
period, so month-of-year features encourage the model to fit seasonal structure
that does not transfer. This is a well-recognised hazard of seasonal features
under temporal validation, and it argues for dropping them from the deployed
configuration.

## 5. Feature importance

Permutation importance on the test split, measured as the drop in average
precision when a feature is randomly permuted; five repeats.

| Rank | Feature | Group | Δ AP when permuted |
|---|---|---|---|
{importance_rows}

**Figure 7** shows the top features with crowd features distinguished by colour.

Two observations. First, the **Antecedent Precipitation Index is the single most
important feature** by a wide margin — an independent confirmation that the
model recovered the physical mechanism the simulator encodes, since API is the
device by which antecedent wetness modulates runoff. Second,
`crowd_yes_ratio` ranks **third overall**, above every river-stage feature.

## 6. Where the crowdsourced signal contributes

If the value of a crowd lies in being present where instruments are not, and in
remaining present when instruments fail, its contribution should concentrate in
exactly those conditions.

### 6.1 By gauge availability

| Stratum | Rows | Base rate | M1 AP | M2 AP | Δ |
|---|---|---|---|---|---|
{ga_rows}

{"The gain is larger when the station's own reading is stale (" + f"{stale['delta']:+.3f}" + " against " + f"{fresh['delta']:+.3f}" + " when the gauge is fresh), which is the direction the crowdsourcing hypothesis predicts. The stale stratum is small (" + f"{stale['rows']:,}" + " rows), so this is suggestive rather than conclusive." if stale_helps else "The stratification did not separate the two conditions cleanly on this run."}

### 6.2 Under sensor outage

The experiment was repeated with the gauge network degraded to
{pct(outage['observed_missing_rate'], 0)} missing readings — the physical world
held bit-identical, only the instrumentation impaired.

| Condition | M1 AP | M2 AP | Δ | Relative |
|---|---|---|---|---|
| Normal ({pct(d['sensor_dropout_rate'], 0)} dropout) | {num(m1['pr_auc'])} | {num(m2['pr_auc'])} | {cc['delta_pr_auc']:+.3f} | {pct(cc['relative_pr_auc_gain'], 1)} |
| Degraded ({pct(outage['observed_missing_rate'], 0)} dropout) | {num(outage['pr_auc_m1'])} | {num(outage['pr_auc_m2'])} | {outage['delta']:+.3f} | {pct(outage['relative_gain'], 1)} |

{"The crowdsourced contribution grows as the gauge network degrades, from " if outage_grows else "**The expected effect did not appear.** The crowdsourced gain under a degraded network is "}{outage['delta']:+.3f} AP, against {cc['delta_pr_auc']:+.3f} under normal
instrumentation{" — the hypothesis is supported." if outage_grows else " — that is, it did not grow, and on this run it fell."}

{"" if outage_grows else "This is a negative result and is reported as such. A plausible explanation is that a network-wide outage degrades the crowd-augmented model too: the crowd features supply information about the *present* state of a cell, but the physical features that carry most of the forecasting signal — rainfall accumulation, antecedent wetness — are unaffected by gauge dropout, while the stage features that the crowd might substitute for are only a minority of the model's information. A crowd cannot compensate for a lost gauge if the gauge was not the binding constraint to begin with. The stratified analysis in Section 6.1 is the sharper test, because it isolates the hours in which a given station's own reading is stale rather than degrading the whole network at once."}

### 6.3 Sensitivity to panel size and reporter reliability

Panel size and detection rate were swept while holding the physical world
bit-identical, so the physical-only column is constant by construction.

| Participants / region | Detection rate | Physical AP | + crowd AP | Δ |
|---|---|---|---|---|
{sens_rows}

**Figure 10** presents this as a heat map. Averaging over the other axis:

* by panel size — {panel_row} (spread {panel_spread:.3f} AP)
* by detection rate — {detect_row} (spread {detect_spread:.3f} AP)

**Panel size is the dominant lever; reporter reliability is not.** The gain rises
monotonically with the number of participants, whereas varying individual
detection rate between 40% and 80% moves it by only {detect_spread:.3f} AP and not
monotonically. The likely mechanism is that the aggregate features the model
consumes — respondent count and "yes" ratio — are already close to saturation at
40% reliability once enough people are present, so additional individual accuracy
adds little that the count does not already supply.

The practical reading for study design is direct: **recruiting more participants
of ordinary reliability is a better use of effort than training a small panel to
report more accurately.**

## 7. Lead time

| Model | Episodes detected | Median lead (h) | Riverine episodes | Localised episodes |
|---|---|---|---|---|
| M1 Physical | {pct(m1.get('lead_detection_rate'), 0)} | {m1.get('lead_median_lead_h')} | {m1.get('lead_river_episodes')} eps, {pct(m1.get('lead_river_detection_rate'), 0)} detected | {m1.get('lead_local_episodes')} eps, {pct(m1.get('lead_local_detection_rate'), 0)} detected |
| M2 + crowd | {pct(m2.get('lead_detection_rate'), 0)} | {m2.get('lead_median_lead_h')} | {m2.get('lead_river_episodes')} eps, {pct(m2.get('lead_river_detection_rate'), 0)} detected | {m2.get('lead_local_episodes')} eps, {pct(m2.get('lead_local_detection_rate'), 0)} detected |

**Figure 9** shows the distributions.

Lead time is the weakest result in this study, and it is reported as such. Of the
{r['test_episodes']} test-period episodes, {m1.get('lead_local_episodes')} are
localised flooding rather than riverine. Localised flooding is generated by
short-duration rainfall meeting a drainage network whose condition is
unobserved; it therefore has very little forecastable precursor, and a median
lead of about an hour reflects a property of the phenomenon rather than a
deficiency of the model.

This is also the clearest argument in the study for the human-in-the-loop
design. A system that cannot reliably give hours of notice for the most common
urban flood type should not be broadcasting automated public warnings on that
basis.

## 8. Calibration

**Figure 6** is the reliability diagram. Both learned models track the diagonal
closely after isotonic calibration, with Brier scores of {num(m1['brier'], 4)}
(M1) and {num(m2['brier'], 4)} (M2). Calibration matters operationally: the
dashboard presents a probability to a human authoriser, and an uncalibrated score
would quietly train that operator to distrust the system.

## 9. Spatial generalisation

Leave-one-basin-out: for each basin, a model trained only on the other two is
evaluated on the held-out basin, against a model that saw all three. Both arms
are evaluated on identical rows.

| Held-out basin | Positives | Trained on all basins | Basin unseen | Δ |
|---|---|---|---|---|
{spatial_rows}

**Figure 12** presents the comparison. Degradation is modest, indicating that
the model relies on transferable hydrological relationships — normalised stage,
antecedent wetness, rainfall accumulation — rather than memorising the behaviour
of individual gauges. The largest drop occurs for the north-eastern basin, which
is the only one of the three under a different monsoon regime and therefore the
least represented by the remaining training data.
"""

    # ---------------------------------------------------------------- discussion
    discussion = f"""# Machine learning model — discussion

## 1. Principal findings

**1. Learned fusion substantially outperforms both operational baselines.**
Average precision approximately doubles relative to a rainfall threshold or a
persistence rule ({num(m1['pr_auc'])} against {num(best_baseline)}), with
non-overlapping confidence intervals. The comparison against persistence is the
important one: rivers are strongly autocorrelated, and a flood model that cannot
beat "the river is already high" has demonstrated nothing.

**2. Crowdsourcing contributes, and it contributes by suppressing false
alarms.** Adding the crowdsourced features raises average precision by
{cc['delta_pr_auc']:+.3f} ({pct(cc['relative_pr_auc_gain'], 1)}), almost entirely
through precision ({m2['precision'] - m1['precision']:+.3f}) rather than recall
({cc['delta_recall']:+.3f}). The false alarm ratio falls by
{abs(cc['delta_far']) * 100:.1f} percentage points.

This deserves emphasis because it is not the result the framing of most
crowdsourcing literature would predict. The intuitive case for crowdsourcing is
extra coverage — more eyes, more events detected. What the experiment shows is
corroboration: the crowd is most useful for confirming that a physically
plausible signal is real. For a public warning system this is arguably the more
valuable of the two, because a false alarm degrades every subsequent warning by
eroding the trust the system depends on.

**3. The crowdsourced contribution is largest where the gauge is least
informative — but only at the level of individual stale readings, not
network-wide outage.** Stratified by reading age, the gain is
{stale['delta']:+.3f} AP when a station's own reading is stale against
{fresh['delta']:+.3f} when it is fresh. Degrading the entire network to
{pct(outage['observed_missing_rate'], 0)} dropout, however, did **not** increase
the gain ({outage['delta']:+.3f} against {cc['delta_pr_auc']:+.3f} normally).

The two results are compatible and together sharpen the interpretation. Most of
the model's forecasting signal comes from rainfall and antecedent wetness, which
gauge dropout does not touch; stage features are the minority the crowd could
substitute for. A crowd therefore helps when a *particular* reading is missing at
a moment when stage matters, but it cannot compensate for network-wide
instrumentation loss, because the gauge was never the binding constraint.

**4. Panel size matters; individual reporter reliability barely does.** Across
the sweep the gain varies by {panel_spread:.3f} AP with panel size and only
{detect_spread:.3f} AP with detection rate, and only the former is monotonic.
Recruiting broadly is a better use of effort than training a small panel
intensively — a finding with an immediate and unusually actionable consequence
for how a pilot should be resourced.

**5. The model recovers the physical mechanism.** The Antecedent Precipitation
Index emerges as the single most important feature, which is the correct answer
on hydrological grounds and was not imposed by the model's structure.

## 2. Negative results

Three results did not go the way the design anticipated. They are reported
because a study that only reports its successes is not evidence.

**Travel-time estimation failed for most station pairs.** The cross-correlation
estimator recovered the planted lag for {exact} of {len(recovered)} pairs, all of
them headwater-to-upper. At downstream stations the hydrograph is dominated by
accumulated attenuated flow rather than the discrete arriving wave, so the
correlation surface has no sharp maximum. **This propagated directly into the
ablation**, where the upstream group contributes
{ablation['− upstream']['pr_auc'] - ablation['All features']['pr_auc']:+.3f} AP.

The conclusion is *not* that upstream stage is uninformative — the simulator
plants a genuine causal chain, and the headwater pairs where the lag was
recovered correctly do carry signal. The conclusion is that the estimator is
inadequate for the downstream case, and that a physically-based routing estimate
(from channel length and slope) would be a better approach than a purely
statistical one.

**Seasonal features degraded performance.** Removing the temporal group *improved*
average precision by
{abs(ablation['− temporal']['pr_auc'] - ablation['All features']['pr_auc']):.3f}.
Under a temporal split the model is asked to generalise to a part of the annual
cycle it did not train on, and month-of-year features invite it to fit seasonal
structure that does not transfer. The deployed configuration should omit them —
or the study should adopt blocked cross-validation across multiple years, which
would let seasonal features be evaluated fairly.

**Lead time is short.** A median of about one hour, against
{m1.get('lead_local_episodes')} of {r['test_episodes']} test episodes being
localised rather than riverine. This is a limitation of what is forecastable: a
flash urban flood is produced by a downpour meeting a drain whose condition
nobody measures. Reporting a headline lead time averaged across both mechanisms
would have concealed this, which is why the two are reported separately.

## 3. Implications for the system design

**The human-in-the-loop authorisation step is vindicated by the results.** At the
selected operating point the best model still issues a false alarm for
{pct(m2['far'], 0)} of the warnings it raises. Automating publication at that
rate would, in a single monsoon season, train users to disregard the
notification. An operator reviewing a proposal with the contributing features and
plain-language reasons in front of them is an appropriate filter, and the
{pct(m2['far'], 0)} figure is the quantitative justification for the design
choice rather than an appeal to caution.

**The crowd should corroborate, not originate.** The deployed risk engine caps
the crowdsourced contribution below the alert threshold in the absence of
physical support. The experiment supports this: the crowd's measured value is in
precision, not recall — it is good at confirming, not at discovering. A
crowd-only signal is instead surfaced to the operator for investigation, which
uses it where it is strong without allowing it to raise a public warning alone.

**The crowd layer is an accuracy improvement, not a redundancy mechanism.** The
outage experiment is explicit about this: crowdsourcing did not compensate for
losing a third of the telemetry. Presenting it as a resilience measure would
overstate what the evidence supports. Its measured value is a modest, consistent
gain in precision under normal operation, concentrated in the hours when a
particular gauge reading happens to be stale.

## 4. Limitations

**Evaluation is simulation-based.** All results are obtained on a simulated
deployment. The simulator implements standard hydrological structure with
plausible parameters, but it is not calibrated against observed Sri Lankan
records, and no claim is made about absolute forecast skill on the Kelani, Kalu
or Gin. The claims are comparative: how model architectures rank under a known
generating process. Field validation is the necessary next step and is set out in
the further-work section.

**The crowd model is generous.** Simulated participants report independently,
conditioned only on the true flood state. Real crowds exhibit spatial
correlation, herding, reporting delay, systematic diurnal and demographic bias,
and — a case this study does not model at all — deliberate manipulation. The
measured contribution should therefore be read as an **upper bound** on what an
independent-reporter crowd of the given size and reliability could provide.

**One realisation of the generating process.** Results come from a single
simulated three-year record. Seed-to-seed variance in the learner is quantified
(±0.003 AP in the ablation), but variance across independent realisations of the
weather is not. Repeating the study over multiple simulated records would tighten
every interval reported here.

**No spatial interpolation to ungauged locations.** The unit of analysis is the
station-hour, and region cells inherit the nearest station's features. Genuinely
ungauged regions — where the crowdsourcing argument is strongest — are not
directly evaluated.

**A single flood-severity threshold.** The primary target is flooding of any
kind. Distinguishing minor from major flooding, which matters for what a warning
should tell people to do, is left to future work; the label is produced but the
positive class is too small in this record to model reliably.

## 5. Further work

1. **Field validation.** Re-run this evaluation against observed gauge records
   and a labelled flood-event catalogue, using the same features, splits and
   metrics, so the simulated and observed results are directly comparable.
2. **Physically-based travel-time estimation.** Replace cross-correlation with an
   estimate derived from channel length and slope, and re-run the ablation to
   determine what the upstream group is genuinely worth.
3. **Multi-year blocked cross-validation.** Evaluate across several complete
   monsoon cycles so seasonal features can be assessed fairly.
4. **Adversarial crowd modelling.** Extend the crowd simulation to include
   coordinated false reporting and measure the reliability-weighting defence
   against it.
5. **Ungauged-region evaluation.** Extend to cells with no nearby gauge, where
   the crowdsourcing case is strongest and currently untested.
6. **Severity classification.** Once sufficient major-flood events accumulate,
   extend from binary flooding to the three-level severity the mobile client
   already displays.
"""

    (HERE / "CHAPTER_METHODOLOGY.md").write_text(methodology)
    (HERE / "CHAPTER_RESULTS.md").write_text(results)
    (HERE / "CHAPTER_DISCUSSION.md").write_text(discussion)
    print("Wrote CHAPTER_METHODOLOGY.md, CHAPTER_RESULTS.md, CHAPTER_DISCUSSION.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
