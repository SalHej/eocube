"""Check fetch_year.py on real swath-edge cases against ground truth: the raw value at
the station pixel in EVERY frame/tile of each acquisition. Run after edge_cases.py, in
the same directory. See docs/examples/swath_edges.md."""
import json, sys, collections, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_year as f, rasterio
from rasterio.warp import transform
sys.stdout.reconfigure(encoding="utf-8")

import math
pos = f.station_positions(2019)
edge = json.load(open("edge_cases.json"))


def edge_dist_m(item, lon, lat):
    """Distance (m) from the station to the nearest footprint edge; + inside, - outside."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110574
    g = item["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    best = math.inf
    for ring in (p[0] for p in polys):
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            ax, ay, bx, by = (x1 - lon) * kx, (y1 - lat) * ky, (x2 - lon) * kx, (y2 - lat) * ky
            dx, dy = bx - ax, by - ay
            t = max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best if f._inside(item, lon, lat) else -best
tok = f.Tokens()

# S1 cases: the one with no containing frame, then the closest-inside ones
s1_cases = [("2019-03-23T05", 139, "ITCSM_16"), ("2019-03-11T05", 139, "ITCSM_16"),
            ("2019-03-23T05", 139, "ITCSM_03"), ("2019-01-31T17", 15, "ITCSM_09")]
# S2 cases: closest positive (just inside) and clearly outside, plus a 5-vertex-footprint case
s2_near = sorted([r for r in edge["s2"] if 0 < r[0] < 2000])[:3]
s2_out = [r for r in edge["s2"] if r[0] < -5000][:1]
s2_cases = [(r[1], r[2]) for r in s2_near + s2_out]

s1 = collections.defaultdict(list)
for it in f.search("sentinel-1-rtc", 2019):
    p = it["properties"]; s1[(p["datetime"][:13], p["sat:relative_orbit"])].append(it)
s2 = collections.defaultdict(dict)
for it in f.search("sentinel-2-l2a", 2019):
    s2[it["properties"]["datetime"][:10]][it["properties"]["s2:mgrs_tile"]] = it

# Placeholder-footprint traps: a 5-point (tile-square) footprint "contains" the station,
# the tile has nodata, and a detailed footprint from the same date puts the station
# outside or within 1 km of the data edge. These are where _inside() alone is fooled.
def npts(it):
    g = it["geometry"]
    return len(g["coordinates"][0]) if g["type"] == "Polygon" else 99

traps = []
for day, tiles in s2.items():
    for st, (lon, lat) in pos.items():
        square = [t for t, it in tiles.items() if npts(it) == 5 and f._inside(it, lon, lat)
                  and it["properties"].get("s2:nodata_pixel_percentage", 0) > 0]
        detail = [t for t, it in tiles.items() if npts(it) > 5]
        if square and detail:
            dmax = max(edge_dist_m(tiles[t], lon, lat) for t in detail)
            if dmax < 1000:
                traps.append((round(dmax), day, st, square))
traps.sort()
print(f"placeholder-footprint trap candidates: {len(traps)}; testing the 3 most extreme + 1 marginal")
s2_cases += [(d, s) for _, d, s, _ in traps[:3]] + [(d, s) for x, d, s, _ in traps if 0 < x < 1000][:1]

def truth(item, asset, coll, lon, lat):
    with rasterio.open(item["assets"][asset]["href"] + "?" + tok(coll)) as src:
        x, y = transform("EPSG:4326", src.crs, [lon], [lat])
        r, c = src.index(x[0], y[0])
        if not (0 <= r < src.height and 0 <= c < src.width):
            return "off-raster"
        v = src.read(1, window=((r, r + 1), (c, c + 1)))[0, 0]
        nd = src.nodata if src.nodata is not None else 0
        return "NODATA" if v == nd else f"data({v:.4g})"

out = Path("edge_test"); shutil.rmtree(out, ignore_errors=True); out.mkdir()
man = f.Manifest(out / "m.jsonl")
with rasterio.Env(**f.GDAL_ENV):
    print("=== S1")
    for pre, orb, st in s1_cases:
        group = s1[(pre, orb)]
        lon, lat = pos[st]
        print(f"\n{pre} rel{orb} {st}: {len(group)} frames")
        for it in group:
            print(f"   frame ...{it['id'][-30:]}  footprint-contains={f._inside(it, lon, lat)}  "
                  f"raw value at station: {truth(it, 'vv', 'sentinel-1-rtc', lon, lat)}")
        f.s1_task(group, {st: (lon, lat)}, out, tok, 1000.0, man)
    print("\n=== S2")
    for day, st in s2_cases:
        tiles = s2[day]; lon, lat = pos[st]
        print(f"\n{day} {st}: tiles {sorted(tiles)}")
        for t, it in sorted(tiles.items()):
            nv = len(it["geometry"]["coordinates"][0]) if it["geometry"]["type"] == "Polygon" else "multi"
            print(f"   {t} ({nv}-pt footprint, nodata {it['properties'].get('s2:nodata_pixel_percentage', 0):.0f}%)"
                  f"  contains={f._inside(it, lon, lat)}  raw B02 at station: {truth(it, 'B02', 'sentinel-2-l2a', lon, lat)}")
        f.s2_task(tiles, {st: (lon, lat)}, out, tok, 1000.0, man)

print("\n=== what fetch_year recorded")
for l in open(out / "m.jsonl"):
    r = json.loads(l)
    if r.get("file", "").endswith(("_VH.tif", "_B03.tif", "_B04.tif", "_B08.tif", "_B11.tif", "_B12.tif", "_SCL.tif")):
        continue
    print(" ", r["status"], r.get("station"), r.get("datetime", "")[:13], (r.get("file") or "")[:48],
          "nodata", r.get("nodata_fraction"), "tried", r.get("frames_tried", r.get("tiles_tried", "")),
          r.get("tiles", ""), r.get("error", "")[:60])
