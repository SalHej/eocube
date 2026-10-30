"""
Can the model generalise to unseen stations? Two arms against the 1 x 1 baseline
(PLAN.md, "Generalisation (2026-10-30)"; docs/generalisation.md).

    python experiments/open_cube/generalisation.py aux   --cube ~/work/data/cubes/open_cube/2019
    python experiments/open_cube/generalisation.py arm-a --cube ... --out ~/work/results/open_cube/generalisation_2019
    python experiments/open_cube/generalisation.py arm-b --cube ... --out ...

An extension; the 1 x 1 point-mode ablation remains the main result.

aux    Static features at each station, read at the station point (network; Slurm):
       SoilGrids 2.0 (ISRIC, CC-BY 4.0) clay, sand, silt, bulk density and organic carbon,
       0-5 cm mean, from the public VRTs; NASADEM elevation and slope (Horn, 3 x 3) from
       Microsoft Planetary Computer. Writes station_aux.json beside the cube. A few MB.
arm-a  Static auxiliary features. Arms: baseline (the 8 pixel features), static only (7),
       baseline + static (15). The model cannot place an unseen station if nothing in its
       features distinguishes sandy from clay soil; this tests that.
arm-b  Change detection. Target: the change in soil moisture between consecutive S1 passes
       at one station, primarily within one relative orbit (viewing geometry cancels),
       secondarily across orbits. Features: change in VV and VH (dB difference, i.e. the
       log of the linear ratio), the time between passes, and the S2 bands at the later
       pass. Compared on the same pairs with the baseline absolute model's implied change,
       p(t2) - p(t1), and with predicting no change. Its own target, never mixed with the
       absolute metric.

Same samples (all 3,505; point-mode gates refuse none), same stratified station folds and
5 partitions, same random forest (300 trees). Absolute and anomaly R2 per fold; permutation
importance on each held-out fold for every arm.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import re
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import EAST, stratified        # noqa: E402
from run_ablation import anom_r2, r2       # noqa: E402

BASE = ("vv", "vh", "B02", "B03", "B04", "B08", "B11", "B12")
SOIL = ("clay", "sand", "silt", "bdod", "soc")
#: SoilGrids 2.0 stores integers; divide by these to get %, %, %, kg/dm3, g/kg (ISRIC).
SOIL_FACTOR = {"clay": 10, "sand": 10, "silt": 10, "bdod": 100, "soc": 10}
TERRAIN = ("elevation", "slope_deg")
STATIC = SOIL + TERRAIN
SOILGRIDS = "/vsicurl/https://files.isric.org/soilgrids/latest/data/{p}/{p}_0-5cm_mean.vrt"


# ------------------------------------------------------------------- aux
def aux(cube: Path):
    import urllib.request
    import rasterio
    from rasterio.warp import transform as warp
    import fetch_year as fy

    pos = fy.station_positions(2019)
    names = sorted({r["station"] for r in csv.DictReader(open(cube / "samples.csv", encoding="utf-8"))})
    out = {n: {"lonlat": list(pos[n])} for n in names}
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
                      GDAL_HTTP_RETRY_DELAY="3"):
        for p in SOIL:
            with rasterio.open(SOILGRIDS.format(p=p)) as src:
                for n in names:
                    x, y = warp("EPSG:4326", src.crs, [pos[n][0]], [pos[n][1]])
                    r, c = src.index(x[0], y[0])
                    v = src.read(1, window=((r, r + 1), (c, c + 1)))[0, 0].item()
                    out[n][p] = None if v == src.nodata else v / SOIL_FACTOR[p]
            print(f"  SoilGrids {p} done", flush=True)
        token = fy._json("https://planetarycomputer.microsoft.com/api/sas/v1/token/nasadem")["token"]
        for n in names:
            lon, lat = pos[n]
            item = fy._json("https://planetarycomputer.microsoft.com/api/stac/v1/search",
                            {"collections": ["nasadem"], "intersects": {"type": "Point", "coordinates": [lon, lat]},
                             "limit": 1})["features"][0]
            with rasterio.open(item["assets"]["elevation"]["href"] + "?" + token) as src:
                r, c = src.index(lon, lat)
                z = src.read(1, window=((r - 1, r + 2), (c - 1, c + 2))).astype(float)
                dy = abs(src.transform.e) * 111320.0
                dx = abs(src.transform.a) * 111320.0 * math.cos(math.radians(lat))
            gx = ((z[0, 2] + 2 * z[1, 2] + z[2, 2]) - (z[0, 0] + 2 * z[1, 0] + z[2, 0])) / (8 * dx)
            gy = ((z[2, 0] + 2 * z[2, 1] + z[2, 2]) - (z[0, 0] + 2 * z[0, 1] + z[0, 2])) / (8 * dy)
            out[n]["elevation"] = float(z[1, 1])
            out[n]["slope_deg"] = math.degrees(math.atan(math.hypot(gx, gy)))
            out[n]["dem_item"] = item["id"]
    meta = {"soilgrids": "ISRIC SoilGrids 2.0, 0-5 cm mean, CC-BY 4.0; units %, %, %, kg/dm3, g/kg",
            "dem": "NASADEM (NASA/JPL, US government work) via Microsoft Planetary Computer; slope by Horn, 3x3",
            "stations": out}
    (cube / "station_aux.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    for n in names:
        print(n, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out[n].items() if k != "lonlat"})


# ------------------------------------------------------------------- shared
def load(cube: Path):
    rows = list(csv.DictReader(open(cube / "samples.csv", encoding="utf-8")))
    A = json.load(open(cube / "station_aux.json"))["stations"]
    y = np.array([float(r["sm"]) for r in rows])
    stn = np.array([r["station"] for r in rows])
    t = np.array([datetime.fromisoformat(r["s1_time"]) for r in rows])
    grp = np.array([f"{r['station']}-{r['s1_time'][5:7]}" for r in rows])
    orbit = np.array([re.search(r"_rel(\d+)_", r["s1_file"]).group(1) for r in rows])
    Xb = np.array([[float(r[f]) for f in BASE] for r in rows])
    Xs = np.array([[A[s][f] for f in STATIC] for s in stn], dtype=float)
    return rows, y, stn, t, grp, orbit, Xb, Xs


def rf(seed):
    from sklearn.ensemble import RandomForestRegressor
    return RandomForestRegressor(n_estimators=300, random_state=seed,
                                 n_jobs=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)))


def perm_importance(model, X, y, names, seed, repeats=3):
    """Drop in held-out R2 when one feature is shuffled across the test rows."""
    rng = np.random.default_rng(seed)
    base = r2(y, model.predict(X))
    out = {}
    for j, n in enumerate(names):
        drops = []
        for _ in range(repeats):
            Xp = X.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - r2(y, model.predict(Xp)))
        out[n] = float(np.mean(drops))
    return out


def folds(stn, seeds):
    names = sorted(set(stn))
    west = [n for n in names if n not in EAST]
    for seed in range(seeds):
        for k, test_st in enumerate(stratified(west, seed)):
            yield seed, k, np.isin(stn, test_st)


def summarise(runs, arms, metric_keys):
    out = {}
    for a in arms:
        rs = [r for r in runs if r["arm"] == a]
        row = {"folds": len(rs)}
        for m in metric_keys:
            v = [r[m] for r in rs]
            row[m] = {"mean": st.mean(v), "sd": st.stdev(v), "median": st.median(v)}
        if "d_r2" in rs[0]:
            for m in ("d_r2", "d_r2a"):
                v = [r[m] for r in rs]
                row[m] = {"mean": st.mean(v), "sd": st.stdev(v), "median": st.median(v),
                          "positive": sum(x > 0 for x in v)}
        imp = collections.defaultdict(list)
        for r in rs:
            for f, v in r["importance"].items():
                imp[f].append(v)
        row["importance"] = dict(sorted(((f, st.mean(v)) for f, v in imp.items()), key=lambda kv: -kv[1]))
        out[a] = row
    return out


def show(summary, title):
    print(f"\n== {title}")
    for a, s in summary.items():
        line = f"  {a:28s} R2 med {s['r2']['median']:+.3f} (mean {s['r2']['mean']:+.3f})  anomaly R2 med {s['r2a']['median']:+.3f}"
        if "d_r2" in s:
            line += (f"  dR2 {s['d_r2']['mean']:+.3f}+-{s['d_r2']['sd']:.3f} med {s['d_r2']['median']:+.3f} "
                     f"{s['d_r2']['positive']}/{s['folds']}  dR2a {s['d_r2a']['mean']:+.3f}+-{s['d_r2a']['sd']:.3f} "
                     f"med {s['d_r2a']['median']:+.3f} {s['d_r2a']['positive']}/{s['folds']}")
        print(line)
        print("      importance (held-out R2 drop): " +
              ", ".join(f"{f} {v:+.3f}" for f, v in list(s["importance"].items())[:8]))


# ------------------------------------------------------------------- arm A
def arm_a(cube: Path, out: Path, seeds: int):
    rows, y, stn, t, grp, orbit, Xb, Xs = load(cube)
    arms = {"baseline (8 pixel features)": (Xb, BASE),
            "static only (7)": (Xs, STATIC),
            "baseline + static (15)": (np.hstack([Xb, Xs]), BASE + STATIC)}
    runs = []
    for seed, k, te in folds(stn, seeds):
        tr = ~te
        base_r2 = base_r2a = None
        for a, (X, names) in arms.items():
            m = rf(seed).fit(X[tr], y[tr])
            p = m.predict(X[te])
            rec = {"arm": a, "seed": seed, "fold": k, "r2": r2(y[te], p), "r2a": anom_r2(y[te], p, grp[te]),
                   "importance": perm_importance(m, X[te], y[te], names, seed)}
            if base_r2 is None:
                base_r2, base_r2a = rec["r2"], rec["r2a"]
            else:
                rec["d_r2"], rec["d_r2a"] = rec["r2"] - base_r2, rec["r2a"] - base_r2a
            runs.append(rec)
        print(f"  partition {seed} fold {k} done", flush=True)
    S = summarise(runs, list(arms), ("r2", "r2a"))
    show(S, f"ARM A: static auxiliary features ({len(y)} samples, {len(runs) // len(arms)} folds)")
    out.mkdir(parents=True, exist_ok=True)
    (out / "arm_a.json").write_text(json.dumps({"summary": S, "runs": runs}, indent=1), encoding="utf-8")


# ------------------------------------------------------------------- arm B
def pairs(stn, t, orbit, same_orbit: bool):
    """Index pairs (i, j): j is the next S1 pass after i at the same station (same orbit)."""
    out = []
    key = (lambda i: (stn[i], orbit[i])) if same_orbit else (lambda i: stn[i])
    groups = collections.defaultdict(list)
    for i in range(len(stn)):
        groups[key(i)].append(i)
    for g in groups.values():
        g.sort(key=lambda i: t[i])
        out += list(zip(g[:-1], g[1:]))
    return np.array(out)


def arm_b(cube: Path, out: Path, seeds: int):
    rows, y, stn, t, grp, orbit, Xb, Xs = load(cube)
    res = {}
    for label, same in (("same orbit (primary)", True), ("any orbit (secondary)", False)):
        P = pairs(stn, t, orbit, same)
        i, j = P[:, 0], P[:, 1]
        dsm = y[j] - y[i]
        dvv = 10 * np.log10(Xb[j, 0] / Xb[i, 0])
        dvh = 10 * np.log10(Xb[j, 1] / Xb[i, 1])
        dt = np.array([(t[b] - t[a]).total_seconds() / 86400 for a, b in P])
        pst, pgrp = stn[j], grp[j]
        feats = {"change detection: radar only (dVV, dVH, dt)": (np.column_stack([dvv, dvh, dt]), ("dVV_dB", "dVH_dB", "dt_days")),
                 "change detection: radar + S2 at t2": (np.column_stack([dvv, dvh, dt, Xb[j, 2:]]),
                                                        ("dVV_dB", "dVH_dB", "dt_days") + BASE[2:])}
        print(f"\n{label}: {len(P)} pairs; median gap {st.median(dt):.1f} d; sd of dSM {dsm.std():.4f}")
        rvv = {}
        for s in sorted(set(stn)):
            m = pst == s
            rvv[s] = float(np.corrcoef(dvv[m], dsm[m])[0, 1])
        print(f"  r(dVV_dB, dSM) per station: median {st.median(rvv.values()):+.3f}, "
              f"positive at {sum(v > 0 for v in rvv.values())} of {len(rvv)}")
        runs = []
        for seed, k, te_s in folds(stn, seeds):
            te_p = te_s[j]                         # a pair is a test pair if its station is a test station
            tr_p = ~te_p
            m0 = rf(seed).fit(Xb[~te_s], y[~te_s])  # the baseline absolute model, same fold
            implied = m0.predict(Xb[j[te_p]]) - m0.predict(Xb[i[te_p]])
            yt = dsm[te_p]
            recs = [{"arm": "baseline absolute model, implied change p(t2) - p(t1)",
                     "r2": r2(yt, implied), "r2a": anom_r2(yt, implied, pgrp[te_p]), "importance": {}},
                    {"arm": "no change (predict 0)", "r2": r2(yt, np.zeros_like(yt)),
                     "r2a": anom_r2(yt, np.zeros_like(yt), pgrp[te_p]), "importance": {}}]
            for a, (X, names) in feats.items():
                m = rf(seed).fit(X[tr_p], dsm[tr_p])
                p = m.predict(X[te_p])
                recs.append({"arm": a, "r2": r2(yt, p), "r2a": anom_r2(yt, p, pgrp[te_p]),
                             "importance": perm_importance(m, X[te_p], yt, names, seed)})
            for r_ in recs:
                r_.update({"seed": seed, "fold": k})
                if r_["arm"] != recs[0]["arm"]:
                    r_["d_r2"], r_["d_r2a"] = r_["r2"] - recs[0]["r2"], r_["r2a"] - recs[0]["r2a"]
            runs += recs
        arms = [r_["arm"] for r_ in runs[:4]]
        S = summarise(runs, arms, ("r2", "r2a"))
        show(S, f"ARM B: change detection, {label}; target dSM; dR2 against the baseline's implied change")
        res[label] = {"pairs": int(len(P)), "median_gap_days": st.median(dt), "r_dvv_dsm": rvv,
                      "summary": S, "runs": runs}
    out.mkdir(parents=True, exist_ok=True)
    (out / "arm_b.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=("aux", "arm-a", "arm-b"))
    ap.add_argument("--cube", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    cube = a.cube.expanduser()
    if a.stage == "aux":
        aux(cube)
    elif a.stage == "arm-a":
        arm_a(cube, a.out.expanduser(), a.seeds)
    else:
        arm_b(cube, a.out.expanduser(), a.seeds)


if __name__ == "__main__":
    main()
