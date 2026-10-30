# The 2019 open-cube ablation: leave one gate out

Twente soil moisture, 2019: 3,505 samples at 17 stations, the cube documented in
docs/cube_2019.md (job 247673). Ablation job **247696**. Results:
`~/work/results/open_cube/ablation_2019/ablation.json` on the cluster.

> **Detection limit: ΔR² ≈ 0.05** (PLAN.md, "Power check"). Any gate whose effect falls
> below it is reported as **"below detection limit on this task"**, never as "no effect".
>
> **This ablation measures downstream accuracy on one task. It does not measure
> validity.** A gate can be protecting correctness without moving R² on a smooth
> target: a misregistered layer, a leaked future scene or a cloud-filled pixel are
> wrong whether or not a model's score notices.

## Result

**No gate had a detectable effect on absolute R².** The quality gate is the only one
whose removal changed anything. **Removing it changed paired ΔR² by +0.033 ± 0.052:
below detection limit on this task.**

**On anomaly R², removing the quality gate exceeded the limit: +0.193 ± 0.223.** That
is in the direction of the gate costing accuracy: 16 of 20 folds improved when its 110
refused samples were trained on. It is the over-rigid chip-level gate refusing samples
whose station pixel was valid (docs/cube_2019.md), now measured as a cost. One
qualification belongs with it, not instead of it: **both models score far below
predicting no anomaly at all.** The gain is a reduction in the error of a model that
does not track moisture (see "Baseline skill").

**Geometry, resolution, temporal alignment and sensor physics refused no sample in this
cube.** Their arms are the baseline, sample for sample, so each is below detection
limit on this task in the plainest sense: **there was nothing to detect.** That holds
for the temporal gate too: it keeps and flags the 1,415 samples with S2 older than
7 days rather than refusing them.

## Setup

| Item | Choice |
|---|---|
| Cube | 2019, job 247673. One row per station and S1 pass. S1 VV/VH and S2 B02–B12 at the station pixel; 5 cm in-situ soil moisture |
| Gates | all five, run per sample on the real layers when the cube was built. **Geometry in point mode** (PLAN.md, "Geometry mode") |
| Bounds | **as declared**: S1 ≥ 1e-5; B02 in [50, 12000]; other S2 bands in [0, 12000] (PLAN.md, "Quality bounds") |
| Arms | baseline: a sample is kept unless any gate refuses it. "Without *g*": *g*'s refusals are ignored, every other gate applies. Supplementary: geometry in grid mode, the design not selected |
| Folds | **stratified station blocks** (`experiments/open_cube/blocks.py`): k-means on the 13 western stations into 4 blocks, one of ITCSM_01/04/07/09 added to each. Not `eocube.pipeline.spatial_block_split`, which splits a pixel grid |
| Partitions | **5 block partitions** (seeds 0–4). The seed changes which stations fall in which fold; it also sets the forest's random state |
| Model | random forest, 300 trees (`eocube.baselines.make_random_forest`), same for every arm. 8 features: S1 VV, VH (linear), S2 B02–B12 (DN) |
| Primary result | **paired ΔR² per fold**: the arm's model minus the baseline's, on the test samples both accept |
| Metrics | **absolute R²**, and **anomaly R²**: target and prediction each minus their own station-month mean over the scored samples |
| Cost | 2 min 12 s on 4 CPUs, ≈ 0.15 CPU-h |

**How an arm is built.** The gates already ran on every sample's layers (job 247673),
so an arm is a filter on those recorded verdicts. Nothing is re-read. This is
`eocube.ablation.run_ablation`'s `skip`, applied per sample, with its conventions:
**ΔR² = arm − baseline. Negative means the gate was doing useful work; positive means
the model did better without it.**

**Why the anomaly prediction is demeaned too.** The power proxy predicted anomalies
directly. This model predicts soil moisture, so its anomaly is its own prediction
minus its own station-month mean. Subtracting the *target's* climatology from the
prediction would leave the residual unchanged and only shrink the denominator. A first
run (job 247692) did that and reported anomaly R² ≈ −22. Absolute R², ΔR² and the
refusals were identical between the two runs.

