"""
Window size around the station pixel (PLAN.md, "Window size (open question 1)";
docs/window_size.md).

    python experiments/open_cube/window_size.py extract --raw ~/work/data/raw/open_cube/2019 \
        --cube ~/work/data/cubes/open_cube/2019
    python experiments/open_cube/window_size.py evaluate --cube ~/work/data/cubes/open_cube/2019 \
        --out ~/work/results/open_cube/window_2019

An extension of the 2019 study; the 1 x 1 point-mode ablation remains the main result.

extract   Reads a 9 x 9 window around the station pixel from every sample's chips (S1 on
          its own 10 m grid; S2 on the 10 m grid of B02, with B11, B12 and SCL read at
          the 10 m cell centres) and writes windows9.npz beside the cube: values,
          per-pixel validity by the cube's own rules, and SCL. Reads chips: Slurm only.
evaluate  For windows 1, 3, 5, 9 and the design fixed in PLAN.md before any result:
          - validity: centre valid, window mean over valid pixels (identical samples at
            every size); the cost of "all valid" and ">= 50% valid" is reported;
          - average-then-calculate (AtC), with S1 and S2 windowed, S1 only, or S2 only;
          - calculate-then-average (CtA): the 1 x 1 model predicts each valid pixel
            pair, and the predictions are averaged;
          - r(VV dB, soil moisture) per station; R2 absolute and anomaly on the
            stratified station folds; paired dR2 against 1 x 1; per-station results;
          - the chip-level quality criteria applied to each window, against the chip's
            own verdicts.
          S1 is averaged in linear power, never in dB.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import EAST, stratified        # noqa: E402
from run_ablation import anom_r2, r2       # noqa: E402

K_MAX = 9
SIZES = (1, 3, 5, 9)
H = K_MAX // 2
FEATURES = ("vv", "vh", "B02", "B03", "B04", "B08", "B11", "B12")
S2_10M, S2_20M = ("B02", "B03", "B04", "B08"), ("B11", "B12")
SCL_USABLE, B02_FLOOR, S1_NODATA, S2_CEILING = (4, 5), 50, -32768.0, 12000
DETECTION_LIMIT = 0.05


# ------------------------------------------------------------------- extract
def _rowcol(t, x, y):
    """Row and column containing (x, y) for a north-up affine transform; arrays allowed."""
    return (np.floor((np.asarray(y) - t.f) / t.e).astype(int),
            np.floor((np.asarray(x) - t.c) / t.a).astype(int))


def _take(a, rows, cols):
    """a[rows, cols] with NaN outside the array."""
    inside = (rows >= 0) & (rows < a.shape[0]) & (cols >= 0) & (cols < a.shape[1])
    out = np.full(rows.shape, np.nan)
    out[inside] = a[rows[inside], cols[inside]]
    return out


def extract(raw: Path, cube: Path, year: int):
    import rasterio
    from rasterio.warp import transform as warp
    from bypass_cube import build

    samples, _, lonlat = build(raw, None, year)
    by_key = {(r["station"], r["s1_time"]): (r, pol, f2) for r, pol, f2 in samples}
    rows = list(csv.DictReader(open(cube / "samples.csv", encoding="utf-8")))
    cache, pts = {}, {}

    def chip(path):
        if path not in cache:
            with rasterio.open(path) as src:
                cache[path] = (src.read(1).astype("float64"), src.transform, str(src.crs), src.nodata)
        return cache[path]

    def point(stn, crs):
        if (stn, crs) not in pts:
            x, y = warp("EPSG:4326", crs, [lonlat[stn][0]], [lonlat[stn][1]])
            pts[(stn, crs)] = (x[0], y[0])
        return pts[(stn, crs)]

    n = len(rows)
    X = np.full((n, K_MAX, K_MAX, len(FEATURES)), np.nan, dtype="float32")
    s1v = np.zeros((n, K_MAX, K_MAX), bool)
    s2v = np.zeros((n, K_MAX, K_MAX), bool)
    scl_w = np.zeros((n, K_MAX, K_MAX), "int16")
    off = np.arange(-H, H + 1)
    for i, row in enumerate(rows):
        _, pol, f2 = by_key[(row["station"], row["s1_time"])]
        stn = row["station"]
        ok = np.ones((K_MAX, K_MAX), bool)
        for j, b in enumerate(("VV", "VH")):
            a, t, crs, nd = chip(pol[b])
            r0, c0 = _rowcol(t, *point(stn, crs))
            v = _take(a, r0 + off[:, None] + 0 * off[None, :], c0 + 0 * off[:, None] + off[None, :])
            X[i, :, :, j] = v
            ok &= np.isfinite(v) & (v > 0) & (v != (S1_NODATA if nd is None else nd))
        s1v[i] = ok
        a, t, crs, _ = chip(f2["B02"])
        r0, c0 = _rowcol(t, *point(stn, crs))
        rr, cc = r0 + off[:, None] + 0 * off[None, :], c0 + 0 * off[:, None] + off[None, :]
        xs, ys = t.c + (cc + 0.5) * t.a, t.f + (rr + 0.5) * t.e          # 10 m cell centres
        for j, b in enumerate(S2_10M, start=2):
            X[i, :, :, j] = _take(chip(f2[b])[0], rr, cc)
        for j, b in enumerate(S2_20M, start=6):
            a20, t20, _, _ = chip(f2[b])
            X[i, :, :, j] = _take(a20, *_rowcol(t20, xs, ys))
        a20, t20, _, _ = chip(f2["SCL"])
        scl = _take(a20, *_rowcol(t20, xs, ys))
        scl_w[i] = np.nan_to_num(scl, nan=-1).astype("int16")
        b02 = X[i, :, :, 2]
        s2v[i] = np.isin(scl, SCL_USABLE) & np.isfinite(b02) & (b02 >= B02_FLOOR)
        if i % 500 == 0:
            print(f"  {i}/{n}", flush=True)
    keys = np.array([f"{r['station']}|{r['s1_time']}" for r in rows])
    np.savez_compressed(cube / "windows9.npz", X=X, s1_valid=s1v, s2_valid=s2v, scl=scl_w, keys=keys)
    print(f"windows9.npz: {n} samples; centre valid S1 {int(s1v[:, H, H].sum())}, "
          f"S2 {int(s2v[:, H, H].sum())}; {len(cache)} chips read")


# ------------------------------------------------------------------- evaluate
def sub(k):
    lo = H - k // 2
    return slice(lo, lo + k)


def window_mean(X, valid, k, cols):
    """Mean of X[..., cols] over the valid pixels of the k x k window (centre always valid)."""
    s = sub(k)
    v = valid[:, s, s][..., None]
    x = np.where(v, X[:, s, s][..., cols], 0.0)
    return x.sum(axis=(1, 2)) / v.sum(axis=(1, 2))


def features(X, s1v, s2v, k, which):
    k1 = k if which in ("both", "s1") else 1
    k2 = k if which in ("both", "s2") else 1
    return np.hstack([window_mean(X, s1v, k1, slice(0, 2)), window_mean(X, s2v, k2, slice(2, 8))])


def evaluate(cube: Path, out: Path, seeds: int):
    from eocube.baselines import make_random_forest
    out.mkdir(parents=True, exist_ok=True)
    W = np.load(cube / "windows9.npz")
    X, s1v, s2v = W["X"].astype("float64"), W["s1_valid"], W["s2_valid"]
    rows = list(csv.DictReader(open(cube / "samples.csv", encoding="utf-8")))
    assert [f"{r['station']}|{r['s1_time']}" for r in rows] == list(W["keys"])
    per = json.load(open(cube / "gates.json"))["per_sample"]
    y = np.array([float(r["sm"]) for r in rows])
    stn = np.array([r["station"] for r in rows])
    grp = np.array([f"{r['station']}-{r['s1_time'][5:7]}" for r in rows])
    orbit = np.array([re.search(r"_rel(\d+)_", r["s1_file"]).group(1) for r in rows])
    centre_ok = s1v[:, H, H] & s2v[:, H, H]
    print(f"{len(y)} samples; centre valid in both sensors: {int(centre_ok.sum())}")
    res = {"n": len(y), "centre_valid": int(centre_ok.sum()), "sizes": SIZES}

    # validity rules: what each costs
    rules = {}
    for k in SIZES:
        s = sub(k)
        f1, f2 = s1v[:, s, s].mean(axis=(1, 2)), s2v[:, s, s].mean(axis=(1, 2))
        rules[k] = {"centre_valid": int(centre_ok.sum()),
                    "all_valid": int(((f1 == 1) & (f2 == 1)).sum()),
                    "valid_fraction_ge_0.5": int(((f1 >= 0.5) & (f2 >= 0.5) & centre_ok).sum()),
                    "mean_valid_fraction_s1": float(f1.mean()), "mean_valid_fraction_s2": float(f2.mean())}
    res["validity_rules"] = rules
    print("\nvalidity rules, samples retained of", len(y))
    for k, v in rules.items():
        print(f"  {k}x{k}: centre {v['centre_valid']}, all valid {v['all_valid']}, >=50% "
              f"{v['valid_fraction_ge_0.5']}; mean valid fraction S1 {v['mean_valid_fraction_s1']:.3f} "
              f"S2 {v['mean_valid_fraction_s2']:.3f}")

    # r(VV dB, SM) per station at each window (S1 window mean, linear -> dB)
    rdiag = {}
    for k in SIZES:
        vv = 10 * np.log10(window_mean(X, s1v, k, slice(0, 1))[:, 0])
        pooled, within = {}, []
        for sname in sorted(set(stn)):
            m = stn == sname
            pooled[sname] = float(np.corrcoef(vv[m], y[m])[0, 1])
            for o in set(orbit[m]):
                mo = m & (orbit == o)
                if mo.sum() >= 15:
                    within.append(float(np.corrcoef(vv[mo], y[mo])[0, 1]))
        rdiag[k] = {"per_station": pooled, "median": st.median(pooled.values()),
                    "median_within_orbit": st.median(within) if within else float("nan"),
                    "n_within": len(within)}
    res["r_vv_sm"] = rdiag
    print("\nr(VV dB, SM), median over stations: " + ", ".join(
        f"{k}x{k} {v['median']:.3f} (within orbit {v['median_within_orbit']:.3f})" for k, v in rdiag.items()))

    # heterogeneity and speckle around each station (descriptive)
    s = sub(K_MAX)
    ndvi = (X[:, s, s, 5] - X[:, s, s, 4]) / (X[:, s, s, 5] + X[:, s, s, 4])
    ndvi_sd = np.array([np.nanstd(np.where(s2v[i], ndvi[i], np.nan)) for i in range(len(y))])
    vv9 = np.where(s1v, X[..., 0], np.nan)
    vv_cv = np.nanstd(vv9, axis=(1, 2)) / np.nanmean(vv9, axis=(1, 2))
    res["station_context"] = {sname: {"ndvi_sd_9x9_median": float(np.nanmedian(ndvi_sd[stn == sname])),
                                      "vv_cv_9x9_median": float(np.nanmedian(vv_cv[stn == sname])),
                                      "east": sname in EAST} for sname in sorted(set(stn))}

    # models
    fit = make_random_forest(n_estimators=300, n_jobs=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)))
    names = sorted(set(stn))
    west = [x for x in names if x not in EAST]
    all9 = (s1v.all(axis=(1, 2)) & s2v.all(axis=(1, 2)))
    sets = {"centre_rule": np.ones(len(y), bool), "all_valid_9x9": all9}
    for name in list(sets):                  # every fold needs training and test samples
        if any(not (sets[name] & np.isin(stn, b)).any() for b in stratified(west, 0)) or sets[name].sum() < 200:
            print(f"  {name}: {int(sets[name].sum())} samples, too few for 4 station folds; not evaluated")
            res[f"{name}_not_evaluated"] = int(sets[name].sum())
            del sets[name]
    F = {(k, w): features(X, s1v, s2v, k, w) for k in SIZES for w in ("both", "s1", "s2")}
    arms = [("AtC", "both"), ("AtC", "s1"), ("AtC", "s2"), ("CtA", "both")]
    runs = []
    oof = collections.defaultdict(lambda: np.full((seeds, len(y)), np.nan))
    for set_name, keep in sets.items():
        for seed in range(seeds):
            for k_fold, test_st in enumerate(stratified(west, seed)):
                te = np.isin(stn, test_st) & keep
                tr = ~np.isin(stn, test_st) & keep
                base = fit(F[(1, "both")][tr], y[tr], F[(1, "both")][te], seed)
                oof[(set_name, 1, "base")][seed, te] = base
                rec0 = {"set": set_name, "seed": seed, "fold": k_fold, "n_test": int(te.sum()),
                        "r2_1": r2(y[te], base), "r2a_1": anom_r2(y[te], base, grp[te])}
                for k in SIZES[1:]:
                    for order, which in arms:
                        if order == "AtC":
                            p = fit(F[(k, which)][tr], y[tr], F[(k, which)][te], seed)
                        else:
                            p = cta_predict(fit, X, s1v, s2v, F[(1, "both")][tr], y[tr], te, k, seed)
                        oof[(set_name, k, f"{order}-{which}")][seed, te] = p
                        runs.append({**rec0, "k": k, "arm": f"{order}-{which}",
                                     "r2": r2(y[te], p), "r2a": anom_r2(y[te], p, grp[te]),
                                     "d_r2": r2(y[te], p) - rec0["r2_1"],
                                     "d_r2a": anom_r2(y[te], p, grp[te]) - rec0["r2a_1"]})
            print(f"  {set_name}: partition {seed} done", flush=True)
    res["runs"] = runs

    summary = []
    for set_name, keep in sets.items():
        b = [r for r in runs if r["set"] == set_name and r["k"] == 3 and r["arm"] == "AtC-both"]
        summary.append({"set": set_name, "n": int(keep.sum()), "k": 1, "arm": "baseline",
                        "r2_median": st.median(r["r2_1"] for r in b), "r2_mean": st.mean(r["r2_1"] for r in b),
                        "r2a_median": st.median(r["r2a_1"] for r in b)})
        for k in SIZES[1:]:
            for order, which in arms:
                rs = [r for r in runs if r["set"] == set_name and r["k"] == k and r["arm"] == f"{order}-{which}"]
                d, da = [r["d_r2"] for r in rs], [r["d_r2a"] for r in rs]
                summary.append({"set": set_name, "n": int(keep.sum()), "k": k, "arm": f"{order}-{which}",
                                "r2_median": st.median(r["r2"] for r in rs), "r2_mean": st.mean(r["r2"] for r in rs),
                                "r2a_median": st.median(r["r2a"] for r in rs),
                                "d_r2_mean": st.mean(d), "d_r2_sd": st.stdev(d), "d_r2_median": st.median(d),
                                "d_r2_pos": sum(x > 0 for x in d),
                                "d_r2a_mean": st.mean(da), "d_r2a_sd": st.stdev(da), "d_r2a_median": st.median(da),
                                "d_r2a_pos": sum(x > 0 for x in da), "folds": len(d)})
    res["summary"] = summary
    print("\nset            k  arm        R2 median  dR2 abs (mean+-sd, median, +folds)     dR2 anomaly")
    for s_ in summary:
        if s_["arm"] == "baseline":
            print(f"{s_['set']:14s} 1  baseline   {s_['r2_median']:+.3f}   (n={s_['n']}, anomaly R2 median "
                  f"{s_['r2a_median']:+.3f})")
            continue
        print(f"{s_['set']:14s} {s_['k']}  {s_['arm']:9s}  {s_['r2_median']:+.3f}    "
              f"{s_['d_r2_mean']:+.3f}+-{s_['d_r2_sd']:.3f} med {s_['d_r2_median']:+.3f} {s_['d_r2_pos']:2d}/{s_['folds']}   "
              f"{s_['d_r2a_mean']:+.3f}+-{s_['d_r2a_sd']:.3f} med {s_['d_r2a_median']:+.3f} {s_['d_r2a_pos']:2d}/{s_['folds']}")

    # per station: within-station R2 of out-of-fold predictions, mean over partitions
    perst = {}
    for sname in names:
        m = stn == sname
        row = {}
        for (set_name, k, arm), P in oof.items():
            if set_name != "centre_rule":
                continue
            vals = [r2(y[m], P[s_, m]) for s_ in range(seeds) if np.isfinite(P[s_, m]).all()]
            row[f"{k}:{arm}"] = st.mean(vals) if vals else None
        perst[sname] = row
    res["per_station_r2"] = perst
    print("\nper station (centre rule): r(VV,SM) 1x1 -> 9x9, within-station R2 1x1 -> AtC-both 9x9, "
          "NDVI sd 9x9, VV CV 9x9")
    for sname in names:
        c = res["station_context"][sname]
        print(f"  {sname}{'E' if sname in EAST else ' '} r {rdiag[1]['per_station'][sname]:+.3f} -> "
              f"{rdiag[9]['per_station'][sname]:+.3f}   R2 {perst[sname]['1:base']:+.3f} -> "
              f"{perst[sname]['9:AtC-both']:+.3f} (S1-only {perst[sname]['9:AtC-s1']:+.3f}, "
              f"S2-only {perst[sname]['9:AtC-s2']:+.3f})   NDVI sd {c['ndvi_sd_9x9_median']:.3f}   "
              f"VV CV {c['vv_cv_9x9_median']:.3f}")

    res["gate_convergence"] = gate_convergence(X, s1v, s2v, per)
    print("\nchip-level quality criteria applied to the window, against the chip's own verdict:")
    for k, v in res["gate_convergence"].items():
        print(f"  {k}x{k} ({v['share_of_chip']:.2%} of the chip): window refuses {v['window_refuse']}; "
              f"chip refuses {v['chip_refuse']}; both {v['both']}, chip only {v['chip_only']}, "
              f"window only {v['window_only']}")
    (out / "window_size.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")


def cta_predict(fit, X, s1v, s2v, Xtr, ytr, te, k, seed):
    """Calculate-then-average: the 1 x 1 model predicts each valid pixel pair, then mean."""
    s = sub(k)
    idx = np.nonzero(te)[0]
    pix = X[idx][:, s, s, :].reshape(len(idx), k * k, len(FEATURES))
    ok = (s1v[idx][:, s, s] & s2v[idx][:, s, s]).reshape(len(idx), k * k)
    flat = pix[ok]
    pred = fit(Xtr, ytr, flat, seed)
    out = np.zeros(len(idx))
    owner = np.repeat(np.arange(len(idx)), ok.sum(axis=1))
    np.add.at(out, owner, pred)
    return out / ok.sum(axis=1)


def gate_convergence(X, s1v, s2v, per):
    """The quality gate's chip criteria, applied to each k x k window instead of the chip.

    S2: refuse if more than 1% of non-nodata pixels lie outside the declared ranges (B02
    [50, 12000], other bands [0, 12000]). S1: refuse if more than 30% of pixels are
    nodata. The chip verdict is build_cube's chip-level quality_missingness.
    """
    chip = np.array([p["quality_missingness"] == "refuse" for p in per])
    out = {}
    for k in SIZES:
        s = sub(k)
        b = X[:, s, s, 2:8]
        present = np.isfinite(b[..., 0]) & (b[..., 0] != 0)
        bad = present & ((b[..., 0] < B02_FLOOR) | (b > S2_CEILING).any(axis=-1) | (b < 0).any(axis=-1))
        implausible = bad.sum(axis=(1, 2)) / np.maximum(present.sum(axis=(1, 2)), 1)
        s1_nodata = 1 - s1v[:, s, s].mean(axis=(1, 2))
        win = (implausible > 0.01) | (s1_nodata > 0.30)
        out[k] = {"share_of_chip": k * k / 10000, "window_refuse": int(win.sum()), "chip_refuse": int(chip.sum()),
                  "both": int((win & chip).sum()), "chip_only": int((chip & ~win).sum()),
                  "window_only": int((win & ~chip).sum())}
    return out


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=("extract", "evaluate"))
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--cube", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    if a.stage == "extract":
        extract(a.raw.expanduser(), a.cube.expanduser(), a.year)
    else:
        evaluate(a.cube.expanduser(), a.out.expanduser(), a.seeds)


if __name__ == "__main__":
    main()
