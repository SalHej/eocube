"""
Leave-one-gate-out ablation on a built open cube (docs/open_ablation.md).

    python experiments/open_cube/run_ablation.py --cube ~/work/data/cubes/open_cube/2019 \
        --out ~/work/results/open_cube/ablation_2019 --seeds 5

Reads build_cube.py's samples.csv and gates.json; no imagery. The gates have already
run on every sample's real layers, so an arm is a filter on those recorded verdicts:

- baseline: a sample is kept unless any of the five gates refused it;
- "without <gate>": refusals by that gate are ignored, every other gate still applies.

Geometry is judged in point mode (PLAN.md, "Geometry mode"). The grid-mode verdict is
run as a supplementary arm, to show what the design not chosen would have cost.

Folds are the stratified spatial blocks (PLAN.md, "Block design"): 4 folds, one eastern
station in each, the partition varying with the seed. Never a random split. The model
is fixed across arms: a random forest on the eight station-pixel features (S1 VV, VH in
linear power; S2 B02-B12 in DN). It is invariant to monotonic rescaling, so the dB
question (PLAN.md, open question 3) does not arise.

Each fold is scored twice:
- own:    on the test samples the arm accepts, which is what that pipeline would report;
- paired: on the test samples both the arm and the baseline accept, with both models
          predicting the same targets. Paired dR2 = arm - baseline on that set. This is
          the primary result (PLAN.md, "Power check", decision 2). Negative means
          removing the gate hurt, i.e. the gate was doing useful work.
Both on the absolute target and on anomalies: target and prediction each minus their
own station-month mean over the scored samples. That scores whether the model tracks
moisture changes within a station and month, separately from soil type and season
(PLAN.md, "Power check", decision 3). Evaluation only, never a model input. Subtracting
one climatology from both target and prediction instead would leave the residual
unchanged and only shrink the denominator: the first run did that, and reported
anomaly R2 of -22.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import statistics as st
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import EAST, stratified        # noqa: E402
from eocube.baselines import make_random_forest   # noqa: E402

FEATURES = ("vv", "vh", "B02", "B03", "B04", "B08", "B11", "B12")
#: The five gates, and the per-sample verdict each is read from. Point mode for geometry.
GATES = {"geometry_crs": "geometry_crs[point]", "resolution": "resolution",
         "temporal_alignment": "temporal_alignment", "quality_missingness": "quality_missingness",
         "sensor_physics": "sensor_physics"}
BASELINE = "<all gates active>"
GRID = "geometry in grid mode (not selected)"
DETECTION_LIMIT = 0.05


def r2(y, p) -> float:
    ss = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - float(np.sum((y - p) ** 2)) / ss if ss > 0 else float("nan")


def anom_r2(y, p, groups) -> float:
    """R2 after removing each station-month mean from target and prediction separately."""
    ya, pa = y.copy(), p.copy()
    for g in np.unique(groups):
        m = groups == g
        ya[m] -= y[m].mean()
        pa[m] -= p[m].mean()
    return r2(ya, pa)


def load(cube: Path):
    rows = list(csv.DictReader(open(cube / "samples.csv", encoding="utf-8")))
    per = json.load(open(cube / "gates.json"))["per_sample"]
    assert [(r["station"], r["s1_time"]) for r in rows] == [(p["station"], p["s1_time"]) for p in per]
    X = np.array([[float(r[f]) for f in FEATURES] for r in rows])
    y = np.array([float(r["sm"]) for r in rows])
    stn = np.array([r["station"] for r in rows])
    sm_group = np.array([f"{r['station']}-{datetime.fromisoformat(r['s1_time']).month:02d}"
                         for r in rows])
    refused = {g: np.array([p[k] == "refuse" for p in per]) for g, k in GATES.items()}
    refused_grid = np.array([p["geometry_crs"] == "refuse" for p in per])
    return X, y, sm_group, stn, refused, refused_grid


def arms(refused, refused_grid) -> dict[str, tuple[str | None, np.ndarray]]:
    """{arm: (gate removed, kept mask)}."""
    def keep(skip=(), override=None):
        r = dict(refused, **(override or {}))
        return ~np.any([v for g, v in r.items() if g not in skip], axis=0)
    out = {BASELINE: (None, keep())}
    for g in GATES:
        out[f"without {g}"] = (g, keep(skip=(g,)))
    out[GRID] = (None, keep(override={"geometry_crs": refused_grid}))
    return out


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cube", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    out = a.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    X, y, grp, stn, refused, refused_grid = load(a.cube.expanduser())
    A = arms(refused, refused_grid)
    names = sorted(set(stn))
    west = [n for n in names if n not in EAST]
    fit_predict = make_random_forest(n_estimators=300, n_jobs=int(os.environ.get("SLURM_CPUS_PER_TASK", 1)))
    print(f"{len(y)} samples, {len(names)} stations, features {FEATURES}")

    folds, runs = [], []
    t0 = time.perf_counter()
    for seed in range(a.seeds):
        blocks = stratified(west, seed)
        folds.append({"seed": seed, "blocks": blocks})
        for k, test_stations in enumerate(blocks):
            test = np.isin(stn, test_stations)
            pred = {}
            for arm, (_, kept) in A.items():
                tr = kept & ~test
                pred[arm] = np.full(len(y), np.nan)
                pred[arm][test] = fit_predict(X[tr], y[tr], X[test], seed)
            null = np.full(len(y), y[A[BASELINE][1] & ~test].mean())
            base_keep = A[BASELINE][1]
            for arm, (_, kept) in A.items():
                own, pair = kept & test, kept & base_keep & test
                rec = {"arm": arm, "seed": seed, "fold": k, "test_stations": test_stations,
                       "n_train": int((kept & ~test).sum()), "n_test_own": int(own.sum()),
                       "n_test_paired": int(pair.sum()),
                       "r2_own": r2(y[own], pred[arm][own]),
                       "r2_anom_own": anom_r2(y[own], pred[arm][own], grp[own]),
                       "r2_paired": r2(y[pair], pred[arm][pair]),
                       "r2_anom_paired": anom_r2(y[pair], pred[arm][pair], grp[pair]),
                       "r2_paired_baseline": r2(y[pair], pred[BASELINE][pair]),
                       "r2_anom_paired_baseline": anom_r2(y[pair], pred[BASELINE][pair], grp[pair]),
                       "r2_null": r2(y[pair], null[pair]),
                       "per_station": {}}
                rec["d_r2"] = rec["r2_paired"] - rec["r2_paired_baseline"]
                rec["d_r2_anom"] = rec["r2_anom_paired"] - rec["r2_anom_paired_baseline"]
                for s in test_stations:
                    m = pair & (stn == s)
                    if m.sum() > 2:
                        rec["per_station"][s] = r2(y[m], pred[arm][m]) - r2(y[m], pred[BASELINE][m])
                runs.append(rec)
        print(f"seed {seed} done ({time.perf_counter() - t0:.0f} s)")

    summary = []
    for arm, (gate, kept) in A.items():
        rs = [r for r in runs if r["arm"] == arm]
        d, da = [r["d_r2"] for r in rs], [r["d_r2_anom"] for r in rs]
        seed_d = [st.mean(r["d_r2"] for r in rs if r["seed"] == s) for s in range(a.seeds)]
        east = [v for r in rs for s, v in r["per_station"].items() if s in EAST]
        rest = [v for r in rs for s, v in r["per_station"].items() if s not in EAST]
        n_ref = int((~kept).sum())
        summary.append({
            "arm": arm, "gate_removed": gate, "samples_kept": int(kept.sum()),
            "samples_refused": n_ref, "refusal_rate": n_ref / len(y),
            "refused_by_station": dict(collections.Counter(stn[~kept].tolist())),
            "identical_to_baseline": bool(np.array_equal(kept, A[BASELINE][1])),
            "r2_own_mean": st.mean(r["r2_own"] for r in rs), "r2_own_sd": st.stdev(r["r2_own"] for r in rs),
            "r2_anom_own_mean": st.mean(r["r2_anom_own"] for r in rs),
            "d_r2_mean": st.mean(d), "d_r2_sd": st.stdev(d), "d_r2_min": min(d), "d_r2_max": max(d),
            "d_r2_seed_means": seed_d,
            "d_r2_anom_mean": st.mean(da), "d_r2_anom_sd": st.stdev(da),
            "d_r2_anom_min": min(da), "d_r2_anom_max": max(da),
            "r2_null_mean": st.mean(r["r2_null"] for r in rs),
            "d_r2_station_east_mean": st.mean(east) if east else None,
            "d_r2_station_rest_mean": st.mean(rest) if rest else None,
            "below_detection_limit": abs(st.mean(d)) < DETECTION_LIMIT and abs(st.mean(da)) < DETECTION_LIMIT})
    result = {"cube": str(a.cube), "seeds": a.seeds, "features": FEATURES,
              "model": "RandomForestRegressor(n_estimators=300)", "geometry_mode": "point",
              "detection_limit_d_r2": DETECTION_LIMIT, "n_samples": len(y), "folds": folds,
              "summary": summary, "runs": runs}
    (out / "ablation.json").write_text(json.dumps(result, indent=1), encoding="utf-8")

    print(f"\n{'arm':40s} {'kept':>5} {'refused':>8} {'R2 own':>13} {'dR2 paired':>17} "
          f"{'dR2 anomaly':>17}  verdict")
    for s in summary:
        verdict = ("baseline" if s["arm"] == BASELINE else
                   "identical to baseline" if s["identical_to_baseline"] else
                   "below detection limit on this task" if s["below_detection_limit"] else "detectable")
        print(f"{s['arm']:40s} {s['samples_kept']:5d} {s['refusal_rate']:7.1%} "
              f"{s['r2_own_mean']:6.3f}±{s['r2_own_sd']:.3f} "
              f"{s['d_r2_mean']:+7.3f}±{s['d_r2_sd']:.3f} {s['d_r2_anom_mean']:+7.3f}±{s['d_r2_anom_sd']:.3f}"
              f"  {verdict}")
    b = summary[0]
    print(f"\nbaseline anomaly R2 (own) {b['r2_anom_own_mean']:.3f}; null (training mean) R2 "
          f"{b['r2_null_mean']:.3f}; {len(runs) // len(A)} folds x seeds per arm")


if __name__ == "__main__":
    main()
