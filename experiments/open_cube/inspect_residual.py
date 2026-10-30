"""
The samples that pass every gate in the fully bypassed cube although they differ from the
protected cube: what is invalid about each? (docs/open_ablation.md, "Station-pixel
validity: before and after")

    python experiments/open_cube/inspect_residual.py --raw ~/work/data/raw/open_cube/2019 \
        --bypass ~/work/data/raw/open_cube_bypass/2019

Rebuilds the bypass_pixel arm (first tile, S2 after the pass allowed, edge checks and
per-pixel rules off) and the protected cube, runs the gates with geometry and quality in
point mode, and for every changed sample that no gate refuses reports:
  - how it differs from the protected cube (new sample, other S2 time, tile or S1 frame);
  - the station pixel: SCL, B02, S1 VV/VH, and whether build_cube's per-pixel rules
    would accept it;
  - the fetcher's station_measured flag on the S2 chip (edge or fill at the station);
  - the S2 age, and whether the S2 scene is after the pass.
Reads chips; run in a Slurm job.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cube as bc                     # noqa: E402
from bypass_cube import ARMS, build         # noqa: E402

METHOD = ("geometry_crs[point]", "resolution", "temporal_alignment",
          "quality_missingness[point]", "sensor_physics")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--bypass", type=Path, required=True)
    ap.add_argument("--year", type=int, default=2019)
    a = ap.parse_args()
    raw, bypass = a.raw.expanduser(), a.bypass.expanduser()

    protected, _, _ = build(raw, bypass, a.year)
    prot = {(r["station"], r["s1_time"]): r for r, _, _ in protected}
    prot_keys = {(r["station"], r["s1_time"], r["s2_time"], r["s1_file"], r["s2_file"])
                 for r, _, _ in protected}
    samples, _, lonlat = build(raw, bypass, a.year, **ARMS["bypass_pixel"])
    changed = [s for s in samples if (s[0]["station"], s[0]["s1_time"], s[0]["s2_time"],
                                      s[0]["s1_file"], s[0]["s2_file"]) not in prot_keys]
    _, _, _, per = bc.run_gates(changed, 7.0, lonlat)
    passing = [(s, p) for s, p in zip(changed, per) if not any(p[g] == "refuse" for g in METHOD)]
    print(f"bypass_pixel: {len(samples)} samples, {len(changed)} changed, "
          f"{len(passing)} pass every gate")

    measured = {}
    for man in (raw / "manifest.jsonl", bypass / "manifest.jsonl"):
        for line in open(man, encoding="utf-8"):
            r = json.loads(line)
            if r.get("kind") == "s2" and r.get("status") == "ok" and r.get("file"):
                measured[r["file"].rsplit("_", 1)[0]] = r.get("station_measured", None)

    rows, why = [], collections.Counter()
    for (row, pol, f2), p in passing:
        stn = row["station"]
        lon, lat = lonlat[stn]
        scl, b02 = bc.pixel(f2["SCL"], lon, lat), bc.pixel(f2["B02"], lon, lat)
        vv, vh = bc.pixel(pol["VV"], lon, lat), bc.pixel(pol["VH"], lon, lat)
        s2_ok = scl in bc.SCL_USABLE and b02 is not None and b02 >= bc.B02_FLOOR
        s1_ok = all(v is not None and v > 0 and v != bc.S1_NODATA for v in (vv, vh))
        old = prot.get((stn, row["s1_time"]))
        if old is None:
            diff = "new sample (dropped in the protected cube)"
        else:
            diff = ", ".join(k for k, lab in (("s2_time", "S2 time"), ("s2_file", "S2 tile"),
                                               ("s1_file", "S1 frame"))
                             if old[k] != row[k]) or "identical keys"
        flag = measured.get(f2["B02"].name.rsplit("_", 1)[0])
        rec = {"station": stn, "s1_time": row["s1_time"][:16], "s2_time": row["s2_time"][:16],
               "s2_age_days": row["s2_age_days"], "differs_by": diff,
               "protected_s2_time": old["s2_time"][:16] if old else None,
               "s2_file": f2["B02"].name, "scl": scl, "b02": b02, "vv": vv, "vh": vh,
               "pixel_rules_accept_s2": s2_ok, "pixel_rules_accept_s1": s1_ok,
               "station_measured": flag, "bypassed_chip": "open_cube_bypass" in str(f2["B02"]),
               "chip_quality_verdict": p["quality_missingness"]}
        rows.append(rec)
        why[(diff, s2_ok and s1_ok, flag)] += 1
    for r in rows:
        print(json.dumps(r))
    print("\nsummary: (differs by, per-pixel rules accept, station_measured) -> count")
    for k, n in why.most_common():
        print(f"  {k}: {n}")
    chip = collections.Counter(r["chip_quality_verdict"] for r in rows)
    print(f"chip-level quality verdict on these samples: {dict(chip)}")


if __name__ == "__main__":
    main()
