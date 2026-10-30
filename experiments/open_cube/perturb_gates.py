"""
Do the gates that never fired on the 2019 cube fire when the failure is present?
(docs/open_ablation.md, "Validation: the silent gates under induced failures")

    python experiments/open_cube/perturb_gates.py --raw ~/work/data/raw/open_cube/2019 \
        --work ~/work/data/perturb/2019 --out ~/work/results/open_cube/perturb_2019 --n 20

Evidence that silence means absence, not blindness. Not part of the ablation, and the
method is unchanged. Geometry, resolution and sensor physics refused nothing on the real
cube and under every bypass. Here each is given a failure on purpose, on N real samples
spread across the cube, and the five gates run on the result (geometry and quality in
point mode, as the method now is).

Cases where a gate is expected NOT to fire are included on purpose. A test that can
only show "fires" cannot tell absence from blindness; these show where the gates are
blind by design, so the claim is bounded.

Perturbed chips are copies written under --work; the cube's own files are not touched.
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cube as bc                     # noqa: E402
from bypass_cube import build              # noqa: E402
from eocube import run_all_gates            # noqa: E402
from eocube.io import describe              # noqa: E402

GATES = ("geometry_crs", "resolution", "temporal_alignment", "quality_missingness",
         "sensor_physics")


def copy_chip(src_path: Path, dst: Path, *, crs=None, shift_m=(0.0, 0.0), fn=None) -> Path:
    """A copy of a chip with its CRS relabelled, its transform shifted, or its values mapped."""
    with rasterio.open(src_path) as src:
        prof, data, t = src.profile.copy(), src.read(), src.transform
    if crs is not None:
        prof["crs"] = crs
    prof["transform"] = rasterio.Affine(t.a, t.b, t.c + shift_m[0], t.d, t.e, t.f + shift_m[1])
    if fn is not None:
        data = fn(data, prof)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **prof) as out:
        out.write(data)
    return dst


def to_db(data, prof):
    """Linear gamma0 to dB, nodata kept. The units change; nothing else does."""
    nd = prof.get("nodata")
    ok = (data > 0) & (data != nd)
    out = np.where(ok, 10 * np.log10(np.where(ok, data, 1)), nd).astype("float32")
    prof["dtype"] = "float32"
    return out


def resample_onto(src_path: Path, ref_path: Path, dst: Path, nearest=False) -> Path:
    """Reproject a chip onto another chip's grid: the shared-grid stack this cube avoids."""
    with rasterio.open(ref_path) as ref, rasterio.open(src_path) as src:
        prof = ref.profile.copy()
        out = np.zeros((1, ref.height, ref.width), dtype="float32")
        reproject(src.read(1), out[0], src_transform=src.transform, src_crs=src.crs,
                  dst_transform=ref.transform, dst_crs=ref.crs, src_nodata=0, dst_nodata=0,
                  resampling=Resampling.nearest if nearest else Resampling.bilinear)
    prof.update(dtype="float32", nodata=0, count=1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **prof) as o:
        o.write(out)
    return dst


def layers_for(pol, f2, point, *, s1_declared=None, s2_declared=None, s2_files=None):
    """The sample's eight layers, described as build_cube.py describes them."""
    f2 = {**f2, **(s2_files or {})}
    s1d = {**bc.DECLARED_S1, "sample_point": point, **(s1_declared or {})}
    out = [describe(pol["VV"], "S1_VV", declared=s1d), describe(pol["VH"], "S1_VH", declared=s1d)]
    for b in bc.S2_BANDS:
        d = {**bc.DECLARED_S2, "sample_point": point, "qa_mask_path": str(f2["SCL"]),
             "qa_valid_classes": bc.SCL_USABLE, **(s2_declared or {})}
        if b != "B02":
            d["plausible_range"] = (0, 12000)
        out.append(describe(f2[b], f"S2_{b}", declared=d))
    return out


def gates(layers, row, **kw):
    t1, t2 = bc.utc(row["s1_time"]), bc.utc(row["s2_time"])
    kw = {"geometry_mode": "point", "quality_mode": "point", **kw}
    rep = run_all_gates(layers, acquisitions={"S1": [t1], "S2": [t2]}, target_time=t1, **kw)
    return {r.gate: (r.verdict.value, r.reasons[:2]) for r in rep.results}


