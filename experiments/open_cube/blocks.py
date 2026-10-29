"""Station locations and the spatial block partitions (PLAN.md, "Block design").

Shared by power_check.py, eastwest_blocks.py and run_ablation.py so that the
ablation folds are the partitions the design was chosen on, not a re-implementation.
Importing this module loads no data.
"""
import random
import statistics as st
from collections import defaultdict

LOC = {  # first-listed WGS84 location; relocations are within a few km at most
 "ITCSM_01": (52.41472, 6.96722), "ITCSM_02": (52.39000, 6.85722), "ITCSM_03": (52.35061, 6.78992),
 "ITCSM_04": (52.27167, 6.92111), "ITCSM_05": (52.27333, 6.69944), "ITCSM_06": (52.41389, 6.63667),
 "ITCSM_07": (52.37167, 6.96528), "ITCSM_08": (52.13583, 6.74500), "ITCSM_09": (52.14639, 6.84306),
 "ITCSM_10": (52.20000, 6.65944), "ITCSM_11": (52.23111, 6.55889), "ITCSM_12": (52.14028, 6.55972),
 "ITCSM_13": (52.19389, 6.41750), "ITCSM_14": (52.19167, 6.31722), "ITCSM_15": (52.30944, 6.61250),
 "ITCSM_16": (52.36781, 6.53525), "ITCSM_17": (52.42056, 6.54056), "ITCSM_18": (52.40528, 6.37991),
 "ITCSM_19": (52.33333, 6.33167), "ITCSM_20": (52.31889, 6.44861)}

#: Stations one S2 orbit's swath does not reach (71-72 of 143 dates in 2019).
EAST = ["ITCSM_01", "ITCSM_04", "ITCSM_07", "ITCSM_09"]


def kmeans(names, k, seed):
    rnd = random.Random(seed)
    pts = {n: LOC[n] for n in names}
    cent = [pts[n] for n in rnd.sample(names, k)]
    for _ in range(50):
        groups = defaultdict(list)
        for n, p in pts.items():
            groups[min(range(k), key=lambda j: (p[0]-cent[j][0])**2 + ((p[1]-cent[j][1])*0.61)**2)].append(n)
        cent = [(st.mean(pts[n][0] for n in g), st.mean(pts[n][1] for n in g)) for g in groups.values()]
        k = len(cent)
    return list(groups.values())


def stratified(west: list[str], seed: int) -> list[list[str]]:
    """Spatial k=4 blocks of the well-covered stations, one EAST station added to each."""
    wb = sorted(kmeans(west, 4, seed), key=lambda b: st.mean(LOC[s][1] for s in b))
    return [sorted(b + [e]) for b, e in zip(wb, sorted(EAST, key=lambda s: LOC[s][0]))]