## Summary table

20 folds per arm (5 partitions × 4 folds). R² on each arm's own accepted test samples,
mean ± sd across folds. ΔR² paired, mean ± sd. **Decisions**: gate verdicts of
PASS_WITH_DECISION on the kept samples, one per gate per sample (a removed gate records
none).

| Arm | Kept | Refusal rate | Decisions (per sample) | R² absolute | **ΔR² absolute** | R² anomaly | **ΔR² anomaly** | Reading |
|---|---|---|---|---|---|---|---|---|
| **All gates active** | 3,395 | 3.1% | 8,566 (2.52) | −0.404 ± 1.479 | — | −3.196 ± 3.350 | — | baseline |
| without geometry_crs | 3,395 | 3.1% | 5,171 (1.52) | −0.404 ± 1.479 | 0.000 | −3.196 ± 3.350 | 0.000 | below detection limit on this task: refused nothing |
| without resolution | 3,395 | 3.1% | 5,171 (1.52) | −0.404 ± 1.479 | 0.000 | −3.196 ± 3.350 | 0.000 | below detection limit on this task: refused nothing |
| without temporal_alignment | 3,395 | 3.1% | 7,182 (2.12) | −0.404 ± 1.479 | 0.000 | −3.196 ± 3.350 | 0.000 | below detection limit on this task: refused nothing |
| **without quality_missingness** | 3,505 | 0.0% | 8,425 (2.40) | −0.359 ± 1.370 | **+0.033 ± 0.052** | −3.014 ± 3.109 | **+0.193 ± 0.223** | absolute: below detection limit on this task. Anomaly: above it, against the gate |
| without sensor_physics | 3,395 | 3.1% | 8,566 (2.52) | −0.404 ± 1.479 | 0.000 | −3.196 ± 3.350 | 0.000 | below detection limit on this task: refused nothing |
| *geometry in grid mode* | 2,475 | 29.4% | 3,743 (1.51) | −0.378 ± 1.459 | +0.037 ± 0.069 | −2.887 ± 2.983 | +0.339 ± 0.608 | absolute: below detection limit on this task. Anomaly: above it, against grid mode |

**What the decisions are**, on the baseline's 3,395 samples:
- point-mode geometry: 3,395 (every sample: CRS differ, point agreement recorded);
- resolution: 3,395 (coarsest-GSD default; describes a grid cube; PLAN.md, open
  question 5);
- temporal alignment: 1,384 (S2 outside the 7-day window, kept and flagged);
- quality: 392 (range excursions within tolerance, zero-fill and retained-invalid
  notes);
- sensor physics: none.

Removing a gate removes its decisions; only the quality gate also changes the samples.

## Per-fold deltas

Paired ΔR² per fold, absolute / anomaly.

**Without quality_missingness**

| Partition | Fold 0 | Fold 1 | Fold 2 | Fold 3 |
|---|---|---|---|---|
| 0 | +0.030 / +0.252 | +0.028 / −0.042 | +0.029 / +0.768 | +0.068 / +0.085 |
| 1 | +0.005 / +0.122 | +0.037 / +0.159 | −0.022 / −0.055 | +0.092 / +0.168 |
| 2 | −0.005 / +0.078 | +0.047 / +0.264 | +0.011 / +0.154 | +0.058 / +0.112 |
| 3 | +0.012 / +0.204 | −0.008 / −0.022 | +0.019 / +0.632 | +0.060 / +0.158 |
| 4 | −0.011 / +0.120 | +0.005 / −0.051 | −0.005 / +0.187 | **+0.212 / +0.572** |

- **Absolute:** median +0.024, positive in 15 of 20 folds. Without the 2-station fold
  (partition 4, fold 3) the mean is +0.024.
- **Anomaly:** median +0.156, positive in 16 of 20.
- **In partitions 0–3, fold 3 holds ITCSM_01–03.** These are the only folds where the
  baseline beats zero absolute R², and the absolute gain there is consistent (+0.058
  to +0.092).

