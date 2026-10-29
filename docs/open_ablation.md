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
  features would not supply the missing between-station information. The weakness is
  in the features, not the learner.

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
