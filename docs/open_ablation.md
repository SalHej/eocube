# The 2019 open-cube ablation: leave one gate out

Twente soil moisture, 2019, 3,505 samples at 17 stations (docs/cube_2019.md).
Jobs 247693 (reported) and 247692 (same run, earlier anomaly metric; see "Corrections").
Results: `~/work/results/open_cube/ablation_2019/ablation.json` on the cluster.

> **Detection limit: ΔR² ≈ 0.05.** Fold-to-fold noise in the paired delta is
> ±0.03–0.05 (PLAN.md, "Power check"). Any gate whose effect falls below it is
> **"below detection limit on this task"**, never "no effect". **On this run, no gate
> clears it.**
>
> **The ablation measures downstream impact, not validity.** A gate can protect
> correctness without moving R², and here the downstream model has no skill on
> held-out stations to begin with (see "Baseline comparison").

## Summary

- **Only the quality gate changed the sample set.** With point-mode geometry, it was
  the only gate that refused any sample: **110 of 3,505 (3.1%)**. The other four arms
  are **identical to the baseline**, sample for sample. They were not measured, and
  there was nothing to measure.
- **Removing the quality gate: ΔR² +0.033 ± 0.052 (absolute).** That is below the
  detection limit on this task. Its sign is positive: the model scored slightly better
  with the 110 samples back in training. That fits the finding that their station
  pixels were valid (docs/cube_2019.md).
- **The downstream model has no skill on held-out stations.** The baseline random forest
  has a median fold R² of −0.07. It beats the training-mean predictor in 7 of 20 folds.
  Within-station anomaly R² is −1 to −15. So neither the deltas nor the limit can be
  read as they would be for a working model.
- **The temporal gate was not tested.** It refuses nothing: it keeps and flags the
  1,415 samples with S2 older than 7 days. The stratified block design was chosen to
  protect exactly this gate's measurement.

## Setup

| Item | Choice |
|---|---|
| Cube | 2019, job 247673: 3,505 samples, S1 VV/VH and S2 B02–B12 at the station pixel |
| Gates | the five, run per sample on the real layers. **Geometry in point mode** (PLAN.md, "Geometry mode") |
| Declared bounds | **as declared**: S1 ≥ 1e-5; B02 in [50, 12000]; other S2 bands in [0, 12000] (PLAN.md, "Quality bounds") |
| Arms | baseline: keep a sample unless any gate refuses it. "Without *g*": ignore *g*'s refusals only. Supplementary: geometry in grid mode (not selected) |
| Folds | **stratified spatial blocks**: 4 folds, one eastern station each (PLAN.md, "Block design"). Never a random split |
| Seeds | **5 (0–4).** Each seed sets the block partition and the forest's random state |
| Model | random forest, 300 trees (`eocube.baselines.make_random_forest`), on the 8 station-pixel features, same for every arm |
| Primary metric | **paired ΔR²** per fold: the arm's model minus the baseline's model, on the test samples both accept |
| Also reported | R² on the arm's own accepted test samples; anomaly R² (target and prediction each minus their station-month mean); the training-mean predictor |
| Cost | 2 min 12 s on 4 CPUs per run (≈ 0.15 CPU-h); main partition, CPU only |

The gates already ran on every sample's layers when the cube was built (job 247673).
An arm is a filter on those recorded verdicts, so no gate is re-run and no layer
is re-read. That is equivalent to `eocube.ablation.run_ablation` with `skip`, applied
per sample, and keeps its conventions: **delta = arm − baseline, negative means the
gate was doing useful work**, and refusals are reported with the metrics.

## Results

20 folds per arm (5 seeds × 4 folds). Mean ± sd across folds.

