"""
Fetch one full year of the open cube: S1-RTC and S2-L2A chips at every usable Twente
station, plus the DANS in-situ files (PLAN.md, Step 4).

    python experiments/open_cube/fetch_year.py --year 2019 --out ~/work/data/raw/open_cube

Run in a Slurm job, never on a login node (scripts/fetch_year.sbatch).

- One chip per station per acquisition: a square of --chip-m metres centred on the
  station's location valid in that year, read from the source cloud-optimised GeoTIFF
  on its own grid and CRS, with no resampling. The gates later see the data as
  published.
- S1: every pass in the year. For each station, the frame whose footprint contains the
  station is used, because one frame does not always reach all stations.
- S2: every date in the year. For each station, of the tiles whose footprint contains
  it, the one with the most real-measurement pixels at the station (SCL not 0/1 and
  B02 >= 50 DN, water exempt). Swath-edge fill reads as small positive numbers, so
  "not nodata" is not enough. Cloud counts as measured, so it cannot steer the choice.
  All dates are fetched regardless of cloud; per-pixel SCL decides validity later.
- Resumable: a chip already on disk is skipped. Every chip and every failure is
  appended to manifest.jsonl, so an interrupted run loses nothing and is auditable.

Open data only: Planetary Computer (anonymous SAS tokens) and DANS (CC-BY-4.0).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform
from rasterio.windows import Window

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
DANS = "https://phys-techsciences.datastations.nl/api/access/datafile/{}"
DATASET = ("https://phys-techsciences.datastations.nl/api/datasets/:persistentId/"
           "?persistentId=doi:10.17026/dans-znj-wyg5")
BBOX = [6.25, 52.05, 7.02, 52.45]
S1_ASSETS = ("vv", "vh")
S2_ASSETS = ("B02", "B03", "B04", "B08", "B11", "B12", "SCL")

#: Station locations (lon, lat) from Twente_network_locations.ods, sheet Lat_Lon_WGS84,
#: as (valid_from, lon, lat). Relocations are listed with their dates. The 17 stations
#: usable in 2017-2020 (PLAN.md, "Power check"); ITCSM_06, 19, 20 are excluded.
LOCATIONS = {
    "ITCSM_01": [("2008-12-29", 6.96722, 52.41472), ("2014-04-09", 6.96583, 52.41472)],
    "ITCSM_02": [("2008-11-12", 6.85722, 52.39000), ("2008-11-12", 6.86011, 52.39083)],
    "ITCSM_03": [("2008-11-19", 6.78992, 52.35061), ("2017-05-02", 6.78951, 52.35024)],
    "ITCSM_04": [("2008-11-12", 6.92111, 52.27167), ("2016-03-09", 6.92133, 52.27158)],
    "ITCSM_05": [("2009-05-27", 6.69944, 52.27333), ("2017-05-02", 6.69273, 52.26787),
                 ("2018-05-24", 6.69109, 52.26598)],
    "ITCSM_07": [("2008-11-12", 6.96528, 52.37167), ("2015-11-03", 6.96228, 52.37328)],
    "ITCSM_08": [("2008-12-19", 6.74500, 52.13583)],
    "ITCSM_09": [("2008-12-19", 6.84306, 52.14639)],
    "ITCSM_10": [("2008-12-12", 6.65944, 52.20000), ("2014-05-02", 6.66719, 52.19884),
                 ("2016-05-20", 6.66827, 52.19104)],
    "ITCSM_11": [("2008-12-12", 6.55889, 52.23111), ("2015-10-09", 6.55828, 52.23139),
                 ("2016-10-18", 6.54288, 52.22005)],
    "ITCSM_12": [("2009-05-27", 6.55972, 52.14028)],
    "ITCSM_13": [("2009-06-03", 6.41750, 52.19389)],
    "ITCSM_14": [("2009-06-03", 6.31722, 52.19167), ("2011-04-20", 6.31389, 52.19444),
                 ("2016-04-01", 6.30484, 52.19594)],
    "ITCSM_15": [("2008-11-19", 6.61250, 52.30944), ("2016-03-07", 6.61053, 52.31036)],
    "ITCSM_16": [("2009-04-01", 6.53525, 52.36781)],
    "ITCSM_17": [("2009-04-01", 6.54056, 52.42056), ("2011-04-20", 6.54044, 52.42013),
                 ("2016-04-01", 6.54070, 52.41877)],
    "ITCSM_18": [("2009-04-01", 6.37991, 52.40528)],
}
#: ITCSM_02's second entry has no date in the sheet (and its RD_new row duplicates
#: ITCSM_01's); the second WGS84 position is used, as the later one. See PLAN.md.

GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
                GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="3",
                GDAL_HTTP_TIMEOUT="60", VSI_CACHE="TRUE")


def station_positions(year: int) -> dict[str, tuple[float, float]]:
    """Each station's location valid on 1 July of ``year`` (mid-year)."""
    ref = date(year, 7, 1).isoformat()
    return {s: [(lon, lat) for since, lon, lat in locs if since <= ref][-1]
            for s, locs in LOCATIONS.items()}


