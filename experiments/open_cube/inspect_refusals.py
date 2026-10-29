"""What were the quality-gate refusals in a built cube? (docs/cube_2019.md)

    python experiments/open_cube/inspect_refusals.py --raw <raw/year> --cube <cubes/year>

For every sample the quality gate refused, re-describe its S2 layers and report which
layer and which bound triggered it, and the SCL class of the offending pixels (read
from the same S2 observation's SCL chip, nearest 20 m pixel). Reads chips; run in a
Slurm job.
"""
import argparse
import collections
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cube as bc                     # noqa: E402
from eocube.io import describe              # noqa: E402

SCL_NAMES = {0: "no_data", 1: "saturated", 2: "dark_area", 3: "cloud_shadow", 4: "vegetation",
             5: "not_vegetated", 6: "water", 7: "unclassified", 8: "cloud_medium",
             9: "cloud_high", 10: "thin_cirrus", 11: "snow"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--cube", type=Path, required=True)
    a = ap.parse_args()
    raw, cube = a.raw.expanduser(), a.cube.expanduser()
    per = json.load(open(cube / "gates.json"))["per_sample"]
    refused = {(p["station"], p["s1_time"]) for p in per if p["quality_missingness"] == "refuse"}
    rows = [r for r in csv.DictReader(open(cube / "samples.csv", encoding="utf-8"))
            if (r["station"], r["s1_time"]) in refused]
    stamps = {}
    for r in rows:
        stamps.setdefault((r["station"], r["s2_time"]), r)
    cause = collections.Counter()           # (layer, bound side) -> samples
    scl_of_bad = collections.defaultdict(collections.Counter)
    invalid_layers = collections.Counter()
    print(f"{len(rows)} refused samples use {len(stamps)} distinct station-S2 observations")
    for (stn, t2), r in stamps.items():
        n_samples = sum(1 for x in rows if x["station"] == stn and x["s2_time"] == t2)
        stamp = t2[:16].replace(":", "").replace("+0000", "")
        d = raw / "s2" / stn
        files = {f.name.rsplit("_", 1)[1].split(".")[0]: f for f in d.glob(f"S2_{stamp}*.tif")}
        with rasterio.open(files["SCL"]) as s:
            scl = s.read(1)
        for b in bc.S2_BANDS:
            rng = bc.DECLARED_S2["plausible_range"] if b == "B02" else (0, 12000)
            lay = describe(files[b], b, declared={**bc.DECLARED_S2, "plausible_range": rng})
            if lay["invalid_fraction"] > 0.30:
                invalid_layers[b] += n_samples
            if lay.get("implausible_fraction", 0) > 0.01:
                with rasterio.open(files[b]) as src:
                    a_ = src.read(1)
                    nd = src.nodata if src.nodata is not None else 0
                low = (a_ != nd) & (a_ < rng[0]); high = (a_ != nd) & (a_ > rng[1])
                side = "below" if low.sum() >= high.sum() else "above"
                cause[(b, side, rng[0] if side == "below" else rng[1])] += n_samples
                bad = low | high
                f = scl.shape[0] / a_.shape[0]
                rr, cc = np.nonzero(bad)
                cls = scl[np.minimum((rr * f).astype(int), scl.shape[0] - 1),
                          np.minimum((cc * f).astype(int), scl.shape[1] - 1)]
                for k, v in collections.Counter(cls.tolist()).items():
                    scl_of_bad[(b, side)][SCL_NAMES.get(k, k)] += v
    print("\nrefusal triggers (samples; a sample can have several):")
    for (b, side, bound), n in cause.most_common():
        tot = sum(scl_of_bad[(b, side)].values())
        mix = ", ".join(f"{k} {v / tot:.0%}" for k, v in scl_of_bad[(b, side)].most_common(4))
        print(f"  {b} {side} {bound}: {n} samples; offending pixels by SCL: {mix}")
    print("layers over 30% invalid:", dict(invalid_layers))


if __name__ == "__main__":
    main()