| Arm | Samples kept | Refused | R² (own test set) | **Paired ΔR², absolute** | Paired ΔR², anomaly | Reading |
|---|---|---|---|---|---|---|
| **All gates active (baseline)** | 3,395 | 110 (3.1%) | −0.404 ± 1.479 | — | — | baseline |
| without geometry_crs | 3,395 | 110 (3.1%) | −0.404 ± 1.479 | 0.000 | 0.000 | identical to baseline: the gate refused nothing |
| without resolution | 3,395 | 110 (3.1%) | −0.404 ± 1.479 | 0.000 | 0.000 | identical to baseline: the gate refused nothing |
| without temporal_alignment | 3,395 | 110 (3.1%) | −0.404 ± 1.479 | 0.000 | 0.000 | identical to baseline: the gate refused nothing |
| **without quality_missingness** | **3,505** | **0** | −0.359 ± 1.370 | **+0.033 ± 0.052** (−0.022 to +0.212) | +0.193 ± 0.223 | **below detection limit on this task** |
| without sensor_physics | 3,395 | 110 (3.1%) | −0.404 ± 1.479 | 0.000 | 0.000 | identical to baseline: the gate refused nothing |
| *geometry in grid mode (not selected)* | 2,475 | 1,030 (29.4%) | −0.378 ± 1.459 | +0.037 ± 0.069 (−0.058 to +0.223) | +0.339 ± 0.608 | below detection limit on this task |

**The four identical arms are reported as below detection limit on this task,** with a
stronger qualifier: they were not tested. Their gates refused no sample in this cube,
so the arm equals the baseline by construction. That says nothing about what the gate
would do on a cube where it does refuse.

**The anomaly column is not read as an effect** (next section). Its means exceed 0.05,
but both models score far below the trivial anomaly predictor, which is zero
everywhere and scores 0. A difference between two models at R² ≈ −3 measures how
badly they fail, not what a gate is worth.

### The quality gate, fold by fold

Paired ΔR² (absolute) for "without quality_missingness":

| Seed | Fold 0 | Fold 1 | Fold 2 | Fold 3 | Seed mean |
|---|---|---|---|---|---|
| 0 | +0.030 | +0.028 | +0.029 | +0.068 | +0.039 |
| 1 | +0.005 | +0.037 | −0.022 | +0.092 | +0.028 |
| 2 | −0.005 | +0.047 | +0.011 | +0.058 | +0.027 |
| 3 | +0.012 | −0.008 | +0.019 | +0.060 | +0.021 |
| 4 | −0.011 | +0.005 | −0.005 | **+0.212** | +0.050 |

- **Positive in 15 of 20 folds.** Excluding the 2-station fold (+0.212; see "Caveats"),
  the mean is **+0.024**, 14 of 19 positive.
- **Four of the five seed means are below 0.05.** Seed 4's is 0.0503, carried by the
  2-station fold; without it, seed 4's mean is −0.004.
- The paired set is the baseline's own test samples, so the comparison is on identical
  targets. The only difference is 110 more training samples (3.2% more), and the model
  that trains on them is slightly, consistently better.

**Descriptive, resting on 4 stations, no significance claimed** (the secondary
analysis in PLAN.md): per-station paired ΔR² averages **+0.188 at the four eastern
stations** and **−0.065 at the other 13**. With a model this weak, this is reported,
not interpreted.

### Where the refusals fall

| Arm | Refused samples by station |
|---|---|
| Quality gate (all gates active) | ITCSM_08 32, 03 14, 07 14, 17 11, 04 8, 11 7, 05 6, 15 6, 02 4, 13 4, 16 4; six stations none |
| Grid-mode geometry (with quality) | every station, 42–85 each; 1,030 in total (979 grid + 110 quality − 59 both) |

The 110 come from 24 S2 observations, mostly winter dates. In all 110 the station
pixel passed the per-pixel rules (docs/cube_2019.md, job 247675). Each refusal is a
correct statement about other pixels in the 1 km chip. That is the evidence for the
open pixel-versus-chip decision (PLAN.md).

## Baseline comparison

Baseline arm, per fold. R² against the in-situ 5 cm soil moisture.

| | Median | Range | Folds positive |
|---|---|---|---|
| Random forest, absolute R² | **−0.07** | −6.63 to +0.28 | 4 of 20, all the fold with ITCSM_01–03 |
| Training-mean predictor, absolute R² | −0.02 | −5.00 to 0.00 | 0 of 20 |
| Random forest beats the training mean | | | **7 of 20 folds** |
| Random forest, anomaly R² | **−1.9** | −15.0 to −1.0 | 0 of 20 |

