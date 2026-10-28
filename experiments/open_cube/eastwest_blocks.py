"""
East/west Sentinel-2 coverage and the spatial-block design (PLAN.md, "Block design").

    python experiments/open_cube/eastwest_blocks.py --manifest <year>/manifest.jsonl

Run in the directory holding sm_stats.py's sm_series_*.json files (power_check.py
reads them at import). Uses only metadata: the fetch manifest, not imagery.

1. For every S1 pass at every station, the age of the most recent usable S2
   observation at or before it (availability only; cloud is NOT applied).
2. For three designs (plain spatial k=4 blocks, drop the low-coverage stations,
   stratify one of them into each fold), the fold sizes, the spread of mean S2 age
   across folds (the confound), and the distance from each test station to its
   nearest training station (spatial independence).
3. The no-satellite power proxy (paired dR2 for a 5-day-stale predictor) under each.
"""

import argparse
import collections
import json
import math
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import power_check as pc  # noqa: E402  (loads the SM series and prints its own summary)

#: Stations one S2 orbit's swath does not reach (71-72 of 143 dates in 2019).
EAST = ["ITCSM_01", "ITCSM_04", "ITCSM_07", "ITCSM_09"]


def s1_to_s2_age(manifest: Path) -> dict[str, list[float]]:
    recs = [json.loads(l) for l in open(manifest, encoding="utf-8")]
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    s1, s2 = collections.defaultdict(list), collections.defaultdict(list)
    for r in recs:
        if r["status"] != "ok":
            continue
        if r["kind"] == "s1" and r["file"].endswith("_VV.tif"):
            s1[r["station"]].append(ts(r["datetime"]))
        if r["kind"] == "s2" and r["file"].endswith("_SCL.tif") and r.get("station_measured", True):
            s2[r["station"]].append(ts(r["datetime"]))
    gaps = {}
    for stn in sorted(s1):
        dates = sorted(s2[stn])
        g = [(t - prior[-1]).total_seconds() / 86400
             for t in s1[stn] if (prior := [x for x in dates if x <= t])]
        gaps[stn] = g
        q = sorted(g)
        print(f"  {stn}{' (E)' if stn in EAST else '    '} S2 dates {len(dates):3d}  "
              f"mean {st.mean(g):5.2f}  median {st.median(g):5.2f}  p90 {q[int(.9 * len(q))]:5.2f}  "
              f"within 2 d {sum(x <= 2 for x in g) / len(g):5.1%}")
    e = [x for s in EAST for x in gaps[s]]
    w = [x for s in gaps if s not in EAST for x in gaps[s]]
    print(f"  EAST mean {st.mean(e):.2f} d (median {st.median(e):.2f}); REST mean {st.mean(w):.2f} d "
          f"(median {st.median(w):.2f}); ratio {st.mean(e) / st.mean(w):.2f}")
    return gaps


def dist_km(a: str, b: str) -> float:
    (la1, lo1), (la2, lo2) = pc.LOC[a], pc.LOC[b]
    return math.hypot((la1 - la2) * 111.0, (lo1 - lo2) * 111.0 * math.cos(math.radians(52.3)))


def stratified(west: list[str], seed: int) -> list[list[str]]:
    """Spatial k=4 blocks of the well-covered stations, one EAST station added to each."""
    wb = sorted(pc.kmeans(west, 4, seed), key=lambda b: st.mean(pc.LOC[s][1] for s in b))
    return [sorted(b + [e]) for b, e in zip(wb, sorted(EAST, key=lambda s: pc.LOC[s][0]))]


def describe(blocks, label, names, gaps):
    near = [min(dist_km(s, o) for o in names if o not in b) for b in blocks for s in b]
    east_per = [sum(s in EAST for s in b) for b in blocks]
    age = [st.mean([x for s in b for x in gaps.get(s, [])]) for b in blocks]
    print(f"  {label}: {len(blocks)} folds, sizes {sorted(len(b) for b in blocks)}, "
          f"eastern per fold {east_per}, mean S2 age per fold {[round(a, 2) for a in age]} "
          f"(spread {max(age) - min(age):.2f} d), nearest training station: "
          f"median {st.median(near):.1f} km, min {min(near):.1f} km")


def power(make_blocks, label):
    for target in ("absolute", "anomaly"):
        d, fresh = [], []
        for seed in range(5):
            b = make_blocks(seed)
            f, s_ = pc.evaluate(b, 0, target), pc.evaluate(b, 5, target)
            d += [x - y for x, y in zip(f, s_)]
            fresh.append(st.mean(f))
        print(f"  {label:14s} {target:8s}: fresh R2 {st.mean(fresh):.3f} (partition sd "
              f"{st.pstdev(fresh):.3f}); paired dR2 {st.mean(d):.3f} +/- {st.pstdev(d):.3f} "
              f"(min {min(d):.3f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    a = ap.parse_args()
    names = sorted(pc.S)
    west = [n for n in names if n not in EAST]

    print("\n== S1 -> most recent S2 at or before it (days); availability only, no cloud")
    gaps = s1_to_s2_age(a.manifest)

    print("\n== block designs")
    for seed in range(3):
        describe(pc.kmeans(names, 4, seed), f"SPATIAL k=4 (seed {seed})", names, gaps)
    for seed in range(2):
        describe(pc.kmeans(west, 4, seed), f"DROP k=4 (seed {seed})", west, gaps)
    for seed in range(2):
        describe(stratified(west, seed), f"STRATIFY (seed {seed})", names, gaps)

    print("\n== power proxy: paired dR2 for a 5-day-stale predictor, 5 partitions")
    power(lambda s: pc.kmeans(names, 4, s), "SPATIAL k=4")
    saved = pc.S, pc.anom
    pc.S = {k: v for k, v in saved[0].items() if k in west}
    pc.anom = {k: v for k, v in saved[1].items() if k in west}
    power(lambda s: pc.kmeans(west, 4, s), "DROP k=4")
    pc.S, pc.anom = saved
    power(lambda s: stratified(west, s), "STRATIFY")


if __name__ == "__main__":
    main()
