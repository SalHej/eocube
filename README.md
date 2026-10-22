# eocube — contract-checked multi-source Earth Observation data cubes

[![DOI](https://img.shields.io/badge/preprint-10.31223%2FX5TN59-blue)](https://doi.org/10.31223/X5TN59)
[![Zenodo](https://img.shields.io/badge/archive-10.5281%2Fzenodo.22552042-blue)](https://doi.org/10.5281/zenodo.22552042)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

A reference implementation of five machine-checkable **contract gates** for
multi-source Earth Observation data cubes, plus a harness for measuring what each
gate is actually worth.

> Al-Heejawi, S. M. A. (2026). *Agentic large language model workflows for Earth
> Observation: tool orchestration, foundation-model adaptation, and the evaluation
> gap.* EarthArXiv. https://doi.org/10.31223/X5TN59

---

## Why this exists

Public EO benchmarks arrive analysis-ready: co-registered, temporally aligned,
quality-flagged and packaged. Real multi-sensor practice does not. Combining
Sentinel-1 C-band SAR with TerraSAR-X, EnMAP hyperspectral imagery and a
meteorological series means confronting temporal asynchrony, differing ground
sample distances, incompatible coordinate reference systems, inconsistent quality
flags and missing observations — before any model is trained.

**A finding that rests on a badly constructed cube is not recoverable by any
downstream architecture, however well designed.** Yet cube construction is usually
treated as a preprocessing step performed by a careful human, rather than as a set
of properties that can be verified, recorded and measured.

## Three design commitments

**1. Nothing is coerced silently.** When a gate must resample, composite or
gap-fill to proceed, it records that decision in the provenance log. If a coarse
band is upsampled, the report says so and states that effective resolution is
unchanged.

**2. Failure produces refusal, not repair.** A gate that cannot satisfy its
contract returns `REFUSE`, and downstream code is expected to stop. For a system
whose outputs are scientific claims, proceeding under insufficient evidence is the
most consequential failure mode.

**3. Declarations are not believed.** `eocube.io` reads what is actually true of a
raster. A declared property that contradicts the file is recorded as a conflict and
the observed value wins. Believing the declaration would reintroduce exactly the
failure the contracts exist to prevent.

## The five gates

| Gate | What is verified | Failure prevented |
|---|---|---|
| `geometry_crs` | Common CRS; sub-pixel origin offsets within tolerance; co-registration verified rather than assumed | Sub-pixel misregistration silently degrades fusion and is later blamed on the architecture |
| `resolution` | GSD reconciliation; declared resampling policy; upsampling blocked unless justified | Interpolation artefacts read as signal; effective resolution overstated |
| `temporal_alignment` | Revisit asynchrony; declared compositing window; **no acquisition later than the target time** | Temporal leakage inflates validation scores, especially in continuous regression |
| `quality_missingness` | Available QA masks actually applied; gap-filling policy explicit and logged | Gap-filled values treated as observations; cloud contamination enters the target |
| `sensor_physics` | SAR units, calibration, terrain correction; modality-appropriate band arithmetic | Physically meaningless indices; decibels added; SAR fused as though it were optical |

## Install

```bash
git clone https://github.com/SalHej/eocube.git
cd eocube
pip install -e .
```

Python 3.10+. The gates and the ablation harness have **no third-party
dependencies**. Reading real rasters requires the optional extra:

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
print(report)          # human-readable
report.to_dict()       # machine-readable
report.provenance()    # every decision taken on your behalf
```

Full worked example: [`examples/peatland_cube.py`](examples/peatland_cube.py).

## From real rasters

```python
from eocube.io import describe_many, to_descriptor, write_descriptor

layers = describe_many([
    ("data/s1_vv.tif", "S1_VV", dict(modality="sar", units="linear",
        calibrated=True, terrain_corrected=True)),
    ("data/s2_b08.tif", "S2_B08", dict(modality="optical", reflectance=True)),
])
doc = to_descriptor(layers, report, cube_id="ezerelis-2025-07-15")
write_descriptor(doc, "cube.json")
```

`describe()` reads CRS, transform, GSD, dtype, nodata and invalid fraction from the
file. Properties that cannot be derived from pixels are recorded separately as
*declared*, and the descriptor keeps the two apart.

**Why the descriptor matters.** An automated system can only reason about a dataset
if the dataset describes itself in a consistent, machine-readable form. A cube that
merely *is* valid is not enough; it has to be able to state in what respects it was
checked, what was decided on its behalf, and what remains unverified. That is what
`to_descriptor()` emits.

## Measuring what the gates are worth

The claim that contracts improve downstream outcomes is a hypothesis. The ablation
harness tests it: disable one gate at a time, rebuild the cube, train the same
model, record what changes.

```python
from eocube import run_ablation

result = run_ablation(build_cube, train_eval, seeds=(0, 1, 2, 3, 4))
print(result.summary())
```

```
configuration                  n      mean       sd     delta   refused   decisions
-----------------------------------------------------------------------------------
<all gates active>             5    0.8175   0.0076   +0.0000       0%         5.0
without geometry_crs           5    0.7275   0.0076   -0.0900       0%         5.0
without quality_missingness    5    0.7575   0.0076   -0.0600       0%         3.0
without resolution             5    0.7775   0.0076   -0.0400       0%         3.0
without sensor_physics         5    0.7875   0.0076   -0.0300       0%         5.0
without temporal_alignment     5    0.8675   0.0076   +0.0500       0%         4.0
```

Note the last row. Removing the temporal gate **raises** the score, because leakage
inflates the metric. That is the case a reviewer will probe first, and the reason
the harness reports both directions rather than only improvements.

Three design decisions in the harness, each addressing a predictable criticism:

- **Repeated seeds.** A single run cannot separate a gate's effect from training
  variance. Three is the minimum; five is better.
- **Both error directions.** A gate can refuse a cube that would have trained
  perfectly well. Over-rigidity is a finding, and `refusal_rate` records it.
- **One variable at a time.** Model, split and training budget are held fixed.

Runnable demo with synthetic data, no GPU required:
[`examples/ablation_demo.py`](examples/ablation_demo.py).

## Scope and limitations

Stated plainly, because they matter:

- **This is a prototype, not production infrastructure.** It is a reference
  implementation of a proposal, released so the argument can be inspected in code
  rather than only in prose.
- **Thresholds are defaults, not standards.** The 0.1-pixel offset tolerance, the
  30% invalid-fraction limit and the 7-day compositing window are starting points.
  Sensible values are task-dependent and should be justified per study.
- **Gate coverage is not exhaustive.** Atmospheric-correction consistency, orbit
  direction mixing in SAR stacks, and label-geometry alignment are all absent and
  all matter.
- **Some properties remain unverifiable from pixels.** Terrain correction and
  radiometric calibration are declared, not measured. The descriptor says which is
  which; it cannot check them.
- **No empirical validation yet.** The ablation harness exists; the study has not
  been run on real data. Until it has, the benefit of these contracts is a
  hypothesis.

## Roadmap

- [x] Five contract gates with independent ablation
- [x] Raster-backed verification of declared metadata
- [x] Machine-readable cube descriptor
- [x] Leave-one-gate-out ablation harness
- [ ] Ablation study on a real multi-sensor retrieval task
- [ ] Atmospheric-correction and orbit-direction gates
- [ ] STAC metadata ingestion

Contributions and criticism are welcome — particularly from remote-sensing
scientists who think a gate is wrong. Please open an issue.

## Citation

```bibtex
@misc{alheejawi2026agentic,
  author    = {Al-Heejawi, Salah Mohammed Awad},
  title     = {Agentic Large Language Model Workflows for {Earth} {Observation}:
               Tool Orchestration, Foundation-Model Adaptation, and the
               Evaluation Gap},
  year      = {2026},
  publisher = {EarthArXiv},
  doi       = {10.31223/X5TN59}
}

@software{alheejawi2026eocube,
  author    = {Al-Heejawi, Salah Mohammed Awad},
  title     = {eocube: contract-checked multi-source Earth Observation data cubes},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.22552042}
}
```

## Licence

Apache License 2.0 — see [LICENSE](LICENSE).

## Author

**Salah Mohammed Awad Al-Heejawi**
ORCID [0000-0003-1949-6715](https://orcid.org/0000-0003-1949-6715) ·
Scopus 57206858380 ·
Artificial Intelligence Methods Laboratory, Vilnius University
