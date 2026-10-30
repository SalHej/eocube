# Open-data multi-sensor cube: acquisition plan

Status: plan for Step 2, written 2026-10-27. Steps 1–3 only; nothing here has been
built into a cube or run through an ablation yet.

**Guesses are marked ⚠️.** Everything unmarked was read from a catalogue, a file
header or a licence page during planning.

## Why this cube

The ablation needs (a) two or more sensors, (b) genuinely asynchronous
acquisitions, (c) differing GSD and (d) a continuous target with ground truth.
The peatland data has all four, but it is not accessible. This cube is the open
substitute: same target quantity (soil moisture), same radar physics (C-band
backscatter responds to near-surface moisture), same asynchrony problem.

## Products

| Role | Product | Source | Licence | Access |
|---|---|---|---|---|
| Target | Twente in-situ soil moisture, 5 cm, calibrated (`ITCSM_XX_cd.csv`) + QC flags (`ITCSM_XX_fg.csv`) | DANS, [doi:10.17026/dans-znj-wyg5](https://doi.org/10.17026/dans-znj-wyg5); paper [ESSD 15, 1889 (2023)](https://essd.copernicus.org/articles/15/1889/2023/) | **CC-BY-4.0** (dataset licence field) | Anonymous. 873 of 874 files open; the one restricted file is the superseded v1 readme |
| Radar | Sentinel-1 RTC, γ⁰ VV + VH, IW, 10 m | Microsoft Planetary Computer `sentinel-1-rtc` | CC-BY-4.0 (collection); contains modified Copernicus Sentinel data | Anonymous SAS token |
| Optical | Sentinel-2 L2A, B02 B03 B04 B08 (10 m), B11 B12 (20 m), SCL (20 m) | Planetary Computer `sentinel-2-l2a` (fallback: Earth Search `sentinel-2-l2a`) | Copernicus open licence; attribution required | Anonymous |

### Why not ISMN

The same Twente data is on ISMN, but its [terms](https://ismn.bafg.de/terms-and-conditions)
are more restrictive than the DANS release:

- **Registration is required** before any download.
- **Scientific use only**; commercial use is not permitted.
- **No onward distribution:** "Re-export or transfer of the original data to a
  third party is prohibited."
- **Derived results are not explicitly addressed.** Publication is allowed with
  acknowledgement of ISMN and every contributing network.
- **Models must not leak the data:** users "have to guarantee that ISMN data
  cannot be directly or indirectly retrieved through the created models and
  algorithms."

**Consequence for the public repo:** using ISMN would leave it unclear whether
derived tables, figures or trained models may be published. The DANS copy is
CC-BY-4.0, which permits redistribution of the data and anything derived from it,
with attribution. **Use the DANS copy and do not download from ISMN.** Even under
CC-BY, CLAUDE.md rule 2 still applies: no data is committed, only code, configs,
metrics and figures.

Required attributions for any publication: van der Velde et al. (2023), DANS dataset
DOI, "Contains modified Copernicus Sentinel data 2017–2020", Microsoft Planetary
Computer / Sentinel-1 RTC.

## Area and period

- **Network:** 20 stations (ITCSM_01–20) at 52.07–52.42 °N, 6.30–6.97 °E, mostly
  agricultural fields. "Twente A" and "Hupsel" are later additions (from 2017-12) with
  their own files.
- **Period: 2017-01-01 to 2020-12-31.** Sentinel-1A+1B and Sentinel-2A+2B were all
  operating (S2B from mid-2017), and the DANS series ends in 2020.
- **Seasons: all twelve months are sampled; no seasonal subset.** The target's
  dynamic range is seasonal. For ITCSM_08 (2017–2020, flag-0 readings at S1-like
  times), the standard deviation is 0.028 m³/m³ in summer (Jun–Aug) but 0.079 in
  winter (Nov–Mar), against a calibrated sensor error of ~0.025. A summer-only study
  would be close to measuring sensor noise. The Step 3 sample window (late June)
  happened to fall in the flattest part of the year.
- **Frozen soil is excluded** ⚠️ (our choice): readings where the 5 cm soil
  temperature is ≤ 0 °C are dropped. Frozen water changes both the probe's
  permittivity reading and C-band backscatter, so neither the target nor the radar
  feature is valid there.
- **Winter has fewer clear S2 scenes,** so the nearest clear optical observation is
  older in winter. That is where the temporal gate has the most to decide, and why
  winter must be in the study.
- **Station availability:** ITCSM_20 stopped 2016-12-31, ITCSM_06 stopped
  2017-08-29 and ITCSM_19 stopped 2017-09-20, so about **17 stations** are active for
  most of the period.

## Asynchrony: what the catalogues actually show

Counted from STAC metadata over the network bounding box (`count_acq.py`, no imagery):

| Year | S1 passes | S1 relative orbits | S2 dates | S2 tile cloud < 20% | < 50% |
|---|---|---|---|---|---|
| 2017 | 236 | 15, 37, 88, 139 | 105 | 19 | 40 |
| 2018 | 232 | 15, 37, 88, 139 | 143 | 40 | 73 |
| 2019 | 234 | 15, 37, 88, 139 | 143 | 34 | 64 |
| 2020 | 233 | 15, 37, 88, 139 | 145 | 41 | 62 |

- **Timing:** S1 passes at about **05:42–05:50 UTC** (descending; orbits 37, 139) and
  about **17:16–17:25 UTC** (ascending; 15, 88), so one every ~1.6 days. S2 passes at
  **10:40 / 10:50 UTC**. The two never coincide; the minimum offset is about 5 h.
- **If samples were anchored on clear S2 dates,** S1 would always be within 0–1 days,
  which is nearly synchronous and would leave the temporal gate with little to do.
- **So samples are anchored on S1 passes:** target time = S1 pass time, and the
  optical feature is the nearest clear S2 observation at or before it. Its age then
  ranges from hours to weeks in cloudy spells. That is the genuine asynchrony the
  temporal gate must judge, with `forbid_future=True` preventing a later S2 scene
  from leaking in.

## CRS and GSD: what the sample will contain

- **Mixed CRS, on both sensors.** S2 tiles 31UGT (EPSG:32631) and 32ULC
  (EPSG:32632) both cover the network. S1-RTC frames over the same area come in
  **both** EPSG:32631 and EPSG:32632, depending on frame. The geometry gate will see
  mixed CRS.
- **Mixed GSD:** S1 10 m; S2 10 m (B02–B04, B08) and 20 m (B11, B12, SCL).
- **Units:** S1-RTC is **γ⁰ in linear power** (float32, nodata −32768), not dB. Any
  dB conversion is ours, and the sensor-physics gate should see it declared.
- **Cloud:** S2 SCL is provided. Tile-level cloud cover is too coarse, so masking is
  per pixel, using SCL at the station pixel.

## Matching ground truth to pixels

1. **Probe:** the 5 cm SM column of `ITCSM_XX_cd.csv`. 5 cm is closest to the C-band
   sensing depth; deeper probes are not used as targets.
2. **Time:** the CSV is in **CET (UTC+1, no daylight saving)**, so subtract 1 h to get
   UTC. Target = mean of the 15-min readings within ±30 min of the S1 pass time
   (⚠️ the ±30 min choice is ours). Missing = −99.999.
3. **Quality:** keep a reading only if its SM flag for the 5 cm probe in
   `ITCSM_XX_fg.csv` is 0 (normal). Drop flags 1–7 and 9.
4. **Location:** `Twente_network_locations.ods` gives **dated** coordinates, because
   stations were relocated. Use the coordinate valid on the target date, and use
   WGS84 rather than RD_new. ITCSM_02's RD_new row duplicates ITCSM_01's, an apparent
   error in the sheet. ITCSM_06 has `??` for 2015-04-17 to 2016-05-20, so readings in
   that interval are dropped.
5. **Pixel:** transform the station coordinate into **each layer's own CRS** and take
   the pixel containing it. ⚠️ We may instead use the mean of a 3 × 3 window at 10 m
   to soften the point-to-pixel mismatch; this is to be decided in Step 4 and recorded
   either way.
6. **Optical validity:** the S2 value is used only where SCL at that pixel is 4
   (vegetation) or 5 (bare soil). Otherwise the next older S2 scene is tried, within
   the temporal gate's window.
7. **Decide at cube assembly, deliberately (not yet implemented): exclude water and
   unclassified pixels from the training samples.** A soil-moisture target over a
   pond or ditch is meaningless, however valid its reflectance.
   - **Rule:** mask SCL 6 (water) and 7 (unclassified) from the training pixels.
   - **Report the cost per station:** the fraction of chip pixels removed.
   - **Look for stations that are mostly water or built-up, before the ablation, not
     after.**
   - **Caveat:** SCL has no built-up class; roads and roofs fall under 5 ("not
     vegetated") with bare soil. Detecting a mostly built-up chip needs an independent
     land-cover layer, e.g. ESA WorldCover 10 m (open, CC-BY-4.0). That would be one
     more open download, to be approved separately.
   - **Per-pixel edge and fill masking at the station.** `fetch_year.py` tags every
     S2 chip with `station_measured`: false where the station pixel is fill, implausible
     (B02 < 50 DN over land) or within 2 pixels of the swath edge in every candidate
     tile. Cube assembly must drop those station-dates. The quality gate judges whole
     layers: a 0.7% fill strip passes its 1% tolerance even when the strip is the sample
     pixel (docs/examples/swath_edges.md). Report per station how many dates this
     removes. ITCSM_08 sits on one S2 orbit's swath edge and loses that orbit's dates.
   - **Not the same as the fetch-time rule.** At fetch time, water is exempt from the
     B02 ≥ 50 plausibility floor so that it is not mistaken for fill. That decides
     which *tile* to read, not which pixels to train on.

## Expected sample count

- **Upper bound:** S1-anchored samples = S1 passes × active stations × in-situ
  availability ≈ 935 × 17 × 0.85 ≈ **13,500** (⚠️ the 0.85 availability is a guess,
  to be measured from the flags).
- **After the S2 window:** requiring a clear S2 observation within the temporal
  window cuts this. With a 10-day window ⚠️ we expect roughly **6,000–10,000**. The
  exact figure is a Step 4 output, not a promise.
- **Independent spatial units: about 17 stations.** Spatial block CV will group
  stations into ~5 blocks of 3–4 nearby stations. This is the study's main
  statistical weakness: many samples, few independent places. Seed-to-seed variance
  will be reported, not hidden.

## Power check (before any larger fetch)

Data: flag-0, 5 cm calibrated readings at S1-like times (06:45, 18:15, 18:30 CET),
2017–2020, streamed from DANS for all 20 stations with nothing stored. **17 stations are
usable.** ITCSM_06 and ITCSM_19 stopped in 2017; ITCSM_20 has no 5 cm data in the period.

**Variance** (total sd 0.152 m³/m³):

| Component | Share |
|---|---|
| Between-station means (soil) | 37.3% |
| Station seasonal cycle | 32.1% |
| Weather anomalies | 30.5% (sd 0.084; 11× the sensor error variance) |

Station anomalies are correlated: median pairwise r = 0.60 (IQR 0.45–0.69).

**CV power, no-satellite proxy.** Each held-out station is predicted by the mean of the
training stations at the same time ("fresh"), or 5 days earlier ("stale"), standing in
for a temporal-gate failure. Spatial k-means blocks, 5 partitions each:

| Folds | Absolute target: paired ΔR² per fold | Anomaly target: paired ΔR² |
|---|---|---|
| k = 3 (3/5/9 stations) | 0.054 ± 0.017 (min 0.019) | 0.224 ± 0.026 |
| **k = 4 (4/4/4/5)** | **0.062 ± 0.030 (min 0.017)** | **0.235 ± 0.047** |
| k = 5 (2/3/3/4/5) | 0.065 ± 0.032; fold R² −0.63..0.63 | 0.243 ± 0.047 |
| LOSO (17) | 0.094 ± 0.070; stale wins in 2 folds | 0.287 ± 0.168 |

**Decisions this fixes in advance:**

1. **Folds:** 4 spatial blocks, repeated over 5 block partitions (the partition varies
   with the seed). k = 5 forces a 2-station block. LOSO is not spatial at r = 0.60
   between neighbours.
2. **Primary result:** paired per-fold ΔR² (with gate vs without, same folds). Absolute
   R² levels vary 0.13–0.63 by fold and are not interpretable alone.
3. **Two metrics:** absolute R², and anomaly R² (the target minus each station's own
   monthly climatology). Anomaly R² is an evaluation metric only, never a model input.
   It separates moisture tracking from soil type and season, which together are 69% of
   the variance.
4. **Minimum detectable effect:** about **ΔR² ≈ 0.05**. Fold-to-fold noise in the
   paired delta is ±0.03–0.05. A gate effect below that is reported as
   **inconclusive**, not as "no effect".

**Decision (2026-10-27): proceed with a detection limit of ΔR² ≈ 0.05.** The write-up
must:
- state the limit prominently;
- report any gate whose effect falls below it as **"below detection limit on this
  task"**, never as "no effect";
- say plainly that **the ablation measures downstream impact, not validity.** A gate
  can be protecting correctness (refusing a misregistered layer, a leaked future
  scene, a cloud-filled pixel) without moving R² on a smooth target.

**Caveat:** the proxy uses neighbouring in-situ readings, which is far more informative
than satellite features. Real gate effects will be smaller. The geometry gate is
⚠️ expected to fall below the detectable effect at field-scale stations; the quality
gate is ⚠️ expected to clear it.

Raam (15 stations, CC0) is **on hold.** If it is ever pooled, the catchment must be a
held-out fold, and that decision is taken on its own merits, not to rescue power.

## Block design: east/west Sentinel-2 coverage

**Decision (2026-10-28): stratified spatial blocks.** Each of the 4 folds contains
exactly one of the four low-coverage stations (ITCSM_01, 04, 07, 09). The other 13
stations form 4 spatial k-means blocks. This replaces plain spatial k=4 blocks.
Reproduce with `eastwest_blocks.py --manifest <2019 manifest.jsonl>`, run beside
`sm_stats.py`'s output.

**The problem.** One Sentinel-2 orbit's swath does not reach the east of the network.
In 2019, ITCSM_01, 04, 07 and 09 have S2 on 71–72 of 143 dates (ITCSM_08: 115). For
each S1 pass, the age of the most recent usable S2 observation at or before it:

| | Mean | Median | 90th pct | Within 2 days |
|---|---|---|---|---|
| East (4 stations) | **2.66 d** | 2.79 d | 4.79 d | ~40% |
| Other 13 | **1.41 d** | 1.27 d | 2.79 d | ~80% |
| Ratio | **1.88** | | | |

Plain spatial blocks put 3 of the 4 eastern stations in one fold, so mean S2 age
differs across folds by **0.78–1.28 d**. A temporal-gate effect in that fold would be
confounded with geography.

**The three designs** (5 block partitions; power proxy as in "Power check"):

| Design | Stations / folds | Age spread across folds | Nearest training station to a test station | Paired ΔR², absolute / anomaly | Absolute R² across partitions (sd) |
|---|---|---|---|---|---|
| Plain spatial k=4 | 17 / 4 | 0.78–1.28 d | median 11–15 km, min 6.3 km | 0.062 ± 0.030 / 0.235 ± 0.047 | 0.020 |
| Drop the four | 13 / 4 (2–6 per fold) | 0.12 d | median 10.6–13.3 km, min 8.3 km | 0.062 ± 0.027 / 0.223 ± 0.028 | 0.364 |
| **Stratify** | **17 / 4 (3–7 per fold)** | **0.13 d** | median 8.6–9.5 km, min 4.8 km | **0.061 ± 0.027 / 0.232 ± 0.034** | 0.233 |
| Report separately | 17 / 4, unchanged | 0.78–1.28 d, unchanged | as plain | as plain | 0.020 |

**Why stratify, not drop, although both reduce the confound almost equally**
(0.13 d vs 0.12 d):

- **What dropping removes.** The four stations dropped are those with the oldest
  optical data, the cases the temporal gate exists for. Dropping them would remove its
  strongest test, in a study of whether temporal alignment matters, and would confine
  every conclusion to the west of the network.
- **What stratifying costs.** Dropping keeps test stations further from training
  (minimum 8.3 km vs 4.8 km), so it is the better design for spatial independence.
  Stratifying accepts closer neighbours. In the proxy this did not inflate R² (absolute
  0.318 vs 0.364 plain; anomaly 0.468 vs 0.470), and the paired-ΔR² noise fell.
- **Neither design makes absolute R² levels interpretable.** They vary across partitions
  by 0.233 (stratify) and 0.364 (drop). That reinforces the earlier decision: the
  primary result is the paired per-fold ΔR², not R² levels.
- **Report separately** keeps the confound in the pooled result, so it is not a design.
  It becomes the secondary analysis below.

**Caveat (as of 2026-10-28): both the proxy and the S2 ages above ignore clouds.**
They count S2 acquisitions available at the station, not usable ones. **The figures
above are the prior estimate.** They are kept so the change is traceable. The decision
now rests on the cloud-aware re-check below.

### Re-checked with clouds applied (2026-10-29, the 2019 cube, job 247673)

**Ages.** S2 age is now the age of the newest S2 observation that is **usable at the
station pixel** (SCL 4/5, B02 ≥ 50, not edge-flagged) at or before each S1 pass, over
the cube's 3,505 samples (docs/cube_2019.md):

| | Prior (availability only) | **Cloud-aware** |
|---|---|---|
| East (4 stations) mean S2 age | 2.66 d | **9.53 d** (median 7.80) |
| Other 13 | 1.41 d | **6.78 d** (median 4.80) |
| Ratio | 1.88 | **1.41** |
| Absolute gap | 1.25 d | **2.75 d** |

**Cross-fold spread of mean S2 age** (same partitions as above):

| Design | Prior | **Cloud-aware** |
|---|---|---|
| Plain spatial k=4 | 0.78–1.28 d | **1.23–3.19 d** |
| Drop the four | 0.12 d | **1.30 d** |
| **Stratify** | 0.13 d | **0.53–1.08 d** |

**The decision holds, and its justification changes in two ways:**

1. **The ratio narrowed (1.88 → 1.41).** The eastern stations' relative disadvantage
   is smaller than the availability-only figures suggested, because clouds lengthen
   every station's wait. The absolute gap more than doubled (1.25 → 2.75 d). The
   absolute gap is what the temporal gate's window acts on, so the confound is larger
   in the unit that matters.
2. **Dropping no longer matches stratifying on the confound.** Without clouds, dropping
   the four left a 0.12 d spread; with clouds it leaves 1.30 d, because cloud
   frequency also varies among the western stations. **Stratifying gives the smallest
   spread, 0.53–1.08 d.** So it is chosen on the confound as well as on what dropping
   removes. The "nearly equal confound reduction" premise of 2026-10-28 does not
   survive the cloud-aware figures.

The power proxy above still ignores clouds and uses in-situ neighbours, not satellite
features. It is not recomputed here; its role was to set the detection limit, and that
is re-examined when the ablation runs.
*Re-examined 2026-10-29 (docs/open_ablation.md, job 247693): the real model has no skill
on held-out stations, and fold noise in paired ΔR² is 0.052. So ≈ 0.05 stands, as an
optimistic limit for this task.*

**Secondary analysis (planned):** paired ΔR² per station, reported for the four
eastern stations against the other 13. This is a **descriptive** breakdown, labelled
in the write-up as **resting on 4 stations**, with no claim of significance.

## Decisions for the 2019 ablation (2026-10-29)

Evidence: docs/cube_2019.md (cube job 247673, refusal inspection job 247675).

### Geometry mode: point

**Decision: the geometry gate runs in point mode for the 2019 study.** Each layer must
have been read at the same declared ground point (pixel centre within half a pixel
diagonal of it), wherever its grid lies.

**Why.** This cube is a value per sensor at a known coordinate. Every S1 and S2
feature is read at the station's own pixel, in each layer's own CRS; nothing is
stacked or resampled onto a shared grid. Grid agreement is the wrong question for it.
Grid mode asks whether the layers share one grid, and they do not need to. It refused
**979 samples (27.9%)**, every sample whose S1 frame and S2 tile fall in different UTM
zones, although the sampled pixel centres agree to 7.1 m at the median and 14.1 m at
most. Point mode asks the question that can actually go wrong here: whether every
sensor was read at the same place. It accepts all 3,505.

Grid mode stays the library default. Point mode is the method change, and it is
recorded with the cube. The ablation reports grid mode as a supplementary arm, to show
what the design not chosen would have cost.

**Flagged, not changed: the resolution gate makes the same image-stack assumption.**
With no target GSD it defaults to the coarsest input (20 m) and records every 10 m
layer as downsampled: 3,505 PASS_WITH_DECISION, 21,030 layer decisions. In this cube no
resampling happens; each value is read at its native pixel. So the decision describes a
grid cube that does not exist. It refuses nothing, so it does not change the samples,
but its provenance record is wrong for a point cube. It may need a matching point
treatment. That is a change to the method and is left for its own decision.

### Quality bounds: kept exactly as declared

**Decision: the B02 ≥ 50 floor and the 12,000 ceiling on the other S2 bands stay as
declared for the ablation.** Both are known to be guesses that do not hold: the floor
refuses dark winter vegetation, the ceiling refuses bright cloud (docs/cube_2019.md).

**Why they are not changed:**
- **The write-up should report what guessed bounds actually cost.** The ablation runs
  on the bounds as they are, so the quality gate's refusals are measured, not
  pre-empted.
- **Changing a threshold because it refused data we wanted is the move CLAUDE.md
  forbids** ("Do not weaken a gate to make data pass"). The S1 upper bound was
  different: it was physically wrong (it flagged real strong reflectors), and was
  replaced by the declared physics, not by a looser number.
- **The floor also selects samples.** B02 ≥ 50 is used in fetch-time tile scoring and in
  the per-pixel usability rule, as well as in the gate. Changing it would change which
  S2 dates are usable, and so the sample set. The ablation would then no longer run on
  the cube documented in docs/cube_2019.md.

### Station pixel or chip for quality

*Resolved 2026-10-30: the station pixel, in point mode (commit 00b166c). Before and
after, with the bypass evidence, in docs/open_ablation.md. The record of the question
as it was posed follows.*

**Open, not implemented: should the quality gate judge the 1 km chip, the station
pixel, or both?** This is the next decision for this cube.

**Evidence: the 110 quality refusals** (job 247675). They come from 24 S2 observations,
mostly winter dates:

| Trigger | Samples |
|---|---|
| B02 below 50 DN on > 1% of the chip | 102 |
| an S2 band above 12,000 on > 1% of the chip | 5 (overlapping) |
| a chip > 30% nodata (S1 frame edges) | 6 |

**In all 110 the station pixel itself passed the per-pixel rules.** None is fill
masquerading as data. The refusals are correct statements about other pixels in the
chip, and irrelevant to the one pixel the sample uses. Conversely, a layer-level gate
can pass a chip whose fill strip is the sample pixel (docs/examples/swath_edges.md).
That case is now caught by the per-pixel rules at assembly, not by the gate.

**Not implemented yet.** It is a change to the method, so it needs its own
before-and-after: the same cube, gated both ways, with refusal counts and the ablation
re-run. The 2019 ablation uses the chip-level gate as it stands.

## Window size (open question 1): design, fixed before any result (2026-10-30)

An extension of the 2019 study, reported separately in docs/window_size.md. **The 1 × 1
point-mode ablation remains the main result.** No new data: the fetched 1 km chips are
100 × 100 px at 10 m.

**Why.** Two effects compete as the window grows:
- **Speckle:** SAR retrievals at high resolution are limited by speckle, and averaging
  (multilooking) reduces it. Published accuracy improves over the first few
  aggregations (20 → 40 → 60 m) and then stagnates.
- **Spatial representativeness:** the probe measures a few centimetres of soil, and a
  larger window averages ground the station does not represent.

**Windows:** 1 × 1, 3 × 3, 5 × 5, 9 × 9 pixels (10, 30, 50, 90 m).
- Each is centred on the station pixel, on each sensor's own grid: S1 on its 10 m grid;
  S2 on the 10 m grid of B02.
- S2's 20 m bands (B11, B12) and SCL are read at the centres of the 10 m cells, so each
  20 m pixel is weighted by its overlap with the window.
- S1 is averaged in **linear power**. Averaging dB would be the arithmetic the
  sensor-physics gate refuses.
- Same samples, same S2 date per sample, same stratified folds (5 partitions), same
  random forest. Only the extraction changes.

**Validity rule: the centre pixel must be valid; the window mean uses its valid pixels
only.** Valid means the cube's per-pixel rules:
- **S2:** SCL 4 or 5, and B02 ≥ 50.
- **S1:** VV and VH finite, positive and not nodata.
- **Outside the chip:** counts as invalid.

**Why this rule:**
- **It keeps the sample set identical at every size.** The centre is exactly the 1 × 1
  sample, so any change in R² is the window's doing, not a change in which samples are
  scored.
- **"All pixels valid" is the strictest alternative, but it changes the sample set
  with the window size.** It confounds the two.
- **"A minimum valid fraction" needs a threshold with no basis.** 50% is reported as an
  illustration only.

**The cost of every rule is reported per size.** As a second, equally clean comparison,
all sizes are also scored on the subset that is **fully valid at 9 × 9** (identical
samples at every size).

**Two aggregation orders:**
- **Average-then-calculate (AtC):** mean the pixel values over the window, then predict
  with a model trained on window means.
- **Calculate-then-average (CtA):** the pixel-scale model (trained on 1 × 1 samples)
  predicts every valid pixel in the test window, and the predictions are averaged.
  - Pixels are paired across sensors by their offset from each sensor's station pixel;
    the two grids differ by up to ~7 m (point-mode geometry).
  - The same test samples as AtC.

**Separating speckle from representativeness.** Two extra AtC arms:
- **Window S1 only** (S2 at the station pixel): S1 is where speckle lives.
- **Window S2 only** (S1 at the station pixel): S2 at 10 m has no speckle, so any
  change there is smoothing or representativeness.

Speckle predicts a gain from S1-only windowing that is similar across stations.
Representativeness predicts losses that grow with the heterogeneity around a station.
That is measured as the within-window spread of NDVI, and compared with per-station
changes. **Descriptive, 17 stations, no significance claimed.**

**Reported per size:**
- r between S1 VV (dB of the window's linear mean) and soil moisture, per station;
- R² absolute and anomaly, and the paired ΔR² against 1 × 1;
- samples retained under each validity rule;
- per-station results.

**The gate connection.** The quality gate's own chip criteria are applied to each window:
- refuse if more than 1% of non-nodata S2 pixels are outside the declared ranges;
- refuse if more than 30% of S1 pixels are nodata.

These window verdicts are compared with the chip's 110 refusals at each size. This
answers at what window size chip-level validity stops being the wrong question for a
sample.

## Download size and placement

**Where data lives.** On the laptop, `data/` in the repo is a directory junction to
**`D:\eocube_data`** (48 GB free; C: had ~2 GB). Scripts use the relative path
`data/open_cube`, or `$EOCUBE_DATA/open_cube` if that variable is set. The junction
sits where `.gitignore:10 data/` already applies, and `git check-ignore` confirms it.
The Step 3 sample (55 files, 286 MB including the earlier Vilnius tiles) was moved
there with checksums verified. On the cluster, once reachable, the agreed layout
applies: `~/work/data/raw` (untouched downloads), `~/work/data/cubes`,
`~/work/results`.

**Full year, all 17 stations, 1 × 1 km chips** (2019; measured by
`size_estimate.py` from the real compressed size of every COG block the chips
touch, times the year's STAC counts):

| Item | Per acquisition | Count (2019) | Transferred | Stored |
|---|---|---|---|---|
| S1-RTC VV+VH | 39.2 MB (46 × 512-px blocks) | 234 passes | **9.2 GB** | 286 MB |
| S2-L2A, 6 bands + SCL | 26.1 MB (93 blocks) | 143 dates | **3.7 GB** | 172 MB |
| In-situ `_cd` + `_fg`, 20 stations | — | once; covers 2008–2020 | 1.66 GB | 1.66 GB |
| **Total, one year** | | | **≈ 14.6 GB** | **≈ 2.1 GB** |
| **Total, 2017–2020** | | | **≈ 53 GB** ⚠️ | **≈ 3.5 GB** ⚠️ |

- **Transfer ≫ storage:** each 1 km chip pulls whole 512 × 512 internal tiles (5.1 km
  at 10 m), so most bytes read are discarded.
- **S2 counts one tile per date (32ULC, which covers every station).** Fetching the
  31UGT duplicate as well, to exercise mixed CRS on S2 itself, would roughly double
  the S2 figure.
- **S1 is measured on one frame;** some passes need two frames to reach every
  station, so treat ±25% as the uncertainty ⚠️.
- **The 4-year figures** scale 2019 by the per-year counts in the asynchrony table;
  2017 has fewer S2 dates.
- **Disk is no longer the constraint;** network time is. At an assumed 50 Mbit/s
  ⚠️, one year is ~40 min and four years ~2.5 h.

**Every option here is over 1 GB, so the full fetch will not start without your
approval.**

## Minimal sample (Step 3)

- **Stations:** ITCSM_08 (52.13583, 6.74500) and ITCSM_09 (52.14639, 6.84306). Neither
  was relocated, so there's no coordinate ambiguity.
- **Window:** 6.72–6.87 °E, 52.125–52.160 °N (about 10 × 4 km).
- **Period:** 2019-06-24 to 2019-07-03.
- **S2:** 2019-06-27 (tile cloud 0.3%) and 2019-07-02 (~25%, to exercise SCL masking),
  on both 31UGT and 32ULC.
- **S1:** four passes around 2019-06-27 10:40 UTC, chosen to exercise every case:
  2019-06-26 05:49 (rel 37, before), 2019-06-27 05:42 (rel 139, 5 h before),
  2019-06-29 17:24 (rel 88, after, in EPSG:32631) and 2019-06-30 17:17 (rel 15,
  after). The last two are **future** relative to an S2-time target, so they must be
  refused as leakage.

### Step 3 result (2026-10-27)

Fetched with `python experiments/open_cube/fetch_sample.py --out data/open_cube`, which
also writes `data/open_cube/manifest.json`.

- **Size:** 191 MB in total, of which 166 MB is the four station CSVs; the 36 image
  windows are ~25 MB.
- **S1:** 8 files. 1039 × 421 px in EPSG:32632, except the orbit-88 pass, which is
  1047 × 444 px in **EPSG:32631**.
- **S2:** 28 files. 10 m bands are 1047 × 444 (31UGT) or 1039 × 421 (32ULC); 20 m bands
  and SCL are about half that.
- **Sanity check at the two station pixels:**
  - **S1 γ⁰ VV:** 0.026–0.095 (linear).
  - **S2 2019-06-27:** SCL 4 (vegetation) at both stations; B04 agrees across the two
    tiles (376 vs 374).
  - **S2 2019-07-02:** SCL 9 (cloud) at ITCSM_08 and SCL 3 (cloud shadow) at ITCSM_09,
    so both are invalid and the masking path has a real case.
  - **In-situ 5 cm SM:** 0.052–0.058 (ITCSM_08) and 0.080–0.083 (ITCSM_09) m³/m³. This
    is a dry spell with almost no variation. Backscatter varied by a factor of ~3 over
    the same days, mostly from orbit geometry and speckle, not moisture. Expect
    low-variance periods to carry little signal.
- **S2 processing baseline:** all scenes up to 2020 predate baseline 04.00 (Jan 2022), so
  no +1000 DN offset applies within the study period.

**The Step 3 sample covers summer only.** It proves the fetch-and-match pipeline,
not the target's dynamic range. Any extension of the sample (not yet fetched) must add
autumn and winter windows (e.g. 2019-11 and 2020-02) before Step 4 draws conclusions.

## Open questions

1. **Point-to-pixel scale:** pixel vs 3 × 3 window (see matching step 5).
   Being tested: "Window size (open question 1)" above; docs/window_size.md.
2. **The temporal window:** is 10 days sensible for soil moisture after rain? It is
   arguably too long; this will be judged with data in Step 4.
3. **dB:** the sensor-physics gate refuses additive arithmetic on dB. Whether any
   feature uses dB is a modelling choice, and will be declared, not hidden.
4. ~~Quality at the station pixel or the chip~~ Resolved 2026-10-30: the station pixel,
   in point mode; chip-level findings are recorded as decisions
   (docs/open_ablation.md).
5. **The resolution gate's stack assumption** in a point cube (flagged, same section).