def _json(url, body=None, attempts=6):
    """GET or POST JSON, retrying transient failures (5xx, timeouts) with backoff.

    The STAC API returned a 504 during testing; without a retry, one transient error
    at start-up would end a multi-hour job.
    """
    for i in range(attempts):
        req = urllib.request.Request(url, json.dumps(body).encode() if body else None,
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException):          # incl. RemoteDisconnected, seen in testing
            if i == attempts - 1:
                raise
        time.sleep(min(60, 5 * 2 ** i))


def search(collection: str, year: int) -> list[dict]:
    """All items over BBOX in ``year``, queried one month at a time.

    A single full-year query (up to ~580 S2 items) repeatedly hit 504 Gateway Timeout;
    month-sized pages are small enough to succeed and retry independently.
    """
    out, seen = [], set()
    for month in range(1, 13):
        end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
        body = {"collections": [collection], "bbox": BBOX, "limit": 250,
                "datetime": f"{year}-{month:02d}-01T00:00:00Z/{end}T00:00:00Z"}
        for _page in range(100):                          # hard cap: never loop forever
            d = _json(f"{STAC}/search", body)
            new = [i for i in d["features"] if i["id"] not in seen]
            for item in new:                              # month boundaries are inclusive
                seen.add(item["id"])
                out.append(item)
            nxt = [l for l in d.get("links", []) if l.get("rel") == "next"]
            # stop on a short or repeated page: the API can keep offering a "next"
            # link after the last page, which made the earlier loop spin forever
            if not nxt or not new or len(d["features"]) < body["limit"]:
                break
            body = {**body, **(nxt[0].get("body") or {})}
        else:
            raise RuntimeError(f"STAC paging did not terminate for {collection} {year}-{month:02d}")
    return out


class Tokens:
    """Planetary Computer SAS tokens, refreshed every 30 minutes."""

    def __init__(self):
        self._lock, self._tok, self._at = threading.Lock(), {}, {}

    def __call__(self, collection: str) -> str:
        with self._lock:
            if time.time() - self._at.get(collection, 0) > 1800:
                self._tok[collection] = _json(SAS.format(collection))["token"]
                self._at[collection] = time.time()
            return self._tok[collection]


