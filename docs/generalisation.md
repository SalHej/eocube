# Generalisation to unseen stations

An extension of the 2019 Twente study. **The 1 × 1 point-mode ablation in
docs/open_ablation.md remains the main result.** The design and the data licences were
fixed in PLAN.md ("Generalisation to unseen stations") before any result.

- **Jobs:**
  - 247810: auxiliary data and Arm A; 4 min 16 s, 0.28 CPU-hours;
  - 247812: Arm B; 3 min 44 s, 0.25 CPU-hours.
- **Script:** `experiments/open_cube/generalisation.py`.
- **Results:** `~/work/results/open_cube/generalisation_2019/arm_a.json` and `arm_b.json`;
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

**Plainly: neither arm fixes generalisation. Both fail for reasons that can be named,**
and they point to one specific next step ("What would address it").

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

## What would address it

**Not run here; each is a decision.**

1. **Normalise each station's radar by its own history** before modelling: VV minus the
   station's own mean, or scaled by its own min and max over the year (Batchu et al.;
   Δσ/Δσmax in the change-detection method). This removes the between-station term with
   the wrong sign and keeps the within-station signal. **It uses only the unseen
   station's radar, never its soil moisture,** so it does not leak. The cheapest and
   most targeted next test: no new data, about 0.3 CPU-hours.
2. **Accept that the level cannot be placed from these inputs,** and evaluate anomaly
   tracking separately: train and score on station-demeaned targets. That is a
   different, honest question.
3. **A regional soil-moisture reference** (SMAP, about 9–36 km) for the shared anomaly.
   Open data, but a new download with its own licence check.
4. **Better static data** at field scale: a groundwater-depth or soil map at 1:50,000
   (the Dutch BRO), not a global 250 m product. This is a licence question first.

## Limits

- **17 stations** for every between-station statement.
- **Permutation importance on held-out stations** measures what helps place unseen
  stations, which is mostly level. It understates a feature that is useful only within a
  station.
- **SoilGrids values are 250 m means** read at one point; NASADEM is ~30 m.
- **Arm B's pairs share samples** (each pass is in two pairs), so fold metrics are not
  independent of one another.
