# Window size around the station pixel

An extension of the 2019 Twente study (PLAN.md, open question 1). **The 1 × 1 point-mode
ablation in docs/open_ablation.md remains the main result; nothing here replaces it.**
The design was fixed in PLAN.md ("Window size (open question 1)") before any result.

- **Job:** 247806 (`scripts/window_size.sbatch`, 4 CPUs, 9 min 6 s, 0.61 CPU-hours).
- **Script:** `experiments/open_cube/window_size.py`.
- **Results:** `~/work/results/open_cube/window_2019/window_size.json`.
- **No new data:** windows were cut from the fetched 1 km chips.

> **Detection limit: ΔR² ≈ 0.05.** As in the ablation, anything below it is "below
> detection limit on this task". The model has no skill on held-out stations at 1 × 1
> (docs/open_ablation.md), and that limits what any R² comparison here can show.

## Result

1. **r between VV and soil moisture rises with the window, mostly as expected.** Within
   one orbit, the median per-station r goes 0.313 → 0.337 → 0.389 → 0.403 from 1 × 1 to
   9 × 9, and r rises at 15 of 17 stations. It has **not** levelled off by 90 m.
   - The rise fits the published behaviour, where averaging reduces speckle.
   - The missing plateau does not: published gains stagnate after the first few
     aggregations, and here r was still climbing at 9 × 9.
2. **Average-then-calculate (AtC) gives no detectable change in R² at any window.**
   - Median paired ΔR² is −0.045 to +0.081.
   - The fold-to-fold sd is 0.10–0.45, several times the detection limit.
   - **Windowing the radar alone, where speckle lives, does nothing:** median ΔR² is
     +0.008 to +0.021.
3. **Calculate-then-average (CtA) improves R² in every fold, but not because it tracks
   moisture.**
   - Absolute ΔR² is +0.17 to +0.26, with 19 or 20 of 20 folds positive.
   - **Anomaly R² stays negative at every size:** −0.68 at 9 × 9 against −1.69 at
     1 × 1. Predicting no anomaly at all would score 0.
   - The likeliest reading: averaging 9–81 noisy predictions of a model without skill
     shrinks their spread toward a constant. That raises R² without adding signal.
   - **This design cannot separate that from a real CtA advantage.**
4. **Representativeness error was not detected.** Per-station gains in r show no
   relation to how heterogeneous a station's surroundings are (Spearman −0.01 against
   the NDVI spread). 17 stations is too few to rule it out.
5. **Chip-level validity is the wrong question at every window tested.** At 9 × 9 the
   window covers 0.81% of the chip. The window reproduces 31 of the chip's 110
   refusals; the other 79 remain refusals for pixels the sample does not use.

**Plainly: on this network and this model, window size changes the radar signal at the
pixel but not the downstream result.** That is a null for R², reported as such.

## Design

**Windows:**
- 1 × 1, 3 × 3, 5 × 5 and 9 × 9 pixels (10, 30, 50, 90 m), centred on the station pixel,
  on each sensor's own 10 m grid.
- S2's 20 m bands and SCL are read at the 10 m cell centres.
- S1 is averaged in linear power, not dB.

**Everything else as in the main study:**
- the same samples;
- the same S2 date per sample;
- the stratified station folds, 5 partitions × 4 folds;
- the random forest on eight features.

**Validity rule** (PLAN.md): the centre pixel must be valid, and the window mean uses
its valid pixels only.
- **S2:** SCL 4/5 and B02 ≥ 50.
- **S1:** VV and VH positive and not nodata.

The centre is the 1 × 1 sample, so **the sample set is identical at every size.** All
3,505 centres are valid in both sensors. A second comparison scores every size on the
**2,284 samples that are fully valid at 9 × 9**.

**What each validity rule would cost:**