**Geometry in grid mode (supplementary)**

| Partition | Fold 0 | Fold 1 | Fold 2 | Fold 3 |
|---|---|---|---|---|
| 0 | +0.132 / +0.933 | +0.055 / +0.019 | −0.027 / +0.704 | −0.041 / −0.206 |
| 1 | −0.019 / −0.001 | +0.039 / +0.268 | +0.099 / +0.259 | −0.010 / −0.211 |
| 2 | +0.019 / +0.055 | +0.048 / +0.399 | +0.098 / +0.322 | −0.034 / −0.180 |
| 3 | +0.065 / +0.373 | +0.052 / +0.108 | +0.041 / +0.338 | −0.027 / −0.078 |
| 4 | +0.086 / +0.851 | −0.011 / −0.105 | −0.058 / +0.442 | +0.223 / +2.500 |

- **Absolute:** median +0.040, positive in 12 of 20.
- **Anomaly:** median +0.264, positive in 14 of 20.
- **The paired set here is the grid arm's 2,475 samples.** The grid model trains on
  fewer samples and still scores better on them in anomaly terms.
- **A possible reason, not verified:** grid mode refuses samples by S1 frame CRS, which
  follows the relative orbit. It therefore drops whole orbits, and with them orbit
  (incidence-angle) variation in backscatter.

The four identical arms are 0.000 in every fold.

## East/west breakdown (secondary)

**Descriptive only. It rests on 4 eastern stations; no significance is claimed.**
Per-station paired ΔR² within each test fold, averaged over folds and partitions:

| Arm | Absolute: 4 eastern | Absolute: other 13 | Anomaly: 4 eastern | Anomaly: other 13 |
|---|---|---|---|---|
| without quality_missingness | +0.188 | −0.065 | +0.278 | +0.849 |
| geometry in grid mode | +0.001 | +0.258 | −0.173 | +1.551 |

Per-station R² is computed on one station's samples, where variance is small and
values swing widely. The breakdown was planned to separate a temporal-gate effect by
S2 age. **The temporal gate refused nothing, so it has nothing to separate here.**

## Baseline skill

Baseline arm, per fold:

| | Median | Range | Folds above 0 |
|---|---|---|---|
| Random forest, absolute R² | **−0.073** | −6.63 to +0.28 | 4 of 20, all the fold with ITCSM_01–03 |
| Training-mean predictor, absolute R² | −0.02 | −5.00 to 0.00 | 0 of 20 |
| Random forest, anomaly R² | **−1.90** | −15.0 to −1.0 | 0 of 20 |

The forest beats the training mean in 7 of 20 folds.

- **Absolute.** Eight station-pixel values from one S1 pass and one S2 scene do not
  carry a held-out station's moisture level. Between-station differences are 37% of
  the variance, and nothing in the features encodes soil.
- **Anomaly.** An anomaly R² of −1 is what uncorrelated predictions of equal variance
  score. Within a station and month, S1 backscatter moves with orbit geometry,
  vegetation and speckle as much as with moisture.
- **What this means for the limit.** The power analysis found ΔR² 0.062 (absolute)
  and 0.235 (anomaly) for its staleness test, with a predictor that had skill. Here the
  baseline has none, and fold noise in the paired delta is 0.052 (absolute) and 0.223
  (anomaly), at or above the proxy's. **The ≈ 0.05 limit stands, and is optimistic
  for this task.**
- **Why a random forest.** It is the repository's reference baseline, invariant to
  linear-vs-dB scaling, and needs no tuning. A different learner on the same eight
  features would not supply the missing between-station information. "Diagnostic: why
  the model has no skill" locates the weakness in the features.

## Diagnostic: why the model has no skill

**Diagnostic only, not the result.** The spatial split above is the result. Job 247700,
`experiments/open_cube/diagnose_skill.py`: same 3,395 samples, same features, same
forest.

