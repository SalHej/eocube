# experiments

Config-driven ablation studies. `run_ablation.py` takes one config and runs the
leave-one-gate-out ablation it describes. The configs say which layers form the cube,
what is declared about each one, which thresholds the gates use, the model, the seeds
and the split. `configs/example.yaml` runs on synthetic data and documents every key.

The three dataset configs below are written **before the data has arrived**. Every
path, date and unverified property is a placeholder, and the placeholders are chosen
so that a run fails loudly rather than going ahead on a guess.

| Config | Task | Data | Where it runs | Runnable now? |
|---|---|---|---|---|
| `configs/peatland.yaml` | Soil-moisture regression | S1 C-band SAR, TerraSAR-X X-band SAR (**licensed**), EnMAP hyperspectral (**licensed**), meteorology, in-situ soil moisture | VU HPC only | Once paths, dates and declarations are filled in |
| `configs/chlorophyll.yaml` | Chlorophyll-a regression | Sentinel-2, in-situ chlorophyll-a (all open) | VU HPC (rasters never come to the laptop) | Once paths, dates and declarations are filled in |
| `configs/solar_panel.yaml` | Panel **segmentation** | Aerial/satellite RGB at 0.8–0.1 m, binary panel masks | VU HPC | **No.** Needs the pipeline changes described below |

## Published baselines

| Study | Published | Metric | Notes |
|---|---|---|---|
| Peatland | 0.73 (radar + weather), 0.81 (+ EnMAP) | R² | Primary source and its split method still to be recorded. The preprint doi in CLAUDE.md (10.31223/X5TN59) is not the paper these come from. |
| Chlorophyll-a | **not available** | — | None was given with the dataset. Record one before claiming an improvement. |
| Solar panel | 83.32% with 60% synthetic augmentation (ratio chosen by manual sensitivity analysis) | IoU | Source doi to be recorded. |

How to compare a result with these:

- **Split.** Every number from this runner comes from a spatial block split. If the
  published figure used a random split, theirs is optimistic and a lower spatial-split
  score is not a regression. Find out which split they used before comparing.
- **Ablation shape.** The peatland pair 0.73/0.81 is an ablation over *layers*, not
  gates. To reproduce it, copy `peatland.yaml`, delete the `ENMAP_*` layers and run
  both. Compare the two baseline arms.
- **Seeds.** Report mean ± stdev over at least three seeds (`scripts/train.sbatch`
  uses five). One seed is not a result.
- **Refusals.** Report the gates' verdicts alongside the metric. A refused cube is a
  finding, including when the gate turns out to be too strict.
- **Chlorophyll-a scale.** R² on log10(chl-a) and R² on chl-a are different numbers.
  State which one you report.

## What each config needs before it runs

Search a config for `PLACEHOLDER` and `UNVERIFIED`. Each one must be replaced.

**Paths.** `data_root` is the cluster project directory. Write every layer, target and
variant path *relative* to it, e.g. `peatland/s1/s1_vv_20250712.tif` once that file
exists. Do not use absolute paths, and do not use `..`. This matters most for peatland:
`tests/test_experiment_configs.py` fails if any of its paths escapes `data_root`. The
runner does not check this itself, because it uses an absolute path exactly as given.

**Dates.** `target_time` is the in-situ measurement time. Each layer's `acquisitions`
lists every scene used for that layer. The runner takes **one target time per study**.
A campaign with several measurement dates therefore needs one config and one target
raster per date, or a runner change.

**Declared properties are claims you vouch for.** The gates check declarations; they
cannot inspect a file to see whether it was calibrated. The placeholders are set to
the values that refuse:

- SAR (`S1_*`, `TSX_*`): `units: UNVERIFIED`, `calibrated: false`,
  `terrain_corrected: false`. Read the processing log or graph, then set `units` to
  what the *file* holds (`linear` or `db`). Set `calibrated: true` only for sigma0 or
  gamma0 backscatter. Set `terrain_corrected: true` only once the incidence angle has
  been corrected radiometrically; for TerraSAR-X EEC, the DEM geocoding on its own
  does not count. The config comments give the processing steps for each sensor.
- Optical: `reflectance: false` until it is confirmed as surface reflectance (for
  lakes, from a water-suited atmospheric correction). `qa_mask_applied: false` where a
  mask exists (EnMAP L2A, S2 SCL) until cloud and shadow pixels are set to nodata.
- EnMAP: each band you use is its own layer entry with a `band:` index. The runner
  refuses a multi-band file with no band named.

**Grid.** The target and every baseline layer must share one CRS, and their extents
must agree to within one target pixel. Reproject and crop upstream. The pre-flight
refuses otherwise, because `extract_samples` pairs pixels by array index.

**Variants.** Every config ships with `variants: {}`. With no variants, each ablation
arm trains on the baseline files and its delta is zero by construction (the runner
warns about this). A variant is the cube you would actually have built without that
gate, for example the S2 scene with the cloud mask not applied. Add variants once
those files exist.

## How to run

Everything below runs on the cluster. Do not run studies on the laptop: the rasters
are not there and must not be copied there.

```bash
# 1. on the laptop: edit the config, run the tests, sync
python -m pytest -q
labdash sync -m "Fill in chlorophyll config paths"

# 2. on the cluster, a dry run first: pre-flight and every gate, no training.
#    It reads every band into memory, so use an interactive Slurm session,
#    not the login node.
python experiments/run_ablation.py --config experiments/configs/chlorophyll.yaml --dry-run

# 3. the real run, via Slurm (train.sbatch defaults to peatland.yaml)
labdash submit --env CONFIG=chlorophyll.yaml
labdash fetch                              # brings back results/*.json, never imagery
```

