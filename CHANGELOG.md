# Changelog

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
