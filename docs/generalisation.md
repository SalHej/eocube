# Generalisation to unseen stations

An extension of the 2019 Twente study. **The 1 × 1 point-mode ablation in
docs/open_ablation.md remains the main result.** The design and the data licences were
fixed in PLAN.md ("Generalisation to unseen stations") before any result.

- **Jobs:**
  - 247810: auxiliary data and Arm A; 4 min 16 s, 0.28 CPU-hours;
  - 247812: Arm B; 3 min 44 s, 0.25 CPU-hours;
  - 247845: Arm C, the radar normalised by its own history; 5 min 27 s, 0.36 CPU-hours.
- **Script:** `experiments/open_cube/generalisation.py`.
- **Results:** `~/work/results/open_cube/generalisation_2019/arm_a.json`, `arm_b.json` and
  `arm_c.json`;
  station values in `~/work/data/cubes/open_cube/2019/station_aux.json`.
- **Setup, as in the main study:**
  - the same 3,505 samples;
  - stratified station folds, 5 partitions × 4 folds;
  - a random forest with 300 trees.

> **Detection limit: ΔR² ≈ 0.05.** Anything below it is "below detection limit on this
> task". An arm that improves absolute R² but not anomaly R² is better fit without
> moisture tracking, not success.

## Result

1. **Arm A is a clean negative, and it is a finding about the auxiliary data.**
   SoilGrids does not separate these stations:
   - **What it sees:** clay 7.2–14.3% and sand 63–80% at all 17. Rank correlations with
     station mean soil moisture lie between −0.28 and +0.26.
   - **What is actually there:** station means range from 0.107 to 0.551 m³/m³.
   - **Why:** at 250 m, SoilGrids cannot resolve what makes one Twente field wet and the
     next dry.

   Adding it made absolute R² worse (median ΔR² −0.191, 7 of 20 folds better). Its
   apparent anomaly gain is mostly an artefact of predicting more constant values.
2. **Arm B is a negative too, judged against the right reference.**
   - **Radar change detection does not beat "no change":** R² on the change in soil
     moisture is −0.044, against −0.001 for predicting none.
   - **Its large gain over the baseline** (median +1.87) only measures how badly the
     baseline's implied change does (R² −1.93).
   - **The change signal at the pixel is real but weak:** per-station r between the
     change in VV and the change in soil moisture has a median of 0.15, positive at 16 of
     17 stations.
3. **The radar contributes nothing to the baseline model.** Held-out permutation
   importance:

   | Feature | Importance |
   |---|---|
   | B11 | +0.233 |
   | B02 | +0.102 |
   | B04 | +0.073 |
   | VV | +0.001 |
   | VH | −0.000 |

   Yet within one orbit, per-station r(VV, soil moisture) has a median of 0.31 at the station pixel (0.40 with a 9 × 9 window). **The
   signal exists, and the forest does not use it.** The mechanism, measured below:
   - **Within a station,** VV rises with moisture (median r 0.31 within one orbit).
   - **Across stations, the sign is reversed:** r(station mean VV, station mean soil
     moisture) = −0.34.
   - **Held-out-station R² mostly rewards the between-station level,** which is 57.5% of
     the target's variance in this cube.

   This explains the window result as well. Averaging out speckle sharpens a
   within-station signal that the metric barely weighs and the model does not use, so R²
   did not move.

4. **Arm C, the last arm: normalising the radar by its own history did not change
   that.** VV and VH were z-scored (dB) or percentile-ranked per station and orbit, from
   2019 radar only. That removes the between-station term exactly: the between-station
   share is 0.0000. The within-station signal stays: r 0.313 (z) and 0.318 (rank).
   - **With the optical bands present, VV is still unused:** importance +0.001 (z) and
     +0.004 (rank). ΔR² is +0.001 and −0.004 at the median.
   - **Alone, VV is used** (importance +0.067 and +0.065), **and the model is worse.**
     Absolute R² falls to −0.19, below predicting the training mean. Anomaly R² falls to
     −3.05 and −2.74.
   - **Normalising the optical as well does not remove its importance** (B11 +0.108 to
     +0.298). R² stays where the baseline was (−0.052 and −0.008 against −0.058).

**Plainly: this network cannot support spatial generalisation of soil moisture with these
inputs, and the study stops here.**
- **The within-station radar signal is real:** r ≈ 0.31, and it survives normalisation
  intact.
- **It is too weak to matter on this target.** Used alone it makes predictions at unseen
  stations worse; beside the optical bands it is ignored.
- **No arm placed a station's level, and none tracked its anomalies.** Anomaly R² was below
  zero in every arm that did not predict a constant.

