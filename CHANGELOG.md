# Changelog

## v0.4.0

**Added**
- `experiments/run_ablation.py` — config-driven leave-one-gate-out study. A YAML
  or JSON config names the layers and their declared properties, the target
  raster, the model (ridge / rf / gb / torch), seeds, spatial-split parameters,
  gate thresholds and output path. Output `rows` are exactly
  `AblationResult.to_rows()`, with per-run records, per-arm contract reports and
  the config alongside. YAML needs PyYAML; JSON needs only the standard library.
- Per-gate *variants* in the config: the cube you would actually have built
  without a gate. Without one an arm trains on the same files as the baseline,
  and the output says so rather than reporting a zero delta as a finding. Each
  variant is also checked against the full gate set, so a defect the gate cannot
  catch is flagged.
- The runner refuses: random splits, fewer than three seeds (unless
  `--allow-few-seeds`, which marks the output not reportable), unknown config or
  threshold keys, multi-band files without an explicit band, and a target whose
  CRS or extent differs from the layers. Synthetic results are marked not
  reportable. `--dry-run` runs the gates for every arm without training.
- `experiments/configs/example.yaml` — a complete synthetic study that writes
  temporary GeoTIFFs and runs end to end with no real data.
- `tests/test_io.py` — first tests for `eocube.io`, on GeoTIFFs written per test.
  Skipped when rasterio is absent. `tests/test_run_ablation.py` likewise.
- Test suite expanded to 74 tests (45 without rasterio and PyYAML).

**Fixed**
- `_invalid_fraction` inspected band 1 only, so a multi-band raster with clean
  first band passed as 0% invalid. Every band is now read, one at a time;
  `invalid_fraction` is the fraction of pixels invalid in any band and
  `invalid_fraction_by_band` keeps the per-band figures.
- Decimated invalid-fraction reads were indistinguishable from full counts.
  `invalid_fraction_sampling` now records the decimation step and pixel counts,
  the quality gate copies it into its evidence, and its messages call a
  decimated figure an estimate. `describe(max_stat_pixels=...)` sets the budget.
- `check_sensor_physics` matched band names by substring, so `B8` matched
  inside `B80`. Names now match as whole tokens; evidence records which layers
  each expression was found to use.
- A layer with no `invalid_fraction` (for example `describe(read_stats=False)`)
  was treated by the quality gate as 0% invalid. It is now reported as not
  measured, with a decision, as the `describe` docstring already promised.
- A non-finite nodata value (`-inf`) was counted twice.
- Declared values were compared with observed ones as strings, so `gsd=10`
  against `10.0`, or `epsg:32635` against `EPSG:32635`, was reported as a
  conflict. Numbers, sequences and CRSs are now compared by value.
- `to_descriptor()` emitted bare `NaN` for NaN nodata, which is not JSON and is
  rejected by strict parsers. Non-finite floats are written as `"nan"`, `"inf"`,
  `"-inf"`. Descriptor version is now `eocube-cube-descriptor/0.3`.

## v0.3.0

**Added**
- `eocube.pipeline` — sample extraction from cubes and, critically,
  `spatial_block_split`. EO data is spatially autocorrelated, so a random
  train/test split places near-duplicate pixels on both sides and inflates every
  metric. Blocks are assigned whole to folds instead.
- `make_train_eval()` — builds the `train_eval(layers, seed) -> metric` callable
  the ablation harness expects, with optional exclusion of gap-filled pixels from
  the test set.
- `eocube.baselines` — interchangeable `fit_predict` implementations: pure-NumPy
  ridge (no dependencies), scikit-learn random forest and gradient boosting, and
  a PyTorch MLP that uses the GPU when one is available.
- `examples/retrieval_experiment.py` — full cube to contracts to samples to model
  to ablation, with a side-by-side comparison of random and spatial splits.
- Test suite expanded to 41 tests.

**Note on expectations**
- For pixel-wise tabular retrieval a random forest is a strong baseline. If a
  deep model does not beat it, that is a result about the task and should be
  reported rather than tuned away.

## v0.2.0

**Added**
- `eocube.io` — raster-backed layer description. `describe()` reads CRS,
  transform, GSD, dtype, nodata and invalid fraction from the file itself.
  Properties that cannot be derived from pixels are recorded separately as
  declared, and a declaration contradicting the file is logged as a conflict
  with the observed value taking precedence.
- `to_descriptor()` / `write_descriptor()` — machine-readable JSON cube
  descriptor separating measurements, assertions and decisions.
- `eocube.ablation` — leave-one-gate-out harness with repeated seeds, refusal-rate
  tracking and effect-size reporting.
- `examples/from_rasters.py`, `examples/ablation_demo.py`.
- Test suite expanded to 28 tests.

**Changed**
- `max_grid_offset_px` default documented: the offset wraps to the nearest grid
  line, so 0.5 is its maximum possible value and 0.1 is the working default.

**Closed limitation**
- v0.1 took declared metadata at its word. It no longer does.

## v0.1.0

Initial release: five contract gates, worked peatland example, 13 tests.