- **Absolute.** Eight station-pixel values from one S1 pass and one S2 scene do not
  carry a held-out station's soil moisture level. Between-station soil differences
  are 37% of the variance (PLAN.md, "Power check"), and nothing in these features
  encodes soil type.
- **Anomaly.** Within a station and month, the predictions move by about as much as the
  target does, and not with it. An anomaly R² of −1 is what uncorrelated predictions of
  equal variance score. The S1 backscatter at a field pixel is dominated by orbit
  geometry, vegetation and speckle; the Step 3 sample already showed a factor-of-3
  variation in VV over a dry week (PLAN.md).
- **What this does to the detection limit.** The ≈ 0.05 limit came from a proxy that
  predicted held-out stations from their neighbours' in-situ readings. It assumed a
  model with skill, and PLAN.md warned that real effects would be smaller. With no
  skill, fold noise is larger: the quality arm's ΔR² has sd 0.052, at the top of the
  proxy's ±0.03–0.05. So 0.05 is, if anything, optimistic for this task.

## Caveats

1. **The temporal gate retains and flags; it does not refuse.** 1,415 samples (40.4%)
   have S2 more than 7 days old. They are in every arm, flagged PASS_WITH_DECISION.
   Removing the gate therefore changes nothing. **Whether an out-of-window sample
   should be excluded is a method decision, not taken here.** Until it is, this
   ablation cannot measure the temporal gate, the main gate the cube was built to test.
2. **5 seeds, 4 distinct partitions.** Seeds 1 and 2 produce the same stratified blocks,
   so they differ only in the forest's random state. The seeds were fixed as 0–4 in
   advance and are not re-picked after seeing results.
3. **One 2-station fold.** Seed 4's partition puts ITCSM_01 and ITCSM_08 alone in fold
   3 (k-means left ITCSM_08 as a 1-station western block). PLAN.md's "3–7 per fold" was
   checked on two partitions only. That fold has baseline R² −6.63 (null −5.00) and
   dominates the arm means and sds; medians are given above for that reason.
4. **The paired comparison holds the test targets fixed, not the training set.** The
   arm and baseline models train on different sample counts by design. That is the
   gate's effect on training, which is what is measured here.
5. **One model, one year.** No hyperparameter was tuned, and none should be tuned to
   make a gate effect appear.

## Corrections during the run

- **Anomaly R², first run (job 247692): wrong by construction.** It subtracted one
  station-month climatology from both target and prediction. That leaves the residual
  unchanged and shrinks only the denominator, so it reported anomaly R² ≈ −22. The
  reported run (247693) demeans target and prediction separately within each
  station-month, which scores tracking. **Absolute R², ΔR² and refusals were identical
  in both runs.**

## What would make the ablation informative

These are not done; each is a decision, not a fix.

1. **A model with skill on held-out stations.** For example, features with a
   between-station reference (per-station backscatter normalisation, change detection
   against a dry reference, a soil map). That is a modelling choice, made once, before
   any gate result is looked at.
2. **The temporal decision:** keep out-of-window samples flagged, or exclude them. Only
   the second gives the temporal gate an arm.
3. **Pixel-versus-chip quality** (PLAN.md). On this cube, the chip-level gate refuses
   only samples whose station pixel is valid, so its measured effect is the cost of
   that rigidity.
4. **More years.** 2017, 2018 and 2020 would multiply samples, but not independent
   places: 17 stations remain the limit.

## Reproduce

```bash
cd ~/work/logs && sbatch -A <account> ~/eocube/scripts/ablation.sbatch 2019 5
```

`experiments/open_cube/run_ablation.py` reads the cube's `samples.csv` and `gates.json`
(build_cube.py output), no imagery. Block partitions come from
`experiments/open_cube/blocks.py`, shared with the power check and the east/west
analysis.