| Split | RF absolute R² | Predicting each station's own training mean | RF anomaly R² |
|---|---|---|---|
| Spatial, stratified station blocks (the result) | median −0.073 | not available (station unseen) | median −1.90 |
| **Within-station temporal** (diagnostic): interleaved month folds {1,5,9}, {2,6,10}, {3,7,11}, {4,8,12}; every station in train and test | pooled **0.184** (folds −0.07 to 0.37) | pooled **0.545** | pooled −2.42 |
| Temporal, R² within one station (median over stations) | **−0.473** | −0.100 | −3.48 |

**Signal at the pixel.** Per-station Pearson r of S1 VV (dB) with 5 cm soil moisture:
- **median 0.29** over all passes, positive at 16 of 17 stations;
- **median 0.30** within a single relative orbit, positive in 60 of 68 station-orbits
  (orbit 88: 0.20; the others 0.32–0.35).

**This is a feature problem first, not a generalisation problem.**
- **With every station in training, the forest still fails.** Predicting each
  station's training mean scores 0.55; the forest scores 0.18. Within a station it is
  worse than that constant (−0.47). If the trouble were only transfer to unseen
  stations, the temporal split would predict well. It does not.
- **The radar signal exists but is weak.** r ≈ 0.3 explains about 9% of a station's
  variance, and holding the orbit fixed barely changes it, so incidence angle is not
  what hides it.
- **Six of the eight features are optical reflectances.** They follow phenology and
  illumination, not soil moisture. The forest fits them, and its predictions move
  within a station more than the target does: anomaly R² of −2 to −3.5 under both
  splits.
- **The spatial split adds a second problem on top:** the level. Between-station
  differences are 37% of the variance, and nothing in the features encodes soil.

Not tested here: whether point-to-pixel scale (a 5 cm probe against a 10 m pixel)
caps r near 0.3, as PLAN.md open question 1 anticipates.

## Diagnostic: why four gates refused nothing

**Tested by bypassing the protections, not by reasoning about them.** Jobs 247701
(fetch) and 247702 (assemble), `experiments/open_cube/bypass_cube.py`. The cube is
rebuilt under each rule and all five gates run on it. fetch_year.py and build_cube.py
are unchanged.

**Where the protections live.** fetch_year.py chooses the S2 tile and the S1 frame.
**Temporal ordering is not in the fetcher:** fetch_year.py fetches every date, and
build_cube.py's assembly takes S2 at or before the pass. Assembly also applies the
per-pixel rules at the station.

**How often they intervened in 2019** (from the fetch manifest):

| Protection | Station-dates or passes changed |
|---|---|
| S2 best-scoring tile instead of the first containing one | 91 of 2,118, of which **14** for an unmeasured station pixel, 4 for the 3 × 3 window and **73 only on chip coverage** |
| S1 next frame, first empty at the station | 11 of 3,977 |
| S2 chip tagged `station_measured: false` | 12 |
| (S2 dates where no tile contains the station: 313, all at ITCSM_01/04/07/09/08) | not a protection: there is no chip to take, under either rule |

The naive chips were fetched: 659 chips, 6.7 MB stored, 1 min 26 s.

**Gate refusals by rule.** Geometry in point mode. "Changed" means samples that differ
from the protected cube in S1 file, S2 file or S2 time.

| Rule | Samples | Changed | Point geometry | Resolution | **Temporal** | **Quality** | Sensor physics | Changed samples refused by any gate |
|---|---|---|---|---|---|---|---|---|
| protected (reproduces docs/cube_2019.md) | 3,505 | 0 | 0 | 0 | 0 | 110 | 0 | — |
| first tile / first frame | 3,495 | 215 | 0 | 0 | 0 | 103 (65 changed) | 0 | **65 of 215 (30%)** |
| S2 after the pass allowed | 3,716 | 1,875 | 0 | 0 | **1,875** | 179 (119 changed) | 0 | **1,875 of 1,875 (100%)** |
| edge checks skipped | 3,505 | 0 | 0 | 0 | 0 | 110 | 0 | — |
| all three bypassed | 3,705 | 1,979 | 0 | 0 | 1,874 | 165 (144 changed) | 0 | 1,909 of 1,979 (96%) |
| all three + build_cube's per-pixel rules off | 3,816 | 3,234 | 0 | 0 | 1,905 | 447 (441 changed) | 0 | 2,111 of 3,234 (65%) |