def _inside(item: dict, lon: float, lat: float) -> bool:
    """Whether (lon, lat) lies inside the item's footprint polygon.

    The bounding box is not enough. An S1 frame's footprint is a slanted polygon, and
    a station inside the rectangle but outside the swath gets a chip that is entirely
    nodata. The first dry run produced exactly that.
    """
    geom = item["geometry"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for rings in polys:
        ring, inside = rings[0], False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        if inside:
            return True
    return False


def _nodata_fraction(data, nodata) -> float:
    if nodata is None:
        return 0.0
    return float((data == nodata).mean())


def chip(src, lon: float, lat: float, chip_m: float):
    """Read a chip_m square centred on (lon, lat), on the source grid. None if outside."""
    x, y = transform("EPSG:4326", src.crs, [lon], [lat])
    row, col = src.index(x[0], y[0])
    half = int(round(chip_m / 2 / abs(src.res[0])))
    win = Window(col - half, row - half, 2 * half, 2 * half)
    full = Window(0, 0, src.width, src.height)
    try:
        win = win.intersection(full)
    except Exception:  # noqa: BLE001 - rasterio raises when there is no overlap
        return None, None
    if win.width < half or win.height < half:          # station near or past the edge
        return None, None
    return src.read(window=win), src.window_transform(win)


def write(path: Path, data, profile: dict, tr, tags: dict) -> int:
    profile = dict(profile)
    profile.update(driver="GTiff", width=data.shape[2], height=data.shape[1], transform=tr,
                   compress="deflate", tiled=False)
    for k in ("blockxsize", "blockysize", "photometric", "interleave"):
        profile.pop(k, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(data)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
    os.replace(tmp, path)
    return path.stat().st_size


class Manifest:
    def __init__(self, path: Path):
        self._path, self._lock = path, threading.Lock()

    def add(self, **rec):
        with self._lock, open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def s1_task(group: list[dict], pos, out: Path, tokens: Tokens, chip_m: float, man: Manifest):
    """One S1 pass (possibly several frames): VV and VH chips for every station."""
    p0 = group[0]["properties"]
    stamp = p0["datetime"][:16].replace(":", "")
    done = 0
    with ExitStack() as stack:
        cache: dict[tuple, object] = {}

        def opened(item, a):                              # each frame opened once per pass
            key = (item["id"], a)
            if key not in cache:
                href = item["assets"][a]["href"] + "?" + tokens("sentinel-1-rtc")
                cache[key] = stack.enter_context(rasterio.open(href))
            return cache[key]

        done = _s1_stations(group, pos, out, chip_m, man, stamp, p0, opened)
    return done


def _s1_stations(group, pos, out, chip_m, man, stamp, p0, opened) -> int:
    done = 0
    for station, (lon, lat) in pos.items():
        frames = sorted((f for f in group if _inside(f, lon, lat)), key=lambda f: f["id"])
        if not frames:
            man.add(kind="s1", station=station, datetime=p0["datetime"], status="no_frame")
            continue
        if list((out / "s1" / station).glob(f"S1_{stamp}_*_VH.tif")):
            continue                                      # pass already fetched
        # try frames in turn until one has data at the station (VV decides; VH follows)
        for item in frames:
            p = item["properties"]
            base = (f"S1_{stamp}_rel{p['sat:relative_orbit']}_{p['sat:orbit_state'][:4]}"
                    f"_EPSG{p['proj:epsg']}")
            try:
                chips = {}
                for a in S1_ASSETS:
                    src = opened(item, a)
                    data, tr = chip(src, lon, lat, chip_m)
                    if data is None:
                        break
                    nd = _nodata_fraction(data, src.nodata)
                    if a == "vv" and nd == 1.0:
                        break                             # empty here: try the next frame
                    chips[a] = (data, tr, src.profile.copy(), src.nodata, nd)
                if len(chips) < len(S1_ASSETS):
                    continue
            except Exception as e:  # noqa: BLE001 - record and try the next frame
                man.add(kind="s1", station=station, item_id=item["id"], status="error",
                        error=str(e)[:300])
                continue
            for a, (data, tr, profile, nodata, nd) in chips.items():
                name = f"{base}_{a.upper()}.tif"
                tags = {"item_id": item["id"], "asset": a, "datetime": p["datetime"],
                        "relative_orbit": p["sat:relative_orbit"],
                        "orbit_state": p["sat:orbit_state"], "platform": p["platform"],
                        "units": "gamma0 linear power", "terrain_corrected": "RTC",
                        "station": station, "station_lonlat": f"{lon},{lat}"}
                size = write(out / "s1" / station / name, data, profile, tr, tags)
                man.add(kind="s1", station=station, file=name, item_id=item["id"],
                        datetime=p["datetime"], crs=f"EPSG:{p['proj:epsg']}", bytes=size,
                        nodata_fraction=round(nd, 4), frames_tried=frames.index(item) + 1,
                        status="ok")
                done += 1
            break
        else:
            man.add(kind="s1", station=station, datetime=p0["datetime"],
                    frames=len(frames), status="all_nodata")
    return done


#: S2 L2A scene classes that mean "no measurement here" (SCL 0 = no data, 1 = saturated
#: or defective). Cloud (8-10) and shadow (3) ARE measurements: they are a per-pixel
#: validity question for the cube, not a tile-selection one.
SCL_NOT_MEASURED = (0, 1)
SCL_WATER = 6
#: Over land, L2A B02 below ~50 DN (reflectance 0.005) is not credible. Fill values at
#: swath edges read as small positive numbers (B02 = 1 was seen at ITCSM_08 on
#: 2019-06-25, with SCL claiming data). Water is genuinely dark in blue, so exempt.
B02_FLOOR = 50
#: Pixels within this many 20 m pixels of the image's nodata boundary are not trusted.
#: At 2019-06-25 the swath edge ran through ITCSM_08: one UTM grid put the station on a
#: 1-pixel fill strip (B02 = 1), the other put it one pixel inside (B02 = 1260). Both
#: grids resample the same acquisition, so neither value at the edge is reliable.
EDGE_BUFFER_PX = 2


def measured_mask(b02, scl):
    """Real-measurement pixels of a chip, on the SCL (20 m) grid.

    A pixel is measured if SCL is not 0/1, B02 is plausible over land (>= 50 DN;
    water exempt) and it is at least EDGE_BUFFER_PX pixels from the nodata boundary.
    Cloud counts as measured, so cloudiness cannot steer tile choice. Two tiles of the
    same cloud are equally cloudy, and preferring clear tiles would bias the sample
    toward dry weather.
    """
    scl = scl[0]
    b = b02[0][::2, ::2][: scl.shape[0], : scl.shape[1]]      # 10 m -> 20 m, same extent
    scl = scl[: b.shape[0], : b.shape[1]]
    empty = (b == 0) | (scl == 0)
    near_edge = empty.copy()
    for _ in range(EDGE_BUFFER_PX):                            # dilate the nodata region
        grown = near_edge.copy()
        grown[1:, :] |= near_edge[:-1, :]; grown[:-1, :] |= near_edge[1:, :]
        grown[:, 1:] |= near_edge[:, :-1]; grown[:, :-1] |= near_edge[:, 1:]
        near_edge = grown
    return (~np.isin(scl, SCL_NOT_MEASURED) & ~near_edge
            & ((b >= B02_FLOOR) | (scl == SCL_WATER)))


def tile_score(b02, scl, scl_transform, crs, lon: float, lat: float
               ) -> tuple[float, float, float]:
    """(station pixel measured, 0 or 1; measured fraction of the 3 x 3 window around
    it; measured fraction of the whole chip), compared in that order.

    The station's own pixel decides first. A window score alone called a station one
    pixel from the swath edge one-third measured, though that pixel is untrusted. Its position is located through the SCL
    chip's transform, not assumed central, because a chip is clipped where it meets
    a tile edge, which is exactly where this matters.
    """
    m = measured_mask(b02, scl)
    if not m.size:
        return 0.0, 0.0, 0.0
    x, y = transform("EPSG:4326", crs, [lon], [lat])
    col, row = ~scl_transform * (x[0], y[0])
    r, c = int(row), int(col)
    if not (0 <= r < m.shape[0] and 0 <= c < m.shape[1]):
        return 0.0, 0.0, float(m.mean())
    window = m[max(0, r - 1): r + 2, max(0, c - 1): c + 2]
    return float(m[r, c]), float(window.mean()), float(m.mean())


def s2_task(tiles: dict, pos, out: Path, tokens: Tokens, chip_m: float, man: Manifest):
    """One S2 date: every asset's chip for every station.

    ``tiles`` maps MGRS tile to item for that date. For each station the candidates
    are the tiles whose footprint contains it. The footprint is only a cheap first
    filter: 287 of 2019's 578 footprints are the bare tile square, and a footprint
    can "contain" a station where the pixels are fill. Each candidate's B02 and SCL
    chips are read and scored by :func:`tile_score`: the station pixel, then the 3 x 3
    window around it, then the whole chip, with zone 32 only
    breaking exact ties. Chip-level counts alone could not separate two grids of the
    same acquisition whose swath edge ran through the station. If no tile has a
    trustworthy station pixel, the best chip is still written, so the cube sees the
    date, and the manifest records ``station_measured: false``. Every candidate's
    score goes to the manifest. SCL is written last, so its presence marks a
    station-date as complete for resuming.
    """
    first = next(iter(tiles.values()))["properties"]
    stamp = first["datetime"][:16].replace(":", "")
    order = sorted(tiles, key=lambda t: (not t.startswith("32"), t))
    done = 0
    with ExitStack() as stack:
        cache = {}

        def opened(tile, a):
            if (tile, a) not in cache:
                href = tiles[tile]["assets"][a]["href"] + "?" + tokens("sentinel-2-l2a")
                cache[(tile, a)] = stack.enter_context(rasterio.open(href))
            return cache[(tile, a)]

        for station, (lon, lat) in pos.items():
            if list((out / "s2" / station).glob(f"S2_{stamp}_*_SCL.tif")):
                continue
            candidates = [t for t in order if _inside(tiles[t], lon, lat)]
            scores = {}
            for tile in candidates:
                try:
                    b02, _ = chip(opened(tile, "B02"), lon, lat, chip_m)
                    scl_src = opened(tile, "SCL")
                    scl, scl_tr = chip(scl_src, lon, lat, chip_m)
                    scores[tile] = ((0.0, 0.0, 0.0) if b02 is None or scl is None
                                    else tile_score(b02, scl, scl_tr, scl_src.crs, lon, lat))
                except Exception as e:  # noqa: BLE001 - record; the tile scores nothing
                    man.add(kind="s2", station=station, item_id=tiles[tile]["id"],
                            status="error", error=str(e)[:300])
                    scores[tile] = (0.0, 0.0, 0.0)
            best = max(candidates, key=lambda t: (scores[t], -order.index(t)), default=None)
            if best is None or scores[best][2] == 0.0:
                man.add(kind="s2", station=station, datetime=first["datetime"],
                        tile_scores={t: [round(v, 3) for v in sc] for t, sc in scores.items()},
                        status="no_measurement")
                continue
            item, p = tiles[best], tiles[best]["properties"]
            try:
                chips = {}
                for a in S2_ASSETS:
                    src = opened(best, a)
                    data, tr = chip(src, lon, lat, chip_m)
                    nd = _nodata_fraction(data, 0 if src.nodata is None else src.nodata)
                    chips[a] = (data, tr, src.profile.copy(), nd)
            except Exception as e:  # noqa: BLE001 - record and move on
                man.add(kind="s2", station=station, item_id=item["id"], status="error",
                        error=str(e)[:300])
                continue
            for a, (data, tr, profile, nd) in chips.items():
                name = f"S2_{stamp}_{best}_EPSG{p['proj:epsg']}_{a}.tif"
                tags = {"item_id": item["id"], "asset": a, "datetime": p["datetime"],
                        "mgrs_tile": best, "platform": p["platform"],
                        "tile_cloud_cover": p["eo:cloud_cover"],
                        "processing_baseline": p.get("s2:processing_baseline"),
                        "station_measured": bool(scores[best][0]),
                        "station_window_measured_fraction": round(scores[best][1], 4),
                        "measured_fraction": round(scores[best][2], 4),
                        "station": station, "station_lonlat": f"{lon},{lat}"}
                size = write(out / "s2" / station / name, data, profile, tr, tags)
                man.add(kind="s2", station=station, file=name, item_id=item["id"],
                        datetime=p["datetime"], crs=f"EPSG:{p['proj:epsg']}", bytes=size,
                        nodata_fraction=round(nd, 4), station_measured=scores[best][0] > 0,
                        tile_scores={t: [round(v, 3) for v in sc] for t, sc in scores.items()},
                        status="ok")
                done += 1
    return done


def ground_truth(out: Path, man: Manifest) -> None:
    files = _json(DATASET)["data"]["latestVersion"]["files"]
    ids = {f["label"]: f["dataFile"]["id"] for f in files}
    names = [f"{s}_{k}.csv" for s in LOCATIONS for k in ("cd", "fg")]
    names.append("Twente_network_locations.ods")
    gt = out / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    for name in names:
        dest = gt / name
        if dest.exists():
            continue
        tmp = dest.with_suffix(".partial")
        urllib.request.urlretrieve(DANS.format(ids[name]), tmp)
        os.replace(tmp, dest)
        man.add(kind="ground_truth", file=name, dans_file_id=ids[name],
                bytes=dest.stat().st_size, status="ok")


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--chip-m", type=float, default=1000.0)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 4)))
    ap.add_argument("--only", choices=("s1", "s2", "gt"), action="append")
    a = ap.parse_args()
    out = a.out.expanduser() / str(a.year)
    out.mkdir(parents=True, exist_ok=True)
    man = Manifest(out / "manifest.jsonl")
    pos = station_positions(a.year)
    only = set(a.only or ("s1", "s2", "gt"))
    t0 = time.time()
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} year {a.year}, "
          f"{len(pos)} stations, chip {a.chip_m:.0f} m, {a.workers} workers -> {out}")

    if "gt" in only:
        ground_truth(out, man)
        print(f"ground truth done ({time.time() - t0:.0f} s)")

    tokens = Tokens()
    jobs = []
    if "s1" in only:
        passes: dict[tuple, list] = {}
        for f in search("sentinel-1-rtc", a.year):
            p = f["properties"]
            passes.setdefault((p["datetime"][:13], p["sat:relative_orbit"]), []).append(f)
        jobs += [("s1", g) for g in passes.values()]
    if "s2" in only:
        dates: dict[str, dict] = {}
        for f in search("sentinel-2-l2a", a.year):
            dates.setdefault(f["properties"]["datetime"][:10], {})[f["properties"]["s2:mgrs_tile"]] = f
        jobs += [("s2", tiles) for tiles in dates.values()]
    print(f"{sum(k == 's1' for k, _ in jobs)} S1 passes, {sum(k == 's2' for k, _ in jobs)} "
          f"S2 dates to process")

    with rasterio.Env(**GDAL_ENV), ThreadPoolExecutor(a.workers) as pool:
        futs = {pool.submit(s1_task if k == "s1" else s2_task, x, pos, out, tokens,
                            a.chip_m, man): k for k, x in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            fut.result()
            if n % 25 == 0 or n == len(futs):
                print(f"  {n}/{len(futs)} acquisitions ({time.time() - t0:.0f} s)")

    recs = [json.loads(l) for l in open(out / "manifest.jsonl", encoding="utf-8")]
    ok = [r for r in recs if r.get("status") == "ok"]
    bad = [r for r in recs if r.get("status") not in ("ok",)]
    print(f"done in {time.time() - t0:.0f} s: {len(ok)} files ok, "
          f"{sum(r.get('bytes', 0) for r in ok) / 1e6:.0f} MB; {len(bad)} not ok "
          f"({', '.join(sorted({r['status'] for r in bad})) or 'none'})")


if __name__ == "__main__":
    main()
