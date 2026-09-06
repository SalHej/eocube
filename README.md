# eocube — contract-checked multi-source Earth Observation data cubes

[![DOI](https://img.shields.io/badge/preprint-10.31223%2FX5TN59-blue)](https://doi.org/10.31223/X5TN59)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Tests](https://github.com/USERNAME/eocube/actions/workflows/tests.yml/badge.svg)](https://github.com/USERNAME/eocube/actions)

A reference implementation of the five machine-checkable **contract gates** for
multi-source Earth Observation data cubes proposed in:

> Al-Heejawi, S. M. A. (2026). *Agentic large language model workflows for Earth
> Observation: tool orchestration, foundation-model adaptation, and the evaluation
> gap.* EarthArXiv. https://doi.org/10.31223/X5TN59

---

## Why this exists

Public EO benchmarks arrive analysis-ready: co-registered, temporally aligned,
quality-flagged and packaged. Real multi-sensor practice does not. Combining
Sentinel-1 C-band SAR with TerraSAR-X, EnMAP hyperspectral imagery and a
meteorological series means confronting temporal asynchrony, differing ground sample
distances, incompatible coordinate reference systems, inconsistent quality flags and
missing observations — before any model is trained.

**A finding that rests on a badly constructed cube is not recoverable by any
downstream architecture, however well designed.** Yet cube construction is usually
treated as a preprocessing step performed by a careful human, rather than as a set of
properties that can be verified.

This library makes the verification explicit, mechanical and — crucially —
*ablatable*. That last property is what turns "we preprocessed carefully" from a claim
about researcher diligence into a measurable system property with an effect size.

## Two design commitments

**1. Nothing is coerced silently.** When a gate must resample, composite or gap-fill to
proceed, it records that decision in the provenance log. If a coarse band is upsampled
to a fine grid, the report says so and states that effective resolution is unchanged.
Provenance is a first-class output, not a side effect.

**2. Failure produces refusal, not repair.** A gate that cannot satisfy its contract
returns `REFUSE`, and downstream code is expected to stop. For any system whose
outputs are scientific claims — and especially for an autonomous one — proceeding
under insufficient evidence is the most consequential failure mode. It should be
measurable, not assumed absent.

## The five gates

| Gate | What is verified | Failure prevented |
|---|---|---|
| `geometry_crs` | Common CRS; sub-pixel origin offsets within tolerance; pixel-grid co-registration verified rather than assumed | Sub-pixel misregistration silently degrades fusion and is later blamed on the architecture |
| `resolution` | GSD reconciliation across bands and sensors; declared resampling policy; upsampling blocked unless justified | Interpolation artefacts read as signal; effective resolution overstated |
| `temporal_alignment` | Revisit asynchrony; declared compositing window; **no acquisition later than the target time** | Temporal leakage inflates validation scores, especially in continuous regression |
| `quality_missingness` | Available QA masks actually applied; gap-filling policy explicit and logged; missingness reported | Gap-filled values treated as observations; cloud contamination enters the target |
| `sensor_physics` | SAR units, calibration and terrain correction; modality-appropriate band arithmetic | Physically meaningless indices; decibels added; SAR fused as though it were optical |

## Install

```bash
git clone https://github.com/USERNAME/eocube.git
cd eocube
pip install -e .
```

Python 3.10+. The core gates have **no third-party dependencies** — they operate on
plain metadata dictionaries so they can be exercised in CI without geospatial stacks
installed. Optional helpers for reading real rasters require `rasterio`:

```bash
pip install -e ".[raster]"
```

## Quick start

```python
from datetime import datetime
from eocube import run_all_gates

layers = [
    dict(name="S1_VV", modality="sar", units="db", calibrated=True,
         terrain_corrected=False, gsd=10.0, crs="EPSG:32635",
         transform=(10, 0, 500000, 0, -10, 6100000), width=512, height=512,
         invalid_fraction=0.02),
    dict(name="S2_B08", modality="optical", reflectance=True, gsd=10.0,
         crs="EPSG:4326",                      # <- different CRS
         transform=(10, 0, 500003, 0, -10, 6100004), width=512, height=512,
         invalid_fraction=0.41, qa_mask_available=True, qa_mask_applied=False),
]

report = run_all_gates(
    layers,
    acquisitions={"S1": [datetime(2025, 7, 12), datetime(2025, 7, 24)]},
    target_time=datetime(2025, 7, 15),
    band_expressions=["(S2_B08 - S1_VV) / (S2_B08 + S1_VV)"],
)
print(report)
```

Output:

```
[REFUSE            ] geometry_crs
    reason:   layers declare 2 distinct CRS: ['EPSG:32635', 'EPSG:4326'];
              reproject to a common CRS before cube assembly
[REFUSE            ] temporal_alignment
    reason:   source 'S1' contains 1 acquisition(s) after the target time;
              this is temporal leakage. Truncate the conditioning window or
              set forbid_future=False and justify it.
[REFUSE            ] quality_missingness
    reason:   layer 'S2_B08' has a quality mask available but not applied
    reason:   layer 'S2_B08' is 41.0% invalid, above the 30% threshold, and
              no gap-filling policy was declared
[REFUSE            ] sensor_physics
    reason:   SAR layer 'S1_VV' is not terrain/incidence-angle corrected
    reason:   expression combines optical and radar bands in a single arithmetic
              term; these measure different physical quantities
    reason:   expression applies additive arithmetic to decibel-scaled layer(s)

CUBE VERDICT: REFUSED (4 refusing gate(s))
```

A full worked example using an asynchronous four-source peatland cube — the motivating
case from Section 6 of the preprint — is in [`examples/peatland_cube.py`](examples/peatland_cube.py).

## Exponential time-decay weighting

The temporal gate optionally records the weight `exp(-alpha * |dt|)` applied to the
nearest acquisition per source, following the formulation used for asynchronous
multi-sensor fusion in peatland moisture retrieval. Recording it does not change the
verdict; it prevents a downstream model from applying a weighting that never appears
in the provenance record.

```python
report = run_all_gates(layers, acquisitions=acq, target_time=t,
                       decay_alpha=0.05, window_days=7.0)
report.results[2].evidence["per_source"]
# {'S1': {'n_acquisitions': 2, 'nearest': '2025-07-12T00:00:00',
#         'delta_days': 3.0, 'weight': 0.860708}, ...}
```

## Ablating a gate

Every gate can be removed individually. This is the point: it converts a
methodological claim into an experiment.

```python
run_all_gates(layers).ok                          # False
run_all_gates(layers, skip=["geometry_crs"]).ok   # True — and now measure the cost
```

An ablation study should report which gate was removed alongside the downstream
metric, so that the contribution of each contract is quantified rather than asserted.

## Provenance log

```python
for line in report.provenance():
    print("-", line)
```

```
- resolution: no target GSD supplied; defaulted to the coarsest input (10.0 m)
  so that no layer is upsampled
- resolution: layer 'TSX_HH' downsampled 3.0 m -> 10.0 m using bilinear
- temporal_alignment: source 'EnMAP' nearest acquisition is 74.0 d from the target,
  outside the declared 7.0 d window; retained and flagged rather than dropped
- temporal_alignment: exponential time-decay weighting exp(-0.05*|dt|) recorded
- quality_missingness: layer 'S2_B08' retains 18.0% invalid pixels, masked not filled
```

`report.to_dict()` returns the same content as JSON for machine consumption — for
example, as the input to an agent's verification step.

## Scope and limitations

Stated plainly, because they matter:

- **This is a prototype, not production infrastructure.** It is a reference
  implementation of a proposal, released so that the argument can be inspected and
  criticised in code rather than only in prose.
- **The gates operate on declared metadata**, not on pixel data. A layer that declares
  `terrain_corrected=True` is taken at its word. Verifying that claim against the
  raster itself is future work and is the natural next step.
- **Thresholds are defaults, not standards.** The 0.1-pixel offset tolerance, the
  30 % invalid-fraction limit and the 7-day compositing window are starting points.
  Sensible values are task-dependent and should be justified per study.
- **Gate coverage is not exhaustive.** Atmospheric correction consistency, orbit
  direction mixing in SAR stacks, and label-geometry alignment are all absent and all
  matter.
- **No empirical validation yet.** The claim that these contracts improve downstream
  outcomes is a hypothesis. Testing it — by ablating each gate and measuring the
  effect on a real retrieval task — is exactly the study the preprint calls for.

## Roadmap

- [ ] Raster-backed verification of declared metadata (`rasterio` path)
- [ ] Atmospheric-correction and orbit-direction gates
- [ ] Ablation harness reporting downstream effect size per gate
- [ ] STAC metadata ingestion
- [ ] Worked ablation on a public benchmark (BigEarthNet v2.0 S1/S2 pairs)

Contributions and criticism are welcome — particularly from remote-sensing scientists
who think a gate is wrong. Please open an issue.

## Citation

If you use this software, please cite the preprint:

```bibtex
@misc{alheejawi2026agentic,
  author       = {Al-Heejawi, Salah Mohammed Awad},
  title        = {Agentic Large Language Model Workflows for {Earth} {Observation}:
                  Tool Orchestration, Foundation-Model Adaptation, and the
                  Evaluation Gap},
  year         = {2026},
  publisher    = {EarthArXiv},
  doi          = {10.31223/X5TN59},
  url          = {https://doi.org/10.31223/X5TN59}
}
```

A `CITATION.cff` file is included for GitHub's citation widget.

## Licence

Apache License 2.0 — see [LICENSE](LICENSE).

## Author

**Salah Mohammed Awad Al-Heejawi**
ORCID: [0000-0003-1949-6715](https://orcid.org/0000-0003-1949-6715) ·
Scopus: 57206858380 ·
[Google Scholar](https://scholar.google.com/citations?user=7wtYD0QAAAAJ)