**What the gates catch, plainly:**
1. **Temporal: the gate catches everything the ordering prevents.** Every one of the
   1,875 samples given a later S2 scene was refused as temporal leakage. **In the main
   cube, this enforcement had moved upstream, into assembly.** The gate refused nothing
   because assembly never handed it a future scene, not because the data lacked the
   failure.
2. **Quality: the gate catches part of what tile choice prevents.** It refused 65 of
   the 215 samples whose tile or frame changed, for out-of-range pixels in the chip
   (98 B02-floor and 11 ceiling messages, 6 over 30% invalid). Most of the 91 swaps
   were chip-coverage ties, not station-pixel protections. So these refusals are mostly
   the chip-level rigidity already documented, not caught fill at the sample pixel.
   - The 11 empty S1 frames never reached the gates: build_cube's own S1-nodata rule
     dropped them (8 → 19 drops).
   - Whether the 14 real station-pixel swaps are also screened by the per-pixel rule
     before the gates is not verified.
3. **Edge checks: redundant in this cube.** All 12 `station_measured: false` chips
   also fail build_cube's per-pixel SCL/B02 rule; skipping the tag changes no sample.
4. **Per-pixel validity is enforced only upstream.** With the per-pixel rules off as
   well, **1,123 of the 3,234 changed samples pass every gate.** By construction, these
   are samples whose station pixel the per-pixel rules reject: cloud, shadow, water or
   fill in S2, or S1 nodata. No gate judges the station pixel; the quality gate judges
   the chip (PLAN.md, the next open decision).
5. **Geometry, resolution and sensor physics refused nothing under any rule.** None of
   the bypassed protections concerns them. Their failures are absent by the pipeline's
   design:
   - every value is read at the station, on each source's own grid, with no
     resampling, so there is nothing to misregister or upsample;
   - units and calibration are declared per product.

   These three are neither "prevented by the fetcher" nor shown to work here. Exercising
   them needs a different perturbation: a shared-grid resample, or a mis-declared unit.
   That was not done.

**What this means for the study.** Of the four silent gates:
- **one (temporal) was silent because validity enforcement had already moved upstream,
  to assembly.** The gate catches the failure in full when that upstream step is
  removed.
- **three were silent because this point-read design has no place for their failures.**

The one gap runs the other way: **the station pixel's own validity is enforced only
upstream**, and no gate would catch its loss.

## Caveats

1. **5 partitions, 4 distinct.** Seeds 1 and 2 give the same stratified blocks, so
   those two differ only in the forest's random state. The power analysis used the same
   seeds 0–4 (`eastwest_blocks.py`) and has the same duplicate. The seeds were fixed in
   advance and are not re-picked after seeing results.
2. **One 2-station fold.** Partition 4 puts ITCSM_01 and ITCSM_08 alone in fold 3:
   k-means leaves ITCSM_08 as a one-station western block. PLAN.md's "3–7 per fold" was
   checked on two partitions only. That fold has baseline R² −6.63 (null −5.00) and
   inflates the means and sds, so medians are given.
3. **The paired set fixes the test targets, not the training set.** The arm and
   baseline models train on different sample counts by design; that is the effect of a
   gate on training.
4. **One model, one year, nothing tuned.** No hyperparameter should be tuned to make a
   gate effect appear.

## Reproduce

```bash
cd ~/work/logs && sbatch -A mif76448_mif ~/eocube/scripts/ablation.sbatch 2019 5
```

`experiments/open_cube/run_ablation.py` reads the cube's `samples.csv` and `gates.json`
(build_cube.py output), no imagery. The block partitions come from
`experiments/open_cube/blocks.py`, which the power check and the east/west analysis
also use.
