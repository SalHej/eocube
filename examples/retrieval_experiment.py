"""End-to-end retrieval experiment: cube -> contracts -> samples -> model -> ablation.

This is the full pipeline with a real model, real spatial cross-validation and real
metrics. Only the imagery is synthetic, and swapping it for the project data is a
change to ``load_layers`` and ``load_target`` — nothing else moves.

    python examples/retrieval_experiment.py                # ridge, no deps
    python examples/retrieval_experiment.py --model rf     # random forest
    python examples/retrieval_experiment.py --model torch  # MLP, GPU if present

What to look at in the output:

* **Random vs spatial split.** The script reports both. The random split scores
  higher — always — because neighbouring pixels are near-duplicates and a random
  split puts them on both sides. That gap is the measure of how much a naive
  evaluation would have over-reported.

* **The ablation table.** Removing a gate changes the score. Negative means the
  gate was earning its place.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import numpy as np

from eocube import (baselines, extract_samples, make_train_eval, r2_score,
                    run_ablation, spatial_block_split)

SIZE = 160
TARGET_TIME = datetime(2025, 7, 15)


# ---------------------------------------------------------------------------
# Data. Replace these two functions with the project rasters.
# ---------------------------------------------------------------------------
def synth_scene(seed: int = 0):
    """A spatially autocorrelated scene with a known target.

    Autocorrelation is the point: without it, random and spatial splits would
    score the same and the demonstration would be meaningless.
    """
    rng = np.random.default_rng(seed)

    def smooth(scale: float) -> np.ndarray:
        coarse = rng.normal(0, 1, (int(SIZE / scale) + 2, int(SIZE / scale) + 2))
        r = np.linspace(0, coarse.shape[0] - 1, SIZE).round().astype(int)
        c = np.linspace(0, coarse.shape[1] - 1, SIZE).round().astype(int)
        return coarse[np.ix_(r, c)]

    wetness = smooth(20.0)
    vegetation = smooth(14.0)

    s1_vv = 0.08 + 0.05 * wetness + rng.normal(0, 0.006, (SIZE, SIZE))
    s2_b08 = 0.22 + 0.09 * vegetation - 0.03 * wetness + rng.normal(0, 0.01, (SIZE, SIZE))
    tsx_hh = 0.06 + 0.04 * wetness + 0.01 * vegetation + rng.normal(0, 0.005, (SIZE, SIZE))
    era5 = 12.0 + 3.0 * smooth(40.0)

    # soil moisture, the quantity to retrieve
    target = (0.30 + 0.12 * wetness + 0.03 * vegetation
              + 0.002 * (era5 - 12.0) + rng.normal(0, 0.012, (SIZE, SIZE)))
    return dict(s1_vv=s1_vv, s2_b08=s2_b08, tsx_hh=tsx_hh, era5=era5), target


def load_layers(scene, skip=()):
    """Assemble the cube. Disabling a gate lets its defect through.

    Each branch introduces the defect the missing gate would have caught — not an
    arbitrary penalty, but an actual corruption of the pixels the model will see.
    """
    tf = (10, 0, 500000, 0, -10, 6100000)
    s1 = scene["s1_vv"].copy()
    s2 = scene["s2_b08"].copy()
    tsx = scene["tsx_hh"].copy()
    era5 = scene["era5"].copy()

    if "geometry_crs" in skip:
        s2 = np.roll(s2, shift=1, axis=1)          # one-pixel misregistration
    if "quality_missingness" in skip:
        rng = np.random.default_rng(7)
        cloud = rng.random(s2.shape) < 0.25        # contamination, unmasked
        s2 = np.where(cloud, s2 + 0.35, s2)
    if "sensor_physics" in skip:
        s1 = 10 * np.log10(np.clip(s1, 1e-6, None))   # dB, then used additively

    layers = [
        dict(name="S1_VV", array=s1, modality="sar",
             units="db" if "sensor_physics" in skip else "linear",
             calibrated=True, terrain_corrected=True, gsd=10.0,
             crs="EPSG:32635",
             transform=(10, 0, 500010, 0, -10, 6100000) if "geometry_crs" in skip else tf,
             width=SIZE, height=SIZE, invalid_fraction=0.0),
        dict(name="S2_B08", array=s2, modality="optical", reflectance=True,
             gsd=10.0, crs="EPSG:32635", transform=tf, width=SIZE, height=SIZE,
             invalid_fraction=0.25 if "quality_missingness" in skip else 0.0,
             qa_mask_available=True,
             qa_mask_applied="quality_missingness" not in skip),
        dict(name="TSX_HH", array=tsx, modality="sar", units="linear",
             calibrated=True, terrain_corrected=True, gsd=10.0,
             crs="EPSG:32635", transform=tf, width=SIZE, height=SIZE,
             invalid_fraction=0.0),
        dict(name="ERA5_T", array=era5, modality="meteorological", gsd=10.0,
             crs="EPSG:32635", transform=tf, width=SIZE, height=SIZE,
             invalid_fraction=0.0),
    ]
    kwargs = dict(
        acquisitions={"S1": [datetime(2025, 7, 12)], "TSX": [datetime(2025, 6, 30)]},
        target_time=TARGET_TIME, decay_alpha=0.05, window_days=30.0,
    )
    if "temporal_alignment" in skip:
        kwargs["acquisitions"]["S1"].append(datetime(2025, 7, 22))
    return layers, kwargs


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["ridge", "rf", "gb", "torch"], default="ridge")
    ap.add_argument("--seeds", type=int, default=5)
    args = ap.parse_args()

    fit_predict = {
        "ridge": baselines.ridge,
        "rf": baselines.random_forest,
        "gb": baselines.gradient_boosting,
        "torch": baselines.torch_mlp,
    }[args.model]

    scene, target = synth_scene()
    build = lambda layers: extract_samples(layers, target)

    # --- why the split matters ----------------------------------------------
    print("=" * 76)
    print("1. SPATIAL VERSUS RANDOM SPLIT")
    print("=" * 76)
    layers, _ = load_layers(scene)
    samples = build(layers)
    print(samples.summary())

    rng = np.random.default_rng(0)
    perm = rng.permutation(len(samples))
    cut = int(0.75 * len(samples))
    rtr, rte = perm[:cut], perm[cut:]
    r_pred = fit_predict(samples.X[rtr], samples.y[rtr], samples.X[rte], 0)
    r_random = r2_score(samples.y[rte], r_pred)

    str_, ste = spatial_block_split(samples.coords, n_blocks=8, seed=0)
    s_pred = fit_predict(samples.X[str_], samples.y[str_], samples.X[ste], 0)
    r_spatial = r2_score(samples.y[ste], s_pred)

    print(f"\n  random split   R2 = {r_random:.4f}   <- optimistic")
    print(f"  spatial blocks R2 = {r_spatial:.4f}   <- honest")
    print(f"  inflation          {r_random - r_spatial:+.4f}")
    print("\n  Neighbouring pixels are near-duplicates. A random split puts them")
    print("  on both sides, so the model is scored on data it has effectively")
    print("  seen. Every number below uses the spatial split.")

    # --- the ablation --------------------------------------------------------
    print("\n" + "=" * 76)
    print(f"2. LEAVE-ONE-GATE-OUT ABLATION  (model: {args.model})")
    print("=" * 76 + "\n")

    train_eval = make_train_eval(build, fit_predict, n_blocks=8, test_fraction=0.25)
    result = run_ablation(
        lambda skip: load_layers(scene, skip),
        train_eval,
        seeds=tuple(range(args.seeds)),
        train_on_refused=True,
        verbose=True,
    )
    print("\n" + result.summary())
    print("\nThese numbers come from a real model on real spatial folds. Only the")
    print("imagery is synthetic. Point load_layers() and the target at the project")
    print("rasters and nothing else in this script changes.")


if __name__ == "__main__":
    main()