No further arm is proposed.

## What the literature does that we do not

Read before the experiment was written.

| Repository | Licence | What it does that we do not |
|---|---|---|
| [google-research/soil_moisture_retrieval](https://github.com/google-research/google-research/tree/master/soil_moisture_retrieval); Batchu, Nearing & Gulshan (2023), J. Hydrometeorology 24(10), doi:10.1175/JHM-D-22-0118.1; [arXiv:2206.09649](https://arxiv.org/abs/2206.09649) | Code: **Apache-2.0** (the google-research repository). Dataset: **CC-BY 4.0** | ~1,300 in-situ sensors, split **by sensor** 60:20:20, which is our held-out-station setting. Inputs: S1 with incidence angle and **each pixel's own max, mean and min over the previous year** for S1 and S2; SoilGrids 0–5 cm; NASA DEM; **SMAP and GLDAS coarse soil moisture**. Deep learning model |
| [aanwarigeo/sentinel-1-soil-moisture](https://github.com/aanwarigeo/sentinel-1-soil-moisture) | **GPL-3.0** | Change detection **calibrated per pixel**: a dry reference and the maximum backscatter change Δσmax from each pixel's own time series, regressed on a vegetation index (DpRVIc). Relative soil moisture is Δσ/Δσmax, scaled to volume with **field capacity and wilting point measured on the ground**. Reports r 0.75 and RMSE 0.095 against in-situ data; the validation design is not stated in the README |
| [soilsensingmonitoring/mlhrsm_1.0](https://github.com/soilsensingmonitoring/mlhrsm_1.0); Peng et al. (2024), Agronomy 14(3), 421 | **Conflicting:** the GitHub sidebar says MIT; the package's DESCRIPTION file says GPL-3 | An R package producing 30–500 m, daily-to-monthly maps with uncertainty for selected US sites (0–5 cm and 0–1 m), built on Google Earth Engine; random forest and quantile regression forest per its dependencies. **The paper returned 403 and the README does not describe the covariates or the validation split**, so the inputs and validation could not be checked |

**No code was copied from any of them.**

**What Batchu et al. show:**

| Inputs (sensor-level split, test set) | Correlation |
|---|---|
| S1 + DEM | 0.437 |
| + S2 | 0.56 |
| + SoilGrids | 0.647 |
| + SMAP + GLDAS | 0.729 |

In their ablation, removing SoilGrids cost the most (correlation −9.5%). These are
pooled correlations over ~1,300 sensors on several continents, not R² over 17 stations
in one region. **SoilGrids works there because soil texture varies a great deal across
their sensors; across ours it barely varies (Arm A).**

**Which of their choices matters most for our result:**
1. **Per-location normalisation of the radar by its own history.** Batchu et al.'s
   last-year min, mean and max, and the Δσmax of the change-detection method, both
   remove each location's own backscatter level. That is exactly the term with the wrong
   sign across our stations. **It needs no soil-moisture labels at the unseen station,
   only its radar history,** so it does not leak. This is the step most directly aimed
   at the mechanism found here.
2. **A dynamic regional soil-moisture reference** (SMAP, GLDAS). Our stations' anomalies
   are correlated (median r 0.60; PLAN.md), so a coarse product would carry the shared
   wet-dry signal that the anomaly metric scores. Our power proxy used neighbouring
   stations' readings for the same reason. It would mostly help anomaly R², not place a
   station's level.
3. **More stations.** 1,300 against 17 is the difference between learning a mapping from
   soil to level and memorising 13 training stations.

## Arm A: static auxiliary features

**The station values,** read at each station point (job 247810):
- SoilGrids 2.0, 0–5 cm mean (ISRIC, CC-BY 4.0);
- NASADEM elevation, and slope by Horn's method over 3 × 3 pixels (US government work).

| Station | Clay % | Sand % | Silt % | Bulk density kg/dm³ | SOC g/kg | Elevation m | Slope ° |
|---|---|---|---|---|---|---|---|
| ITCSM_01 | 10.8 | 65.6 | 23.6 | 0.98 | 98.2 | 24 | 1.6 |
| ITCSM_04 | 14.3 | 63.1 | 22.7 | 0.97 | 98.7 | 50 | 2.0 |
| ITCSM_10 | 7.9 | 79.8 | 12.3 | 1.12 | 85.1 | 14 | 2.0 |
| ITCSM_12 | 7.2 | 78.0 | 14.8 | 1.11 | 56.5 | 16 | 2.9 |
| ITCSM_16 | 7.3 | 79.9 | 12.7 | 1.05 | 68.2 | 8 | 1.2 |
| … (all 17 in `station_aux.json`) | | | | | | | |

**Range over all 17 stations:**
- clay 7.2–14.3%;
- sand 63.1–79.9%;
- organic carbon 56.5–107.1 g/kg;
- elevation 8–50 m.

**Station mean soil moisture ranges from 0.107 to 0.551 m³/m³** (sd 0.115). Spearman
rank correlation with station mean soil moisture, over 17 stations:

| Clay | Sand | Silt | Bulk density | SOC | Elevation |
|---|---|---|---|---|---|
| +0.18 | −0.10 | −0.03 | −0.14 | +0.26 | −0.28 |

**Results** (20 folds; R² median, with the mean in brackets):

| Arm | R² absolute | Anomaly R² | ΔR² absolute vs baseline | ΔR² anomaly vs baseline |
|---|---|---|---|---|
| Baseline (8 pixel features) | −0.058 (−0.359) | −1.694 | — | — |
| Static only (7) | −0.521 (−0.954) | **0.000** | −0.595 ± 0.995, med −0.506, 3/20 | +3.014 ± 3.109, med +1.694, 20/20 |
| Baseline + static (15) | −0.079 (−0.706) | −0.846 | **−0.347 ± 0.879, med −0.191, 7/20** | +1.305 ± 1.874, med +0.701, 16/20 |

- **Adding the static features made absolute R² worse** in 13 of 20 folds.
- **The anomaly "gains" are not moisture tracking.**
  - **Static only:** the features are constant within a station, so the predictions are
    constant within each station-month. Their anomaly is zero, and anomaly R² is exactly
    0 by construction.
  - **Baseline + static:** the static features take over part of the prediction, which
    then varies less within a station. Anomaly R² rises toward 0 (−0.846) but stays
    below it, which predicting no anomaly would beat. This is the same averaging toward a
    constant seen with calculate-then-average in docs/window_size.md.

**Feature importance** (mean drop in held-out R² when a feature is shuffled; 20 folds):

| Arm | Top features |
|---|---|
| Baseline | B11 +0.233, B02 +0.102, B04 +0.073, VV +0.001, VH −0.000, B03 −0.000, B08 −0.005, B12 −0.067 |
| Static only | clay +0.080, elevation +0.046, slope +0.002; bulk density, silt, sand and SOC negative |
| Baseline + static | B11 +0.186, B02 +0.085, **clay +0.079, elevation +0.065**, B08 +0.047, B03 +0.024 |

**Did SoilGrids change what the model relies on?** Partly:
- **Clay and elevation enter at third and fourth.**
- **B11 and B02 still lead,** and the radar remains unused.
- **The new features carry no useful between-station information here.** Clay and
  elevation are used, but they do not rank stations' wetness. So the forest splits on
  them and loses accuracy at stations it has not seen.

**This is a negative about SoilGrids for Twente, not about static information.** The
static facts that would place a field are what SoilGrids cannot see at 250 m:
groundwater depth, drainage, peat lenses and management.

## Arm B: change detection

**Target:** the change in 5 cm soil moisture between consecutive S1 passes at a
station.
- **Primary:** within one relative orbit. 3,437 pairs, median gap 6 days.
- **Secondary:** any orbit. 3,488 pairs, median gap 1.5 days.

**Features:**
- the change in VV and VH in dB (the log of the linear ratio);
- the time between the passes;
- in one variant, the S2 bands at the later pass.

**Compared on the same pairs and folds with:**
- the baseline absolute model's implied change, p(t2) − p(t1);
- predicting no change.

**Results:** R² on the change in soil moisture (median over 20 folds), and the same with
station-month means removed.

| Pairs | Arm | R² (ΔSM) | Anomaly R² (ΔSM) |
|---|---|---|---|
| Same orbit | Baseline's implied change | −1.929 | −2.196 |
| | **No change (predict 0)** | **−0.001** | 0.000 |
| | Change detection, radar only | −0.044 | −0.068 |
| | Change detection, radar + S2 | −0.015 | −0.012 |
| Any orbit | Baseline's implied change | −2.541 | −2.605 |
| | **No change (predict 0)** | **−0.000** | 0.000 |
| | Change detection, radar only | −0.057 | −0.055 |
| | Change detection, radar + S2 | −0.031 | −0.018 |

- **Against the baseline:** ΔR² is +1.73 to +2.38 at the median, positive in 20 of 20
  folds. **That says the baseline's implied change is worse than useless, not that
  change detection works.**
- **Against no change, change detection does not win.** Every arm scores at or below 0.
- **It is still a different target, not a rescue of the absolute one.**

**The signal is there, weakly:** per-station r(ΔVV, ΔSM) has a median of +0.151 (16 of 17
positive) within one orbit, and +0.090 across orbits.

**Importance:** in the radar-only arm, the time between passes leads (+0.104). ΔVH
(+0.019) and ΔVV (+0.016) carry little. Adding S2 puts B03, B12, B02 and B04 alongside
them. A change of r ≈ 0.15 explains about 2% of the variance in the change, and the
forest does not extract even that at unseen stations.

**What this arm did not do.** The change-detection literature calibrates each location
to its own dynamic range: Δσ/Δσmax, with a dry reference. This arm used raw consecutive
differences, so a 1 dB change means the same at every station. That difference is the
untested part (next section).

## Why the radar is unused

Variance of the cube's 3,505 samples, split between and within stations:

| Variable | Between-station share | Note |
|---|---|---|
| Soil moisture | **57.5%** | the part that held-out-station R² mostly scores |
| VV (dB) | 15.1% (22.8% between station-orbit groups) | within-station sd 2.38 dB; sd of station means 0.98 dB |
| B11 | 8.4% | |
| B02 | 7.0% | |

The share for soil moisture is 57.5% here, against 37.3% in PLAN.md's decomposition.
PLAN.md used 2017–2020 readings at all S1-like times, and split out the seasonal part
separately.

| Relationship | r |
|---|---|
| VV and soil moisture, within one station and orbit (median per station) | **+0.31** (1 × 1); +0.40 at 9 × 9 |
| Station mean VV and station mean soil moisture, across the 17 stations | **−0.34** |
| Station mean B11 and station mean soil moisture | −0.47 |

**The mechanism:**
- **Most of the target's variance lies between stations** (57.5%). Held-out-station R² is
  dominated by whether the model places each station's level.
- **VV relates to soil moisture with opposite signs** within and between stations.
  Wetter fields have lower mean VV, probably through vegetation, roughness and land
  cover, while a given field's VV rises as it wets. No single rule the forest learns can
  use both.
- **B11 (SWIR) relates to between-station wetness with one consistent sign,** so the
  forest uses it.

**This is not quite "VV's between-station variation swamps its within-station
variation":** most of VV's own variance is within stations (85%). **The swamping is in the
target and in the sign:**
- the within-station signal VV carries is worth little to a metric that the
  between-station level dominates;
- and across stations, VV points the wrong way.

**It explains the window result.** Averaging out speckle raised within-orbit r from 0.31
to 0.40, a within-station improvement. R² did not move, because the model did not use VV
and the metric rewards the between-station level (docs/window_size.md).

## Arm C: radar normalised by its own history (the last arm)

Design fixed in PLAN.md ("Radar normalised by its own history") before the run.

**What was done:**
- **VV and VH in dB** were normalised per station and relative orbit, using only that
  station-orbit's 2019 radar values in the cube, never soil moisture. Two variants:
  - z-score: (x − mean) / sd;
  - percentile rank, 0 to 1.
- **For arm 4,** each optical band was normalised per station by the same method.
- **This uses no labels from the held-out station, but it is transductive:** a station's
  statistics include its own test passes, as a deployed retrieval with a radar history
  would.

**The construction check:**

| | VV | VH | B11 | Station means: r(VV, SM) | Within station-orbit: median r(VV, SM) |
|---|---|---|---|---|---|
| Raw, between-station share | 0.151 | 0.052 | 0.084 | −0.340 | 0.313 |
| z-score, between-station share | **0.0000** | **0.0000** | **0.0000** | (−0.26; the means are all ~0) | **0.313** |
| Percentile rank, between-station share | **0.0000** | **0.0000** | **0.0000** | (+0.12; the means are all ~0) | **0.318** |

- **The between-station term is gone,** and the within-station signal is unchanged.
- **The correlation of station means after normalisation is computed over values that are
  all close to zero.** It only reflects how each station's passes are split between
  orbits, and carries no information.

**Results** (20 folds; R² median, with the mean in brackets; ΔR² paired against the
baseline):

| Arm | R² absolute | Anomaly R² | ΔR² absolute | ΔR² anomaly | Importance (top, and VV) |
|---|---|---|---|---|---|
| 1 Baseline, 8 raw | −0.058 (−0.359) | −1.694 | — | — | B11 +0.233, B02 +0.102, B04 +0.073; **VV +0.001** |
| 2 z radar + raw optical | −0.053 (−0.359) | −1.722 | −0.000 ± 0.012, med +0.001, 10/20 | −0.080 ± 0.325, med −0.016 | B11 +0.227, B02 +0.096; **VV_z +0.001** |
| 3 z radar only | −0.191 (−0.576) | −3.050 | −0.217 ± 0.243, med −0.186, 3/20 | −0.857 ± 2.703, med −0.350 | **VV_z +0.067**, VH_z +0.007 |
| 4 z radar + z optical | −0.052 (−0.405) | −2.260 | −0.046 ± 0.180, med −0.034, 8/20 | +0.410 ± 1.273, med +0.250 | B08_z +0.116, B11_z +0.108; **VV_z +0.007** |
| 2 rank radar + raw optical | −0.057 (−0.363) | −1.768 | −0.004 ± 0.014, med −0.004, 9/20 | −0.112 ± 0.407, med −0.023 | B11 +0.220, B02 +0.091; **VV_rank +0.004** |
| 3 rank radar only | −0.211 (−0.583) | −2.744 | −0.225 ± 0.193, med −0.181, 2/20 | −0.993 ± 2.447, med −0.722 | **VV_rank +0.065**, VH_rank −0.024 |
| 4 rank radar + rank optical | −0.008 (−0.417) | −2.739 | −0.059 ± 0.435, med +0.046, 13/20 | −0.082 ± 1.375, med −0.183 | B11_rank +0.298, B02_rank +0.296; **VV_rank +0.010** |

### Does VV finally get used?

**Only when it is the only feature, and then it makes the model worse.**
- **Arms 2 and 4:** with any optical band beside it, normalised VV's importance stays at
  +0.001 to +0.010, the same as raw VV.
- **Arm 3:** alone, it reaches +0.065 to +0.067. Shuffling it costs that much held-out R²,
  so the forest does use it.
- **But arm 3's absolute R² (−0.19, −0.21)** is below the training-mean predictor (median
  about −0.02 to −0.03, docs/open_ablation.md), and well below the baseline. Its anomaly R²
  (−3.05, −2.74) is worse than the baseline's.

A within-station correlation of 0.31 explains about 10% of a station's variance at best.
Fitted by a forest across 13 stations, it adds more prediction variance than signal.

**This is the case the interpretation rules name in advance: VV gets used, and R² does
not improve.** The within-station radar signal is real, survives normalisation intact,
and is too weak to matter on this target.

### Was B11 used for the right reason?

**Partly.** Arm 4 removes every between-station term from the optical as well, yet the
normalised optical bands stay the most important features (B11 +0.108 to +0.298). They
carry within-station information, most likely the seasonal cycle shared by vegetation and
soil moisture (32% of the target's variance is seasonal; PLAN.md).

Arm 4's absolute R² equals the baseline's (−0.052 and −0.008 against −0.058). Two readings
follow:
- **B11's consistent between-station sign bought nothing measurable;** and
- **the baseline was never placing station levels:** it does no better than a model that
  has no level information at all.

Arm 4's anomaly R² stays far below zero (−2.26, −2.74), so the seasonal information does
not become moisture tracking either.

## Conclusion: the study stops here

**This network cannot support spatial generalisation of soil moisture from these inputs.**

| Test | Result |
|---|---|
| Static soil and terrain (Arm A) | SoilGrids does not distinguish the stations; absolute R² worse |
| Change detection (Arm B) | Does not beat predicting no change |
| Radar normalised by its own history (Arm C) | VV used only alone, and then worse; never used beside the optical |
| Every arm | No anomaly R² above zero, except arms that predict a constant within a station-month (exactly 0 by construction) |

**What this means:**
- **The within-station signal exists:** r(VV, SM) ≈ 0.31 per station, 0.40 with a 9 × 9
  window, unchanged by normalisation.
- **With 17 stations and these features, it cannot be turned into a prediction at a
  station the model has not seen.**
- **This is a finding about the network and the inputs, not a step to be engineered
  around.** Per the rules fixed before this arm, no further arm is proposed.

**For the contract study, it confirms what docs/open_ablation.md already reported:** the
downstream task on which the gates were ablated has no skill at unseen stations, and that
does not change with the feature set. Whether contracts improve accuracy has to be tested
on a task where the model works.

## Limits

- **17 stations** for every between-station statement.
- **Permutation importance on held-out stations** measures what helps place unseen
  stations, which is mostly level. It understates a feature that is useful only within a
  station.
- **SoilGrids values are 250 m means** read at one point; NASADEM is ~30 m.
- **Arm B's pairs share samples** (each pass is in two pairs), so fold metrics are not
  independent of one another.