**Cost.** All three configs use the random-forest baseline, which is CPU only.
`train.sbatch` asks for 8 CPUs for up to 4 h, so one job is at most 32 CPU-hours of the
6,000 allocation, and in practice less. A job uses GPU-hours only if the model becomes
`torch` and the GPU lines in `train.sbatch` are uncommented. In that case, state the
GPU-hour cost before submitting. The ablation runs 6 arms × 5 seeds = 30 fits per job
(5 arms for solar panel, once it runs).

Read `warnings`, `contract_reports` and `not_reportable_because` in the output before
`rows`. A result marked not reportable stays out of the write-up.

## Why the thresholds differ

| Gate key | Peatland | Chlorophyll-a | Solar panel | Basis |
|---|---|---|---|---|
| `window_days` | 7 | 1 | — (gate off) | Peatland: dense S1 asc/desc revisit, slow peat moisture dynamics. Chl-a: blooms change within days. Solar: single date. **All guesses**; reasoning in the configs. |
| `forbid_future` | true | true | — | Peatland: post-measurement rain or radar is leakage. Chl-a: kept true conservatively, even though a symmetric match-up window is standard for retrieval. |
| `decay_alpha` | null | null | — | The peatland formulation uses exp(−α\|dt\|), but its α is not known here. It was left unset rather than invented. |
| `max_invalid_fraction` | 0.30 | 0.30 | 0.10 | Default for the two satellite studies. Tighter for orthophotos, which should be complete (**guess**). |
| `gap_fill` | null | null | null | Peatland: off by design (see the config). Chl-a: filling reflectance over water fabricates the predicted signal. Solar: filling under a mask invents panels. |
| `target_gsd` | null | null | null | Coarsest input, nothing upsampled. **Open for peatland**: km-scale meteorology would set the grid. |
| `band_expressions` | [] | `S2_B05 / S2_B04` | [] | Only arithmetic you actually perform belongs here. |

The temporal window keeps and flags a late scene; it never drops one. Tightening the
window therefore costs nothing except visibility. `forbid_future` is the setting that
refuses.

## Solar panel: what the pipeline needs before segmentation

Nothing below is implemented. This is the list of changes needed before
`solar_panel.yaml` can produce a number comparable to 83.32% IoU.

1. **Metric.** Add an IoU metric for the positive class, `|P∩T| / |P∪T|` on binarised
   predictions, and allow `metric: iou` in `validate()`. Two choices must be made and
   recorded:
   - micro IoU, pooled over all test pixels, versus mean per-tile IoU. These can
     differ a lot when tiles vary in panel cover. Use whichever the published 83.32%
     used.
   - the threshold for turning a score into a mask (0.5, or tuned on a validation
     fold, never on the test fold).

   Report F1/Dice and precision/recall as well. IoU alone hides whether a model
   over-segments or under-segments.
2. **Target handling.** The target is a {0, 1} mask, not a continuous field.
   `extract_samples` already drops nodata (unlabelled) pixels, and that should stay.
   What is missing:
   - a check that the target holds only {0, 1, nodata};
   - a classifier, not a regressor. `baselines` has only regressors, and thresholding
     `rf` regression output is a stopgap at best;
   - handling for class imbalance, since panels are a small fraction of pixels.
3. **Sample extractor.** Per-pixel samples from three RGB values discard the spatial
   context that segmentation depends on. The published result is almost certainly a
   convolutional model. A segmentation path would need:
   - patch extraction (image chip + mask chip) instead of per-pixel rows;
   - many tiles per study. The runner reads one raster per layer, so a tile
     collection needs either a mosaic/VRT or a list-of-tiles input;
   - a `fit_predict` that takes chips and returns masks.
4. **Split.** Split by tile, or by geographic cluster of tiles (same rooftop, same solar
   farm), never by pixel. `spatial_block_split` works on pixel coordinates within one
   raster and would have to work on tile coordinates instead.
5. **Synthetic augmentation.** The 60% synthetic share must be:
   - a training-only transform, never in validation or test;
   - recorded in the output as a decision, the same way the gates record theirs;
   - tracked per sample, with a test-time assertion that no synthetic sample reached
     the test set.

   The existing `exclude_synthetic_from_test` flag covers *gap-filled* pixels only.
   It does not cover this. The ratio was tuned by hand, so treat it as a
   hyperparameter selected on validation data. Choosing it on the test set would
   leak.
6. **Label registration.** The target is not a cube layer, so the geometry gate never
   checks it against the imagery. The pre-flight only checks extent to within one
   pixel. At 0.1 m GSD, either the mask should go through `check_geometry_crs` as a
   layer, or the target should be held to the same `max_grid_offset_px`.
7. **Temporal gate.** It stays off while each label was drawn on its own image. If
   labels come from a different date (e.g. a registry of installations), the dates
   matter again: a panel installed after the image was taken is a false negative in
   the label.
8. **GPU cost.** A segmentation CNN is a GPU job. Estimate GPU-hours per seed, then
   multiply by 5 arms × seeds × GSD subsets before submitting anything. The 1,000
   GPU-hour allocation is shared.

## Known gaps in the runner that these configs expose

- One `target_time` per study (affects peatland and chlorophyll campaigns).
- No path allow-list: only the peatland test guards against paths outside the project
  directory.
- `invalid_fraction` is scene-wide. For sparse in-situ targets (both regression
  studies), what matters is whether the pixels *at the sample points* are valid. A
  mostly cloudy scene can be refused even though every match-up pixel is clear, and
  the reverse can also happen.
- The resolution gate records a target GSD, but `extract_samples` resamples by
  nearest-neighbour onto the target raster's shape whatever the gate decided. Make
  the target raster match the GSD the gate reports.
- `band_expressions` are checked, not computed. A ratio feature has to be written as
  its own layer.
- No per-lake or per-tile grouping in the split.