| Window | Centre valid (the rule used) | All pixels valid | ≥ 50% valid | Mean valid fraction, S2 |
|---|---|---|---|---|
| 1 × 1 | 3,505 | 3,505 | 3,505 | 1.000 |
| 3 × 3 | 3,505 | 3,080 (−12%) | 3,497 | 0.970 |
| 5 × 5 | 3,505 | 2,743 (−22%) | 3,498 | 0.952 |
| 9 × 9 | 3,505 | 2,284 (−35%) | 3,469 | 0.931 |

S1's mean valid fraction is 1.000 (to three decimals) at every size. The losses are
optical: cloud, shadow, water, or a dark or bright pixel near the station. Requiring all pixels valid
would have removed a third of the samples at 9 × 9, and would have changed the sample
set with the window size.

## Results

### r between VV (dB) and soil moisture

| Window | Median over stations, all passes | Median within one orbit (68 station-orbits) | Stations where r is higher than at 1 × 1 |
|---|---|---|---|
| 1 × 1 | 0.299 | 0.313 | — |
| 3 × 3 | 0.328 | 0.337 | 16 of 17 |
| 5 × 5 | 0.288 | 0.389 | 14 of 17 |
| 9 × 9 | 0.352 | 0.403 | 15 of 17 |

The within-orbit figure holds incidence angle fixed, and it rises at every step. The
all-passes median is noisier, because orbit geometry mixes in.

### R² on held-out stations

Paired ΔR² against 1 × 1 on the same folds: mean ± sd over 20 folds, the median, and how
many folds improved.

**Centre-valid rule, 3,505 samples.** At 1 × 1: R² median −0.058; anomaly R² median −1.694.

| Window | Arm | R² median | ΔR² absolute | ΔR² anomaly | Anomaly R² median |
|---|---|---|---|---|---|
| 3 × 3 | AtC, both sensors | −0.118 | −0.114 ± 0.273, med −0.045, 4/20 | −0.034 ± 1.207, med −0.186 | −1.855 |
| | AtC, S1 only | −0.049 | −0.018 ± 0.115, med +0.008, 13/20 | −0.129 ± 0.679, med +0.031 | |
| | AtC, S2 only | −0.134 | −0.125 ± 0.289, med −0.065, 5/20 | −0.048 ± 1.167, med −0.199 | |
| | **CtA** | **+0.071** | **+0.169 ± 0.285, med +0.075, 19/20** | +1.373 ± 1.627, med +0.742 | −1.059 |
| 5 × 5 | AtC, both sensors | −0.049 | −0.081 ± 0.447, med −0.002, 10/20 | −0.213 ± 1.821, med +0.027 | −1.869 |
| | AtC, S1 only | −0.033 | −0.004 ± 0.103, med +0.009, 13/20 | −0.061 ± 0.702, med +0.072 | |
| | AtC, S2 only | −0.067 | −0.079 ± 0.380, med +0.007, 11/20 | −0.151 ± 1.299, med −0.055 | |
| | **CtA** | **+0.100** | **+0.236 ± 0.319, med +0.145, 20/20** | +1.713 ± 1.936, med +0.888 | −0.853 |
| 9 × 9 | AtC, both sensors | −0.001 | +0.028 ± 0.347, med +0.081, 18/20 | +0.298 ± 2.323, med +0.295 | −1.399 |
| | AtC, S1 only | −0.028 | −0.047 ± 0.436, med +0.021, 14/20 | −0.106 ± 1.862, med +0.168 | |
| | AtC, S2 only | −0.008 | +0.077 ± 0.166, med +0.086, 18/20 | +0.437 ± 1.422, med +0.302 | |
| | **CtA** | **+0.116** | **+0.262 ± 0.318, med +0.194, 20/20** | +2.017 ± 2.298, med +1.017 | −0.682 |

**Fully valid at 9 × 9, 2,284 samples.** At 1 × 1: R² median +0.075; anomaly R² median
−1.689. The pattern is the same:
- **AtC:** median ΔR² from −0.018 to +0.030, with no consistent sign.
- **CtA:** ΔR² +0.294, +0.319 and +0.296 at 3, 5 and 9; 20 of 20 folds improved at
  each size.

`window_size.json` has every arm.

### Reading it

