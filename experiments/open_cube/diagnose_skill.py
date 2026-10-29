"""
Why does the ablation model have no skill? A diagnostic, not a result (docs/open_ablation.md).

    python experiments/open_cube/diagnose_skill.py --cube ~/work/data/cubes/open_cube/2019

Same samples (the all-gates baseline arm), same features, same random forest as
run_ablation.py. The question is whether the model fails because the features carry
no moisture signal, or because what they carry does not transfer between stations.

1. Spatial split, the result: the stratified station blocks, 5 partitions.
2. Within-station temporal split, a diagnostic only: every station is in both train and
   test; folds are interleaved months ({1,5,9}, {2,6,10}, {3,7,11}, {4,8,12}), so every
   fold spans the seasons. Month blocks, not random samples, because soil moisture is
   autocorrelated over days; some leakage across month boundaries remains.
   Compared with predicting each station's own training mean, the level-only model.
3. Signal at the pixel: per-station correlation of S1 VV (dB) with soil moisture, over
   all passes and within one relative orbit (incidence angle held fixed).
The spatial split stays the result. Nothing here replaces it.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import EAST, stratified        # noqa: E402
from eocube.baselines import make_random_forest   # noqa: E402
from run_ablation import BASELINE, anom_r2, arms, load, r2   # noqa: E402

MONTH_FOLDS = [(1, 5, 9), (2, 6, 10), (3, 7, 11), (4, 8, 12)]


def per_station(y, p, stn, grp, mask):
    """Median over stations of within-station R2 (absolute) and anomaly R2."""
    a, b = [], []
    for s in sorted(set(stn[mask])):
        m = mask & (stn == s)
        if m.sum() > 10:
            a.append(r2(y[m], p[m]))
            b.append(anom_r2(y[m], p[m], grp[m]))
    return st.median(a), st.median(b)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cube", type=Path, required=True)
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    cube = a.cube.expanduser()
    X, y, grp, stn, verdicts, decisions = load(cube)
    keep = arms(verdicts, decisions)[BASELINE][1]
    X, y, grp, stn = X[keep], y[keep], grp[keep], stn[keep]
    month = np.array([int(g.rsplit("-", 1)[1]) for g in grp])
    rows = [r for r, k in zip(csv.DictReader(open(cube / "samples.csv", encoding="utf-8")), keep) if k]
    orbit = np.array([re.search(r"_rel(\d+)_", r["s1_file"]).group(1) for r in rows])
    fit = make_random_forest(n_estimators=300, n_jobs=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)))
    names = sorted(set(stn))
    west = [n for n in names if n not in EAST]
    print(f"{len(y)} samples (all-gates baseline arm), {len(names)} stations")

    print("\n== 1. spatial split (the result): stratified station blocks")
    ab, an, nul = [], [], []
    for seed in range(a.seeds):
        for test_st in stratified(west, seed):
            te = np.isin(stn, test_st)
            p = fit(X[~te], y[~te], X[te], seed)
            ab.append(r2(y[te], p)); an.append(anom_r2(y[te], p, grp[te]))
            nul.append(r2(y[te], np.full(te.sum(), y[~te].mean())))
    print(f"  RF absolute R2 median {st.median(ab):.3f} (range {min(ab):.2f}..{max(ab):.2f}); "
          f"anomaly R2 median {st.median(an):.3f}; training-mean predictor median {st.median(nul):.3f}")

    print("\n== 2. within-station temporal split (DIAGNOSTIC ONLY): interleaved month folds")
    pred_rf, pred_lvl = np.full(len(y), np.nan), np.full(len(y), np.nan)
    fold_rows = []
    for k, months in enumerate(MONTH_FOLDS):
        te = np.isin(month, months)
        pred_rf[te] = fit(X[~te], y[~te], X[te], 0)
        means = {s: y[~te & (stn == s)].mean() for s in names}
        pred_lvl[te] = [means[s] for s in stn[te]]
        fold_rows.append((months, int(te.sum()), r2(y[te], pred_rf[te]), r2(y[te], pred_lvl[te]),
                          anom_r2(y[te], pred_rf[te], grp[te])))
    for months, n, rf, lvl, an_ in fold_rows:
        print(f"  months {months}: n {n:4d}  RF R2 {rf:6.3f}  station-mean R2 {lvl:6.3f}  "
              f"RF anomaly R2 {an_:6.3f}")
    allm = np.ones(len(y), bool)
    print(f"  pooled: RF absolute R2 {r2(y, pred_rf):.3f}; station-mean predictor {r2(y, pred_lvl):.3f}; "
          f"RF anomaly R2 {anom_r2(y, pred_rf, grp):.3f}")
    ps_rf = per_station(y, pred_rf, stn, grp, allm)
    ps_lvl = per_station(y, pred_lvl, stn, grp, allm)
    print(f"  within one station (median over stations): RF R2 {ps_rf[0]:.3f}, anomaly {ps_rf[1]:.3f}; "
          f"station-mean predictor R2 {ps_lvl[0]:.3f}")

    print("\n== 3. signal at the pixel: Pearson r of S1 VV (dB) with soil moisture, per station")
    vv_db = 10 * np.log10(X[:, 0])
    pooled, within = [], collections.defaultdict(list)
    for s in names:
        m = stn == s
        pooled.append(np.corrcoef(vv_db[m], y[m])[0, 1])
        for o in sorted(set(orbit[m])):
            mo = m & (orbit == o)
            if mo.sum() >= 15:
                within[o].append(np.corrcoef(vv_db[mo], y[mo])[0, 1])
    print(f"  all passes: median r {st.median(pooled):.2f} (range {min(pooled):.2f}..{max(pooled):.2f}; "
          f"{sum(r > 0 for r in pooled)} of {len(pooled)} positive)")
    for o, rs in sorted(within.items()):
        print(f"  relative orbit {o}: median r {st.median(rs):.2f} over {len(rs)} stations "
              f"({sum(r > 0 for r in rs)} positive)")
    allw = [r for rs in within.values() for r in rs] or [float("nan")]
    print(f"  within one orbit, all station-orbits: median r {st.median(allw):.2f} "
          f"({sum(r > 0 for r in allw)} of {len(allw)} positive)")
    mean_vv = {o: float(np.mean(vv_db[orbit == o])) for o in sorted(set(orbit))}
    print(f"  mean VV by orbit (dB): " + ", ".join(f"{o}: {v:.2f}" for o, v in mean_vv.items()))


if __name__ == "__main__":
    main()
