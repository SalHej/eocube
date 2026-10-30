# The 2019 open-cube ablation: leave one gate out

Twente soil moisture, 2019: 3,505 samples at 17 stations, the cube documented in
docs/cube_2019.md. Ablation jobs **247696** (before: chip-level quality) and **247798**
(after: station-pixel quality). Results under `~/work/results/open_cube/` on the
cluster.

> **Detection limit: ΔR² ≈ 0.05** (PLAN.md, "Power check"). Any gate whose effect falls
> below it is reported as **"below detection limit on this task"**, never as "no effect".
>
> **This ablation measures downstream accuracy on one task. It does not measure
> validity.** A gate can be protecting correctness without moving R² on a smooth
> target: a misregistered layer, a leaked future scene or a cloud-filled pixel are
> wrong whether or not a model's score notices.

## Conclusion

1. **No gate had a detectable effect on absolute R² on this task.** Before the change
   below, the quality gate's removal moved paired ΔR² by +0.033 ± 0.052; the other
   four gates refused nothing. Each is below detection limit on this task.
2. **The model has no skill, and the cause is the features, not the split.** With every
   station in training, the random forest scores R² 0.18 against 0.55 for predicting
   each station's own mean. The radar signal at the pixel is real but weak (r ≈ 0.30).
3. **The temporal check had moved upstream, into assembly, and the gate fires when
   that is removed.** Allowing an S2 scene after the pass gave 1,875 leaked samples;
   the temporal gate refused all 1,875.
4. **The framework judged layers, not the sample pixel, and 1,123 samples passed
   through it with invalid station pixels.** These were stations read through cloud,
   shadow, water or fill, in the cube built with assembly's per-pixel rules removed.
   Every gate passed them, because every gate looked at the 1 km chip and none at the
   one pixel the sample is. In the documented cube those rules had already kept such
   samples out, so the flaw was masked there. Meanwhile the quality gate refused 110
   samples for pixels the sample never used.

**The fourth is the contribution.** A validity framework for point-sampled cubes that
does not check the point being measured has a design flaw. That flaw stayed invisible
while assembly's per-pixel rules happened to cover it. It was found by running the
framework and removing the upstream protections one at a time, not by inspection.

**The fix is now in the method.** In point mode, the quality gate judges the sample
pixel: it refuses nodata, implausible values or an invalid QA class there, and records
chip-level anomalies as decisions. Measured on the same cube:
- **its 110 refusals are gone;**
- **so is its negative effect on accuracy:** anomaly ΔR² was +0.193 when it was
  removed; now it is 0.000;