- **AtC is below detection limit on this task at every window.** Its fold sd is
  0.10–0.45, against a limit of 0.05. The 9 × 9 AtC median of +0.081, positive in 18 of
  20 folds, is the only hint of a gain. Its mean is +0.028 ± 0.347.
- **The literature's shape** (improvement from 1 × 1 to 3 × 3, then diminishing
  returns) **is not seen in R².** AtC at 3 × 3 is no better than 1 × 1. In r, the
  improvement is seen but does not stagnate.
- **CtA is consistently better than AtC,** matching the reported "substantial
  advantage" in direction: at 9 × 9, +0.262 against +0.028. **The reason here is
  probably not the one in the literature.** CtA averages up to 81 predictions from a
  model with no skill. That shrinks their variance, and R² rises toward that of a
  constant. Anomaly R² stays below zero at every size, so no within-station tracking
  emerges.

**What the design cannot answer:** whether CtA would also beat AtC with a model that has
skill.

## Speckle or smoothing: can this design tell them apart?

**If larger windows had improved R², two explanations would compete:**
- **Speckle:** less noise in the radar. That predicts a gain from windowing S1 alone,
  similar at every station.
- **Smoothing that happens to match the surroundings:** larger windows average ground
  the probe does not represent. That predicts changes driven by the optical window, and
  varying with the heterogeneity around each station.

**The design separates them in part.** S2 has no speckle at 10 m, so windowing S2 alone
can only act through smoothing or representativeness.

| Signature | Observed |
|---|---|
| r rises when only S1 is averaged (the r figures use the S1 window only) | **Yes**: within-orbit r 0.313 → 0.403; up at 15 of 17 stations |
| R² rises when only S1 is averaged | **No**: median ΔR² +0.008 to +0.021, below detection limit |
| R² rises when only S2 is averaged | Only at 9 × 9: +0.077 ± 0.166, median +0.086, 18 of 20 folds; below detection limit |
| Per-station change in r tracks heterogeneity (median NDVI sd in the 9 × 9 window) | **No**: Spearman −0.01 over 17 stations |
| ... tracks radar texture (median VV coefficient of variation in the window) | Weakly, Spearman −0.30; descriptive only |

**The reading:**
- **The speckle effect is real at the level of the signal.** Averaging the radar raises
  its correlation with soil moisture.
- **It does not reach the model.** The forest's predictions are dominated by the six
  optical features, so a cleaner VV does not change R².
- **The small 9 × 9 gain, if it is anything, comes from the optical window.** That is
  smoothing, not speckle. With 17 stations and fold sd of 0.17, it cannot be separated
  from chance.
- **Representativeness error was not detected.** Two stations lose r as the window grows:
  - ITCSM_16, which has the most heterogeneous surroundings (NDVI sd 0.158) and the
    highest radar texture (CV 1.02);
  - ITCSM_17 (NDVI sd 0.117, CV 0.96).

  That fits the representativeness explanation, but two stations are an anecdote, not a
  result.

## Per station

Within-station R² from spatial cross-validation is dominated by each station's level
bias; for example, ITCSM_12 scores −29.6 at 1 × 1. It is in `window_size.json` but not
interpreted. The informative per-station quantity is r.

