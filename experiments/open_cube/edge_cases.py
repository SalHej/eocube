"""Find 2019 S1 passes and S2 dates where a Twente station sits near a footprint edge.

Metadata only (STAC footprints). Writes edge_cases.json for edge_verify.py.
See docs/examples/swath_edges.md."""
import json, math, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_year as f
sys.stdout.reconfigure(encoding="utf-8")

pos = f.station_positions(2019)

def rings(item):
    g = item["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    return [p[0] for p in polys]

def edge_dist_m(item, lon, lat):
    """Distance (m) from the station to the nearest footprint edge; + inside, - outside."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110574
    best = math.inf
    for ring in rings(item):
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            ax, ay = (x1 - lon) * kx, (y1 - lat) * ky
            bx, by = (x2 - lon) * kx, (y2 - lat) * ky
            dx, dy = bx - ax, by - ay
            t = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best if f._inside(item, lon, lat) else -best

def in_bbox(item, lon, lat):
    w, s, e, n = item["bbox"]
    return w <= lon <= e and s <= lat <= n

out = {"s1": [], "s2": []}
s1 = f.search("sentinel-1-rtc", 2019)
passes = collections.defaultdict(list)
for it in s1:
    p = it["properties"]; passes[(p["datetime"][:13], p["sat:relative_orbit"])].append(it)
for key, group in passes.items():
    for st, (lon, lat) in pos.items():
        d = [(edge_dist_m(it, lon, lat), it) for it in group]
        inside = [x for x in d if x[0] > 0]
        trap = [it["id"] for dd, it in d if dd < 0 and in_bbox(it, lon, lat)]
        if inside:
            dmin = max(x[0] for x in inside)          # best (deepest) containing frame
            if dmin < 2000 or trap:
                out["s1"].append((round(dmin), key[0], key[1], st, len(group), len(inside), len(trap)))
        else:
            out["s1"].append((round(max(x[0] for x in d)) if d else None, key[0], key[1], st, len(group), 0, len(trap)))

s2 = f.search("sentinel-2-l2a", 2019)
print("S2 geometry vertex counts (sample):", collections.Counter(len(rings(i)[0]) for i in s2).most_common(5))
print("S2 items with nodata%>0:", sum(i["properties"].get("s2:nodata_pixel_percentage", 0) > 0 for i in s2), "of", len(s2))
dates = collections.defaultdict(list)
for it in s2:
    dates[it["properties"]["datetime"][:10]].append(it)
for day, items in dates.items():
    for st, (lon, lat) in pos.items():
        d = sorted(((edge_dist_m(it, lon, lat), it["properties"]["s2:mgrs_tile"],
                     round(it["properties"].get("s2:nodata_pixel_percentage", 0), 1)) for it in items),
                   reverse=True)
        best = d[0][0]
        if best < 2000:
            out["s2"].append((round(best), day, st, [(round(a), t, n) for a, t, n in d[:3]]))

json.dump(out, open("edge_cases.json", "w"))
print("\nS1: stations within 2 km of the deepest containing frame's edge, or with a bbox-trap frame:",
      len(out["s1"]))
for r in sorted(out["s1"], key=lambda r: (r[0] if r[0] is not None else -9e9))[:12]:
    print("  ", r)
print("  stations with NO containing frame:", sum(1 for r in out["s1"] if r[5] == 0))
print("  passes with a bbox-trap frame (inside bbox, outside footprint):", sum(1 for r in out["s1"] if r[6]))
print("\nS2: station-dates whose best tile has it within 2 km of the data edge (or outside):", len(out["s2"]))
for r in sorted(out["s2"])[:12]:
    print("  ", r)