- **of the 1,123 invalid-pixel samples, all but 26 are now refused** ("Station-pixel
  validity: before and after").

The three gates that never fired were then given their failures on purpose. Each fired
on 20 of 20 samples. Each also stayed silent in the cases where it is blind by design,
which are stated, not hidden ("Validation: the silent gates under induced failures").
**Their silence on the real cube means the failures were absent, not unseen**, within
the bounds that section names.

## Station-pixel validity: before and after

**What changed** (commit 00b166c):
- `eocube.io.describe` now records, at the declared sample point:
  - the value in each band;
  - whether it is nodata;
  - whether it is outside the declared plausible range;
  - the QA class there, read from a declared `qa_mask_path` on its own grid and checked
    against `qa_valid_classes`.
- `check_quality_missingness(mode="point")` refuses if, in any layer, the sample pixel
  is outside the layer, nodata, implausible, or of an invalid QA class. Chip-level
  failures become PASS_WITH_DECISION. A QA mask whose class at the pixel was not read
  is reported as unchecked, not valid.
- Nine new tests, covering both paths:
  - a chip fill row away from the station: refused in grid mode, recorded in point mode;
  - cloud at the station in a clean chip: passed in grid mode, refused in point mode;
  - single-pixel fill at the station, within the chip tolerance: refused in point mode;
  - nodata at the station; a clean pixel; no `sample_pixel`; an unread QA class; an
    invalid mode.

For this cube the S2 layers declare their own SCL chip as the QA mask, with valid
classes 4 and 5: the rule assembly already applied. The bounds are unchanged
(B02 ≥ 50, other bands ≤ 12,000, S1 ≥ 1e-5).

**On the documented cube** (job 247797; the same 3,505 assembled samples):

| Quality gate | Refused | Pass with decision | Pass |
|---|---|---|---|
| Chip level (before) | 110 | 392 | 3,003 |
| **Station pixel (after)** | **0** | 502 | 3,003 |

The 110 became decisions, as their causes were all away from the station pixel. The
rebuilt cube's chip-level verdicts match the previous build for all 3,505 samples, so
the "before" arm is a reproduction, not an approximation.

**The documented cube grew; it did not shrink.** The 1,123 invalid-pixel samples were
never in it: assembly's per-pixel rules (SCL 4/5, B02 ≥ 50, S1 not nodata) had already
excluded them, before any gate ran. They exist only in the bypassed cube below. So on
the documented cube, station-pixel quality refuses nothing and returns the 110.

Samples kept by all five gates, per station:

| Station | Assembled | Kept, chip-level quality | **Kept, station-pixel quality** |
|---|---|---|---|
| ITCSM_01 | 205 | 205 | 205 |
| ITCSM_02 | 206 | 202 | 206 |
| ITCSM_03 | 167 | 153 | 167 |
| ITCSM_04 | 201 | 193 | 201 |
| ITCSM_05 | 223 | 217 | 223 |
| ITCSM_07 | 205 | 191 | 205 |
| ITCSM_08 | 220 | 188 | 220 |
| ITCSM_09 | 172 | 172 | 172 |
| ITCSM_10 | 200 | 200 | 200 |
| ITCSM_11 | 217 | 210 | 217 |
| ITCSM_12 | 231 | 231 | 231 |
| ITCSM_13 | 223 | 219 | 223 |
| ITCSM_14 | 229 | 229 | 229 |
| ITCSM_15 | 146 | 140 | 146 |
| ITCSM_16 | 209 | 205 | 209 |
| ITCSM_17 | 234 | 223 | 234 |
| ITCSM_18 | 217 | 217 | 217 |
| **Total** | **3,505** | **3,395** | **3,505** |

**No station lost samples, so every fold is still supported.** The smallest station
keeps 146 (ITCSM_15). The smallest test fold holds 425 samples: the 2-station fold of
partition 4.

**Ablation, before and after** (paired ΔR², mean ± sd over 20 folds):

| | Samples kept by all gates | Removing the quality gate: ΔR² absolute | Removing the quality gate: ΔR² anomaly |
|---|---|---|---|
| Before: chip-level quality (job 247696) | 3,395 | +0.033 ± 0.052 | **+0.193 ± 0.223** (16 of 20 folds improved) |
| **After: station-pixel quality** (job 247798) | **3,505** | **0.000** | **0.000** |

**The baseline changed, so before and after are not like-for-like.** The before
baseline kept 3,395 samples; the after baseline keeps 3,505, the same 110 samples more.
Each ΔR² is paired within its own run, on the test samples both of its arms accept,
but the two runs' baselines train and test on different sets. The supplementary
chip-level arm inside job 247798 is the like-for-like comparison: same run, same
folds, the two quality modes side by side.

**The quality gate's negative effect did not just shrink; it went to zero.** The
station-pixel gate refuses nothing on this cube, so removing it changes nothing. The
chip-level strictness was the entire cause. Job 247798 also reruns the old gate as a
supplementary arm: against the new baseline it scores −0.033 ± 0.052 (absolute) and
−0.193 ± 0.223 (anomaly), the before figures with the sign reversed, as they should be.

**What the new gate catches, on the bypassed cubes** (job 247799):

| Rule | Changed samples | Refused by any gate, chip-level quality | **Refused by any gate, station-pixel quality** |
|---|---|---|---|
| first tile / first frame | 215 | 65 (chip rigidity; station pixels valid) | **0** |
| S2 after the pass allowed | 1,875 | 1,875 (temporal) | 1,875 (temporal) |
| all three bypassed | 1,979 | 1,909 | 1,874 |
| all three + per-pixel rules off | 3,234 | 2,111, so **1,123 pass** | **3,208, so 26 pass** |

- With assembly's per-pixel rules removed, the station-pixel gate alone refuses 2,614
  of the changed samples. **It now does the job those rules did.**
- On the tile-choice changes, it refuses none of the 215; the chip-level gate refused
  65. Those stations' pixels were valid.
- **The 26 that still pass were not examined individually.** The likeliest source is
  tile-choice changes with valid station pixels, which the first-tile arm shows pass
  (0 of 215 refused). This is not verified.

**The ablation has not been re-run on a cube without assembly's per-pixel rules.** That
experiment would ask whether the gate's catches move R²: the cube with them in,
against the cube with them refused.

## Setup

| Item | Choice |
|---|---|
| Cube | 2019, job 247673. One row per station and S1 pass. S1 VV/VH and S2 B02–B12 at the station pixel; 5 cm in-situ soil moisture |
| Gates | all five, run per sample on the real layers when the cube was built. **Geometry in point mode** (PLAN.md, "Geometry mode"). **Quality at chip level before (job 247696), at the station pixel after (job 247798)** |
| Bounds | **as declared**: S1 ≥ 1e-5; B02 in [50, 12000]; other S2 bands in [0, 12000] (PLAN.md, "Quality bounds") |
| Arms | baseline: a sample is kept unless any gate refuses it. "Without *g*": *g*'s refusals are ignored, every other gate applies. Supplementary: geometry in grid mode, the design not selected; after the change, also quality at chip level |
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

## Summary table (before: chip-level quality, job 247696)

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

## Summary table (after: station-pixel quality, job 247798)

Same folds, model and conventions as above.

| Arm | Kept | Refusal rate | Decisions (per sample) | R² absolute | **ΔR² absolute** | R² anomaly | **ΔR² anomaly** | Reading |
|---|---|---|---|---|---|---|---|---|
| **All gates active** | 3,505 | 0.0% | 8,927 (2.55) | −0.359 ± 1.370 | — | −3.014 ± 3.109 | — | baseline |
| without geometry_crs | 3,505 | 0.0% | 5,422 (1.55) | −0.359 ± 1.370 | 0.000 | −3.014 ± 3.109 | 0.000 | below detection limit on this task: refused nothing |
| without resolution | 3,505 | 0.0% | 5,422 (1.55) | −0.359 ± 1.370 | 0.000 | −3.014 ± 3.109 | 0.000 | below detection limit on this task: refused nothing |
| without temporal_alignment | 3,505 | 0.0% | 7,512 (2.14) | −0.359 ± 1.370 | 0.000 | −3.014 ± 3.109 | 0.000 | below detection limit on this task: refused nothing |
| **without quality_missingness** | 3,505 | 0.0% | 8,425 (2.40) | −0.359 ± 1.370 | **0.000** | −3.014 ± 3.109 | **0.000** | below detection limit on this task: refused nothing |
| without sensor_physics | 3,505 | 0.0% | 8,927 (2.55) | −0.359 ± 1.370 | 0.000 | −3.014 ± 3.109 | 0.000 | below detection limit on this task: refused nothing |
| *geometry in grid mode* | 2,526 | 27.9% | 3,859 (1.53) | −0.351 ± 1.420 | +0.031 ± 0.059 | −2.831 ± 3.202 | +0.247 ± 0.391 | absolute: below detection limit on this task. Anomaly: above it, against grid mode |
| *quality at chip level (before)* | 3,395 | 3.1% | 8,566 (2.52) | −0.404 ± 1.479 | −0.033 ± 0.052 | −3.196 ± 3.350 | −0.193 ± 0.223 | the before result, sign reversed against the new baseline |

The quality gate's decisions rise from 392 to 502: the 110 former refusals are now
recorded as chip-level decisions. The grid-mode arm differs slightly from before
(2,526 kept, not 2,475), because the 110 quality refusals no longer overlap it.

## Per-fold deltas (before: chip-level quality)

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

## East/west breakdown (secondary; before: chip-level quality)

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

**Gate refusals by rule** (job 247702, before the station-pixel change). Geometry in point mode, quality at chip level. "Changed" means samples that differ
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
4. **Per-pixel validity was enforced only upstream** (fixed since; "Station-pixel validity:
   before and after"). With the per-pixel rules off as
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

The one gap ran the other way: **the station pixel's own validity was enforced only
upstream**, and no gate would have caught its loss. The station-pixel quality gate now
does: 1,123 passing samples became 26.

## Validation: the silent gates under induced failures

**Not part of the ablation, and the method is unchanged.** This is evidence that the
three gates that never fired are not blind. Job 247800,
`experiments/open_cube/perturb_gates.py`: 20 real samples, spread across all 17 stations.
Each failure was applied to every sample, then all five gates ran, with geometry and
quality in point mode. The cube's files were not touched; perturbed chips are copies.
**Cases where a gate should not fire are included on purpose**, so that the test can
tell blindness from absence.

| Induced failure | Gate expected to fire | Geometry | Resolution | Temporal | Quality | Sensor physics |
|---|---|---|---|---|---|---|
| none (unperturbed) | — | 0/20 | 0/20 | 0/20 | 0/20 | 0/20 |
| G1 S2 chip from another station (a join error) | geometry | **20/20** | 0 | 0 | 20/20 | 0 |
| G2 S2 CRS mislabelled, zone 31 ↔ 32 | geometry | **20/20** | 0 | 0 | 20/20 | 0 |
| G3 S2 transform shifted 2 km | geometry | **20/20** | 0 | 0 | 20/20 | 0 |
| G4 S2 transform shifted 30 m | none: **blind** | 0/20 | 0 | 0 | 0 | 0 |
| R1 native layers, declared 10 m target (20 m bands would be upsampled) | resolution | 0 | **20/20** | 0 | 0 | 0 |
| R2 the same, upsampling allowed | none: recorded as a decision | 0 | 0/20 | 0 | 0 | 0 |
| R3 S2 already resampled onto a shared 10 m grid | none: **blind** | 0 | 0/20 | 0 | 0 | 0 |
| G5 that shared grid, grid-mode geometry, one band shifted 0.3 px | geometry | **20/20** | 0 | 0 | 0 | 0 |
| G6 that shared grid, grid-mode geometry, unshifted | none | 0/20 | 0 | 0 | 0 | 0 |
| S1 SAR units undeclared | sensor physics | 0 | 0 | 0 | 0 | **20/20** |
| S2 SAR declared not terrain-corrected | sensor physics | 0 | 0 | 0 | 0 | **20/20** |
| S3 SAR declared uncalibrated | sensor physics | 0 | 0 | 0 | 0 | **20/20** |
| S4 dB layers averaged (`mean(S1_VV, S1_VH)`), declared dB | sensor physics | 0 | 0 | 0 | 0 | **20/20** |
| S5 optical-radar normalised difference | sensor physics | 0 | 0 | 0 | 0 | **20/20** |
| S6 dB values declared as linear | sensor physics **blind**; quality catches | 0 | 0 | 0 | **20/20** | 0/20 |

**Every gate fired on every sample where its failure was present, and nowhere else.**
**No over-sensitivity was seen.** The clean controls fired nothing:
- the unperturbed samples: 0 of 20 on every gate;
- the shared grid in grid mode, unshifted (G6): 0 of 20;
- upsampling permitted and recorded (R2): 0 of 20, PASS_WITH_DECISION.

The quality gate's 20 of 20 on G1–G3 is correct, not over-firing: the station really
does lie outside those chips.
The station-pixel quality gate also caught G1–G3 (the station lies outside the chip)
and S6. In S6 the sample pixel reads −15.3, below the declared S1 floor of 1e-5:
plausibility at the pixel catches a units error that the declaration-only physics
gate cannot see.

**Where the gates are blind, by design:**
- **G4, a georeferencing error smaller than the chip.** Point mode checks that each
  layer was read where its own metadata says the station is. If the metadata is wrong
  by 30 m, the pixel read is wrong by 30 m and nothing in the metadata shows it.
  Detecting that needs image matching against a reference, which no gate does.
- **R3, upsampling already done.** A band resampled to 10 m before description
  reports 10 m. The resolution gate prevents upsampling at assembly; it cannot detect
  it after the fact without a declared native resolution.
- **S6 for the physics gate.** Sensor physics checks declarations and expressions, not
  values. A wrong declaration passes it; the plausible range is what catches it.

**None of the three is an implementation bug.** In each, the gate does what its
contract says, and the failure lies outside that contract. Two are candidate design
gaps worth closing:
- R3: a declared native GSD would let the resolution gate see upsampling done before
  description;
- S6: the physics gate could cross-check declared units against the value range
  `describe()` already records.

G4 needs image matching, which is out of scope for a metadata gate.

**What this establishes.** On the real cube, geometry, resolution and sensor physics
were silent because their failures were absent, not because the gates cannot see them,
for every failure tested here. The blind cases above bound that claim.

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
cd ~/work/logs
sbatch -A mif76448_mif ~/eocube/scripts/build_cube.sbatch 2019          # gates, both quality modes
sbatch -A mif76448_mif ~/eocube/scripts/ablation.sbatch 2019 5          # after it finishes
sbatch -A mif76448_mif ~/eocube/scripts/diagnose.sbatch skill
sbatch -A mif76448_mif ~/eocube/scripts/diagnose.sbatch bypass-fetch     # then bypass-assemble
sbatch -A mif76448_mif ~/eocube/scripts/diagnose.sbatch perturb
```

`experiments/open_cube/run_ablation.py` reads the cube's `samples.csv` and `gates.json`
(build_cube.py output), no imagery. The block partitions come from
`experiments/open_cube/blocks.py`, which the power check and the east/west analysis
also use.
