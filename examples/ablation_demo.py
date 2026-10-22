"""Ablation demo on synthetic data — runs with no rasters and no GPU.

Shows the harness end to end so the experimental design can be validated before
real data arrives. The synthetic "model" is deliberately crude: its score degrades
when the cube carries a defect that a gate would have caught. Replace ``train_eval``
with a real training loop and ``build_cube`` with real layers, and the harness is
unchanged.

Run:  python examples/ablation_demo.py
"""

import random
from datetime import datetime

from eocube import run_ablation

TARGET = datetime(2025, 7, 15)


def build_cube(skip):
    """Assemble a cube, introducing the defect each disabled gate would catch."""
    layers = [
        dict(name="S1_VV", modality="sar", units="linear", calibrated=True,
             terrain_corrected=True, gsd=10.0, crs="EPSG:32635",
             transform=(10, 0, 500000, 0, -10, 6100000), width=512, height=512,
             invalid_fraction=0.02, defect=0.0),
        dict(name="S2_B08", modality="optical", reflectance=True, gsd=10.0,
             crs="EPSG:32635", transform=(10, 0, 500000, 0, -10, 6100000),
             width=512, height=512, invalid_fraction=0.18,
             qa_mask_available=True, qa_mask_applied=True, defect=0.0),
        dict(name="TSX_HH", modality="sar", units="linear", calibrated=True,
             terrain_corrected=True, gsd=3.0, crs="EPSG:32635",
             transform=(3, 0, 500000, 0, -3, 6100000), width=1706, height=1706,
             invalid_fraction=0.0, defect=0.0),
    ]
    kwargs = dict(
        acquisitions={"S1": [datetime(2025, 7, 12)], "TSX": [datetime(2025, 6, 30)]},
        target_time=TARGET, decay_alpha=0.05, window_days=30.0,
    )

    # Without a gate, the corresponding defect goes undetected and enters the cube.
    if "geometry_crs" in skip:
        layers[1]["transform"] = (10, 0, 500004, 0, -10, 6100000)   # misregistered
        layers[1]["defect"] += 0.09
    if "resolution" in skip:
        kwargs["target_gsd"] = 3.0                                   # upsampling
        kwargs["allow_upsampling"] = True
        layers[0]["defect"] += 0.04
    if "quality_missingness" in skip:
        layers[1]["invalid_fraction"] = 0.47                         # contaminated
        layers[1]["qa_mask_applied"] = False
        layers[1]["defect"] += 0.06
    if "temporal_alignment" in skip:
        kwargs["acquisitions"]["S1"] = [datetime(2025, 7, 12), datetime(2025, 7, 24)]
        layers[0]["defect"] -= 0.05                                  # leakage inflates
    if "sensor_physics" in skip:
        layers[0]["units"] = "db"                                    # dB arithmetic
        layers[0]["defect"] += 0.03
    return layers, kwargs


def train_eval(layers, seed):
    """Stand-in for a real retrieval model. Returns a pseudo-R-squared."""
    rng = random.Random(seed)
    penalty = sum(l.get("defect", 0.0) for l in layers)
    return 0.81 - penalty + rng.gauss(0, 0.008)


if __name__ == "__main__":
    print("Leave-one-gate-out ablation (synthetic data)\n")
    res = run_ablation(build_cube, train_eval, seeds=(0, 1, 2, 3, 4),
                       train_on_refused=True)
    print("\n" + res.summary())
    print()
    print("train_on_refused=True trains even when a gate refuses, which is how the")
    print("cost of ignoring that gate is measured. Set it False to measure the")
    print("refusal rate instead. Note the temporal_alignment row: removing it")
    print("*raises* the score, because leakage inflates the metric. That is exactly")
    print("the case a reviewer will probe, and the reason both directions are reported.")
