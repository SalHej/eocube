# Changelog

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