def cases(sample, other, work: Path):
    """{case: (gate expected to fire or None, layers, gate kwargs)}."""
    row, pol, f2 = sample
    point = tuple(map(float, row["_point"]))
    w = work / f"{row['station']}_{row['s1_time'][:16].replace(':', '')}"
    crs_flip = {"EPSG:32631": "EPSG:32632", "EPSG:32632": "EPSG:32631"}
    with rasterio.open(f2["B02"]) as s:
        flip = crs_flip[str(s.crs)]
    out = {"unperturbed": (None, layers_for(pol, f2, point), {})}

    # geometry, point mode
    out["G1 S2 chip from another station (join error)"] = (
        "geometry_crs", layers_for(pol, other[2], point), {})
    out["G2 S2 CRS mislabelled (zone 31 <-> 32)"] = ("geometry_crs", layers_for(
        pol, f2, point, s2_files={b: copy_chip(f2[b], w / "g2" / f2[b].name, crs=flip)
                                  for b in (*bc.S2_BANDS, "SCL")}), {})
    out["G3 S2 transform shifted 2 km"] = ("geometry_crs", layers_for(
        pol, f2, point, s2_files={b: copy_chip(f2[b], w / "g3" / f2[b].name, shift_m=(2000, 0))
                                  for b in (*bc.S2_BANDS, "SCL")}), {})
    out["G4 S2 transform shifted 30 m (expected blind)"] = (None, layers_for(
        pol, f2, point, s2_files={b: copy_chip(f2[b], w / "g4" / f2[b].name, shift_m=(30, 0))
                                  for b in (*bc.S2_BANDS, "SCL")}), {})

    # a shared 10 m grid: every S2 band resampled onto the S1 VV grid
    shared = {b: resample_onto(f2[b], pol["VV"], w / "grid" / f2[b].name)
              for b in bc.S2_BANDS}
    shared["SCL"] = resample_onto(f2["SCL"], pol["VV"], w / "grid" / f2["SCL"].name, nearest=True)
    out["R1 native layers, declared 10 m target (20 m bands upsampled)"] = (
        "resolution", layers_for(pol, f2, point), {"target_gsd": 10.0})
    out["R2 the same, upsampling allowed and recorded"] = (
        None, layers_for(pol, f2, point), {"target_gsd": 10.0, "allow_upsampling": True})
    out["R3 already resampled to a shared 10 m grid (expected blind)"] = (
        None, layers_for(pol, f2, point, s2_files=shared), {"target_gsd": 10.0})
    out["G5 shared grid, grid-mode geometry, one band shifted 0.3 px"] = ("geometry_crs", layers_for(
        pol, f2, point, s2_files={**shared, "B04": copy_chip(shared["B04"], w / "g5" / "B04.tif",
                                                             shift_m=(3, 0))}),
        {"geometry_mode": "grid"})
    out["G6 shared grid, grid-mode geometry, unshifted"] = (
        None, layers_for(pol, f2, point, s2_files=shared), {"geometry_mode": "grid"})

    # sensor physics
    out["S1 SAR units undeclared"] = ("sensor_physics", layers_for(
        pol, f2, point, s1_declared={"units": None}), {})
    out["S2 SAR declared not terrain-corrected"] = ("sensor_physics", layers_for(
        pol, f2, point, s1_declared={"terrain_corrected": False}), {})
    out["S3 SAR declared uncalibrated"] = ("sensor_physics", layers_for(
        pol, f2, point, s1_declared={"calibrated": False}), {})
    db = {"VV": copy_chip(pol["VV"], w / "db" / pol["VV"].name, fn=to_db),
          "VH": copy_chip(pol["VH"], w / "db" / pol["VH"].name, fn=to_db)}
    out["S4 dB layers averaged, declared dB"] = ("sensor_physics", layers_for(
        db, f2, point, s1_declared={"units": "db", "plausible_range": None}),
        {"band_expressions": ["mean(S1_VV, S1_VH)"]})
    out["S5 optical-radar normalised difference"] = ("sensor_physics", layers_for(pol, f2, point),
        {"band_expressions": ["(S2_B08 - S1_VV) / (S2_B08 + S1_VV)"]})
    out["S6 dB values declared linear (sensor physics blind; quality should catch)"] = (
        "quality_missingness", layers_for(db, f2, point), {})
    return out


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    raw, work, out = a.raw.expanduser(), a.work.expanduser(), a.out.expanduser()
    if work.exists():
        shutil.rmtree(work)
    out.mkdir(parents=True, exist_ok=True)
    samples, _, lonlat = build(raw, None, a.year)
    for s in samples:
        s[0]["_point"] = lonlat[s[0]["station"]]
    pick = [samples[i] for i in np.linspace(0, len(samples) - 1, a.n).round().astype(int)]
    print(f"{len(samples)} samples in the protected cube; {len(pick)} picked across "
          f"{len({s[0]['station'] for s in pick})} stations")

    tally = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    expected, example = {}, {}
    for k, s in enumerate(pick):
        other = next(o for o in pick[k + 1:] + pick[:k] if o[0]["station"] != s[0]["station"])
        for case, (gate, layers, kw) in cases(s, other, work).items():
            expected[case] = gate
            res = gates(layers, s[0], **kw)
            for g in GATES:
                tally[case][g][res[g][0]] += 1
                if gate == g and res[g][1] and case not in example:
                    example[case] = res[g][1][0][:200]
        print(f"  sample {k + 1}/{len(pick)} done")

    print(f"\n{'case':72s} {'expected':>16}  " + "  ".join(f"{g[:10]:>10}" for g in GATES))
    rows = []
    for case, by_gate in tally.items():
        ref = {g: by_gate[g].get("refuse", 0) for g in GATES}
        print(f"{case:72s} {str(expected[case]):>16}  " +
              "  ".join(f"{ref[g]:>4}/{len(pick):<5}" for g in GATES))
        rows.append({"case": case, "expected_gate": expected[case], "n": len(pick),
                     "refusals": ref, "verdicts": {g: dict(c) for g, c in by_gate.items()},
                     "example_reason": example.get(case)})
    print("\ncells: samples refused by that gate, of N")
    for case, msg in example.items():
        print(f"  {case}: {msg}")
    (out / "perturb.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