| Station | r 1 × 1 | r 9 × 9 | Change | NDVI sd, 9 × 9 | VV CV, 9 × 9 |
|---|---|---|---|---|---|
| ITCSM_01 (E) | 0.204 | 0.315 | +0.111 | 0.144 | 0.649 |
| ITCSM_02 | 0.489 | 0.663 | +0.174 | 0.102 | 0.550 |
| ITCSM_03 | 0.108 | 0.194 | +0.086 | 0.062 | 0.431 |
| ITCSM_04 (E) | 0.299 | 0.464 | +0.165 | 0.063 | 0.543 |
| ITCSM_05 | 0.340 | 0.404 | +0.064 | 0.045 | 0.549 |
| ITCSM_07 (E) | 0.316 | 0.320 | +0.004 | 0.058 | 0.559 |
| ITCSM_08 | 0.358 | 0.386 | +0.028 | 0.072 | 0.539 |
| ITCSM_09 (E) | 0.472 | 0.524 | +0.052 | 0.105 | 0.438 |
| ITCSM_10 | 0.403 | 0.506 | +0.103 | 0.109 | 0.442 |
| ITCSM_11 | 0.229 | 0.343 | +0.114 | 0.076 | 0.671 |
| ITCSM_12 | 0.338 | 0.352 | +0.014 | 0.079 | 0.748 |
| ITCSM_13 | 0.143 | 0.212 | +0.069 | 0.035 | 0.397 |
| ITCSM_14 | −0.018 | 0.353 | +0.371 | 0.115 | 0.665 |
| ITCSM_15 | 0.079 | 0.384 | +0.305 | 0.053 | 0.438 |
| ITCSM_16 | 0.153 | 0.143 | **−0.010** | 0.158 | 1.022 |
| ITCSM_17 | 0.350 | 0.145 | **−0.205** | 0.117 | 0.964 |
| ITCSM_18 | 0.125 | 0.345 | +0.220 | 0.148 | 0.476 |

r is computed over all passes, from the S1 window mean. NDVI sd and VV CV are medians over
the station's samples. (E) marks the four eastern stations.

The gain is not uniform:
- **Largest:** ITCSM_14 (+0.37) and ITCSM_15 (+0.31), both of which start near zero.
- **Negative:** at the two most textured stations, ITCSM_16 and ITCSM_17.

**Descriptive, 17 stations; no significance claimed.**

## The gate finding, by window size

The quality gate's chip criteria were applied to each window instead of the chip:
- refuse if more than 1% of non-nodata S2 pixels lie outside the declared ranges;
- refuse if more than 30% of S1 pixels are nodata.

These window verdicts were compared with the chip's verdicts on the same 3,505 samples.

| Window | Share of the chip | Window refuses | Chip refuses | Both | Chip only |
|---|---|---|---|---|---|
| 1 × 1 | 0.01% | 0 | 110 | 0 | 110 |
| 3 × 3 | 0.09% | 9 | 110 | 9 | 101 |
| 5 × 5 | 0.25% | 21 | 110 | 21 | 89 |
| 9 × 9 | 0.81% | 31 | 110 | 31 | 79 |

The window never refused a sample the chip accepted.

**Answer: chip-level validity is the wrong question at every window size tested.**
- **At 1 × 1** the two units are as far apart as they can be: one pixel judged by
  10,000. Every chip refusal is about pixels the sample does not use.
- **As the window grows, they converge slowly.** At 90 m the window reproduces 31 of the
  110, but 79 chip refusals still concern pixels outside it.
- **Extrapolating would be speculation.** The trend suggests agreement only as the window
  approaches the chip itself: about a kilometre, a hundred times the area at which the
  probe is matched.

**The chip's 1% tolerance does not transfer to small windows either.** In a 9 × 9 window,
one bad pixel is 1.2% and triggers a refusal. A window-level gate would need its own
tolerance; none was invented here.

**The 1,103 bypass samples are not tested here.** Under the centre-valid rule, a sample
with an invalid station pixel is excluded at every window size. Under a ≥ 50% rule, a
cloudy-centre sample with clear neighbours could re-enter. That was not measured: windows
were extracted only for the documented cube, where every centre is valid.

**This ties back to the main finding:**
- in a point-sampled cube, the unit of validity must be the unit of the sample;
- the station-pixel gate is correct for 1 × 1;
- a windowed cube would need the gate to judge the window, with a declared tolerance.

## Limits

- **The model has no skill at 1 × 1,** so R² comparisons start from a floor. A real
  window effect on a working model may differ in size and sign.
- **The detection limit of 0.05 is small against the fold noise here** (sd 0.10–0.45).
- **CtA pairs S1 and S2 pixels by their offset from each sensor's station pixel.** The two
  grids differ by up to ~7 m.
- **The 1 × 1 model is reused for CtA.** A CtA model trained on window pixels was not
  tested.
- **17 stations** for every per-station statement.
