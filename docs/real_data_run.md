# First real-data run: Sentinel-2 (Vilnius tile)

Date: 2026-10-25. eocube at `075ee9f` (v0.4.0) plus the fixes described below.

Until this run, `eocube.io.describe()` and the five gates had only seen synthetic
fixtures. This document records what happened when they met real files.

## The dataset

> Kömürcü, K. & Petkevicius, L. *Global Spatiotemporal Sentinel-2 Dataset with
> Pre-computed GEE Embeddings with Captions (2017–2024).* Vilnius University.
> Zenodo [doi:10.5281/zenodo.19447415](https://doi.org/10.5281/zenodo.19447415), CC-BY-4.0.

The Zenodo record has no description. Its contents were worked out by reading
the zip's central directory over HTTP range requests (0.3 MB transferred), not
by downloading the 37.7 GB archive.

| File | Size | Contents |
|---|---|---|
| `downloads.zip` | 37.7 GB | 1455 GeoTIFFs: 93 regions × up to 8 years (2017–2024) × 2 products |
| `captions.zip` | 32 MB | 29,029 JSON captions, one per 128-px patch per year, plus `captions.json` |
| `s2_future_jepa-best.pth.tar` | 2.1 GB | Model checkpoint (not used here) |
| `s2_caption_guided-best.pth.tar` | 4.2 GB | Model checkpoint (not used here) |

Per region and year there are two files:

| Product | Count | Typical size | Bands | dtype | Values |
|---|---|---|---|---|---|
| `s2_rgb_<region>_<year>_10km.tif` | 712 | 4.1 MB | 3, unnamed | float32 | ~0.01–0.7, reflectance scaled to 0–1 |
| `embedding_<region>_<year>_10km.tif` | 743 | 55 MB | 64, unnamed | float64 | −0.48…0.44, unit-length vectors |

Captions are LLM-generated text (`informative`, `geometric`, `semantic`) about
each patch. They carry the authors' original file paths but no acquisition
dates, cloud cover or processing level. There is **no regression or
segmentation target** in the dataset.

### What the files say, and what they do not

- **Format:** GeoTIFF, deflate-compressed, tiled, pixel-interleaved.
- **CRS and transform survive.** All files are EPSG:4326 with a pixel size of
  8.983e-5° (10 m at the equator). For Vilnius the RGB and embedding files for
  every year sit on the identical 1323 × 770 grid.
- **The CRS is geographic, not UTM.** At 54.7° N a pixel is about **5.8 m east–west
  by 10 m north–south** on the ground: square in degrees, not on the ground.
- **No nodata value is set in any file.**
- **Band names are absent.** The RGB band order is inferred from the filename only.
- **One file per year:** these are annual composites. Acquisition dates, the
  compositing method and the scene count are not recorded anywhere.
- **L1C or L2A: not stated.** The evidence points to surface reflectance
  (L2A-like). The darkest pixels reach about 0.01 in every band, and band 3
  (presumably blue) is the darkest band in every year. Top-of-atmosphere blue
  rarely falls that low, because of atmospheric path radiance. **This is an
  inference, not a confirmed fact**; it needs confirming with the authors.
- **The embeddings are most likely Google's Satellite Embedding (AlphaEarth)
  annual product.** That is suggested by the 64 bands, unit-length vectors,
  coarsely quantised values (118 distinct values in band 1), and "GEE
  Embeddings" in the title. This is also an inference.

## Sample used

Vilnius_Lithuania only: the eight RGB files (2017–2024) and the 2024 embedding,
80 MB in all, extracted one at a time from the remote zip. The data is in
`data/s2_sample/` and is gitignored (`.gitignore:10: data/`). The full archive
could not have been used regardless: drive C: had **1.8 GB free**.

## What `describe()` read

| Field | Result |
|---|---|
| crs, transform, width, height, band_count, dtype | Read correctly on every file |
| gsd | Read, but **in degrees, reported as if metres** (bug 1) |
| nodata | `None` on every file, correctly (the files set none) |
| invalid_fraction | **0.0 on the embedding, which is 40.4% empty** (bug 2) |
| value_range | Listed as observable, **never computed** (bug 4) |
| acquisition_time, processing_level, reflectance, units, qa_mask | Not derivable from these files; left undeclared rather than guessed |

Nothing errored. float64 and 64-band files were read without trouble; a
regression test now covers that shape anyway.

## Gate report

The embedding was declared `modality: "embedding"`, and the RGB files
`modality: "multispectral"`, `sensor: "Sentinel-2"`. Reflectance was left undeclared
because the dataset does not state it.

### Before the fixes

| Gate | RGB 2017–2024 stack | RGB 2024 + embedding 2024 |
|---|---|---|
| geometry_crs | PASS | PASS |
| resolution | PASS_WITH_DECISION, "8.98e-05 **m**" | same |
| temporal_alignment | **absent from the report** | **absent** |
| quality_missingness | PASS | **PASS, 0% invalid** |
| sensor_physics | PASS_WITH_DECISION (reflectance undeclared) | same; embedding passed unremarked |
| **Cube** | ACCEPTED | **ACCEPTED** |

### After the fixes

| Gate | RGB 2017–2024 stack | RGB 2024 + embedding 2024 |
|---|---|---|
| geometry_crs | PASS | PASS |
| resolution | PASS_WITH_DECISION: GSD in degrees, not square on the ground | same |
| temporal_alignment | **NOT_EVALUATED**: no acquisition times | **NOT_EVALUATED** |
| quality_missingness | PASS_WITH_DECISION: 0.2% zero pixels in 2017 | **REFUSE: 40.4% of pixels zero in all 64 bands** |
| sensor_physics | PASS_WITH_DECISION: reflectance undeclared | PASS_WITH_DECISION: embedding not checked |
| **Cube** | ACCEPTED (1 not evaluated) | **REFUSED** (1 not evaluated) |

With `declared={"nodata": 0}` on the embedding, the quality gate still refuses,
now on measured invalidity (40.8%). The 0.4-point difference is pixels that are zero
in some bands but not all, most likely genuine quantised zeros. For this file the
all-band figure (40.4%) is the more accurate one.

### Reading the gates

- **geometry_crs passes, and that is correct.** Every layer is on one grid with
  zero sub-pixel offset. The gate does not judge whether a geographic CRS is
  appropriate; the resolution gate now flags it.
- **resolution has something to say**, but only after the fix. Before, it would
  have compared degrees against metres without noticing.
- **quality_missingness had the most to say, and was blind to it.** The 2024
  embedding has no data for the whole top 40% of the tile, in solid rows.
  The RGB image covers that area normally. Because the fill is zero vectors
  with no nodata tag, it was measured as perfectly clean. It is now refused.
  **This is a finding about the data:** the embedding coverage gap is real and
  would silently corrupt any model trained on these pixels.
- **temporal_alignment is idle, as expected, but for a stronger reason than
  "single sensor".** These are annual composites with no acquisition dates at
  all, so there is nothing for the gate to inspect. It now says so instead of
  disappearing from the report.
- **sensor_physics is partly idle.** It has no rules for a learned embedding,
  and with a single optical sensor there is no cross-modality arithmetic to
  catch. Its one live finding is that RGB reflectance is not declared, which
  is true: the dataset does not say.

## Bugs found and fixed

Each fix has regression tests built on small synthetic files, not the real data:
21 new tests (101 in total). 16 of them fail against the pre-fix code. The
other 5 guard behaviour that was already correct.

1. **GSD units ignored.** `describe()` reported a geographic pixel size in degrees
   as `gsd`, and the resolution gate labelled and compared it as metres. Now
   `describe()` records `gsd_units` and, for geographic CRS, `gsd_ground_m`, plus a
   provenance conflict when ground pixels are not square. The resolution gate
   refuses mixed units, and refuses a metre `target_gsd` on a degree grid.
   Tests: `test_geographic_gsd_is_labelled_degrees_with_ground_size`,
   `test_projected_gsd_is_in_metres`, `test_mixed_gsd_units_refuses`,
   `test_metre_target_on_degree_grid_refuses`, `test_degree_gsd_not_labelled_as_metres`,
   `test_layers_without_units_still_treated_as_metres`.
2. **Zero fill without a nodata tag was invisible.** `describe()` now records
   `zero_fill_fraction` (pixels zero in every band) when no nodata is set. The
   quality gate refuses a multi-band layer whose zero fill exceeds
   `max_invalid_fraction` (unless `gap_fill` is declared), and records it as a
   decision for single-band layers, where zero is often a real value. A declared
   `nodata` is now honoured when the file has none; previously it was rejected as a
   "conflict" with the file's `None`. Tests: `test_zero_fill_without_nodata_is_reported`,
   `test_zero_in_some_bands_is_not_zero_fill`,
   `test_zero_fill_fraction_absent_when_file_declares_nodata`,
   `test_declared_nodata_used_when_file_has_none`,
   `test_declared_nodata_contradicting_file_still_conflicts`,
   `test_multiband_zero_fill_above_threshold_refuses`,
   `test_multiband_zero_fill_with_gapfill_is_a_decision`,
   `test_single_band_zeros_are_logged_not_refused`,
   `test_real_shaped_tile_is_refused_by_quality_gate`.
3. **Idle gates were silent.** `run_all_gates` dropped the temporal gate from the
   report when no acquisitions were given, which looked the same as skipping it
   on purpose. There is a new verdict, `Verdict.NOT_EVALUATED`, which is not a
   refusal (`report.ok` is unaffected), and `ContractReport.not_evaluated`. The
   sensor-physics gate now names layers it has no rules for, and is
   `NOT_EVALUATED` when none are checkable. Tests:
   `test_temporal_gate_without_times_is_reported_not_omitted`,
   `test_skipped_temporal_gate_is_absent`, `test_unknown_modality_is_named_not_passed`,
   `test_no_checkable_layer_is_not_evaluated`.
4. **`value_range` was declared observable but never observed.** It is now computed
   over valid pixels during the stats pass. Test: `test_value_range_excludes_nodata`.

Not a bug: 64-band float64 files read fine (`test_float64_multiband_reads` guards it).

Descriptor version is now `eocube-cube-descriptor/0.4`. Two existing tests had
relied on the temporal gate being silently absent; they now skip it explicitly,
and their assertions are unchanged. No gate threshold was changed.

## Is this dataset suitable for a gate-ablation study?

**No, not as it stands.** An ablation measures what happens to a downstream
metric when each gate is removed. This dataset gives the gates almost nothing
to vary:

- **No target.** There is nothing to regress or segment, so there is no metric to
  ablate against. The captions are text.
- **One sensor, one grid, already harmonised.** Geometry and resolution pass
  trivially; removing them changes nothing.
- **No acquisition dates.** The temporal gate, the one most relevant to the
  peatland case, cannot run.
- **No QA masks** and no stated processing level.
- The one gate with real work to do, quality, refuses the embeddings outright.

It suits its intended purpose (self-supervised forecasting and caption-guided
generation, per the authors' Sat-JEPA-Diff papers), and it is a useful **stress
test for `describe()`**, as this run showed.

**What would suit an ablation:**

- **The chlorophyll-α dataset** (`experiments/configs/chlorophyll.yaml`):
  per-scene Sentinel-2 L2A with SCL quality masks and dated in-situ targets.
  The temporal, quality and sensor-physics gates all have real work there.
- **The peatland dataset** (Sentinel-1 + TerraSAR-X + EnMAP + meteorology):
  asynchronous, multi-sensor and multi-resolution, so every gate fires. It is
  licensed, so it must run on the cluster.
- **To reuse this dataset**, pair its tiles with an independent target and a
  second sensor: for example, Sentinel-1 over the same tiles and years, plus a
  land-cover or biomass raster as the target. You would also need per-scene
  imagery rather than annual composites if the temporal gate is to be tested.

## Open questions for the dataset authors

1. Processing level of `s2_rgb`: L2A (`S2_SR_HARMONIZED`) or L1C?
2. Band order (assumed B4, B3, B2) and the compositing method (median? cloud-masked?).
3. Is the embedding Google Satellite Embedding V1, and is the zero-vector region a
   known coverage gap or a masking step?
