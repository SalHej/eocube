"""
Fetch the minimal open-data sample described in PLAN.md (Step 3).

    python experiments/open_cube/fetch_sample.py [--out DIR]

DIR defaults to $EOCUBE_DATA/open_cube, or data/open_cube if EOCUBE_DATA is unset.
On the author's laptop data/ is a directory junction to D:\\eocube_data; either
way it is gitignored and nothing written here is ever committed.

Reads only a small window from each cloud-optimised GeoTIFF, never whole
scenes (one Sentinel-1 RTC VV file is ~1.9 GB). Each window is written on the
source file's own grid and CRS, with no resampling, so that the contract gates
later see the data exactly as published: mixed CRS, mixed GSD and all.

Everything fetched is open: Sentinel-1 RTC and Sentinel-2 L2A from Microsoft
Planetary Computer (anonymous SAS tokens), and Twente in-situ soil moisture from
DANS (CC-BY-4.0). Nothing licensed, nothing from the cluster.

A manifest.json records every item id, asset, CRS, window and file written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
DANS = "https://phys-techsciences.datastations.nl/api/access/datafile/{}"

#: lon/lat window covering stations ITCSM_08 and ITCSM_09 (PLAN.md, "Minimal sample")
WINDOW = (6.72, 52.125, 6.87, 52.160)

#: S1 passes around the 2019-06-27 10:40 UTC S2 overpass: two before, two after.
S1_PASSES = [("2019-06-26T05", 37), ("2019-06-27T05", 139),
             ("2019-06-29T17", 88), ("2019-06-30T17", 15)]
S1_ASSETS = ("vv", "vh")

#: S2 dates: one clear, one partly cloudy (to exercise SCL masking); both UTM tiles.
S2_DATES = ("2019-06-27", "2019-07-02")
S2_TILES = ("31UGT", "32ULC")
S2_ASSETS = ("B02", "B03", "B04", "B08", "B11", "B12", "SCL")

#: DANS file ids (dataset doi:10.17026/dans-znj-wyg5, version 2.1)
GROUND_TRUTH = {
    "ITCSM_08_cd.csv": 76943, "ITCSM_08_fg.csv": 76816,
    "ITCSM_09_cd.csv": 76384, "ITCSM_09_fg.csv": 75868,
    "Twente_network_locations.ods": 75960,
}

GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
                GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2")


def _json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _search(collection: str, start: str, end: str) -> list[dict]:
    body = {"collections": [collection], "bbox": list(WINDOW),
            "datetime": f"{start}/{end}", "limit": 200}
    return _json(f"{STAC}/search", body)["features"]


def _covers(item: dict) -> bool:
    """Whether the item's footprint contains the whole window."""
    w, s, e, n = item["bbox"]
    return w <= WINDOW[0] and s <= WINDOW[1] and e >= WINDOW[2] and n >= WINDOW[3]


def _select_s1() -> list[dict]:
    items = _search("sentinel-1-rtc", "2019-06-24", "2019-07-03")
    chosen = []
    for prefix, orbit in S1_PASSES:
        frames = [i for i in items if i["properties"]["datetime"].startswith(prefix)
                  and i["properties"]["sat:relative_orbit"] == orbit and _covers(i)]
        if not frames:
            raise SystemExit(f"no S1 frame covers the window for {prefix} orbit {orbit}")
        chosen.append(sorted(frames, key=lambda i: i["id"])[0])
    return chosen


def _select_s2() -> list[dict]:
    items = _search("sentinel-2-l2a", "2019-06-24", "2019-07-03")
    chosen = [i for i in items if i["properties"]["datetime"][:10] in S2_DATES
              and i["properties"]["s2:mgrs_tile"] in S2_TILES]
    if len(chosen) != len(S2_DATES) * len(S2_TILES):
        raise SystemExit(f"expected {len(S2_DATES) * len(S2_TILES)} S2 items, got {len(chosen)}")
    return sorted(chosen, key=lambda i: i["id"])


def _read_window(href: str, out: Path, tags: dict) -> dict:
    """Copy the pixels covering WINDOW, on the source grid, to ``out``."""
    with rasterio.open(href) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *WINDOW, densify_pts=21)
        win = from_bounds(*bounds, transform=src.transform)
        # snap outward to whole pixels so the output grid is the source grid
        col0, row0 = int(win.col_off // 1), int(win.row_off // 1)
        col1 = int(-(-(win.col_off + win.width) // 1))
        row1 = int(-(-(win.row_off + win.height) // 1))
        win = Window(col0, row0, col1 - col0, row1 - row0).intersection(
            Window(0, 0, src.width, src.height))
        data = src.read(window=win)
        profile = src.profile.copy()
        profile.update(driver="GTiff", width=int(win.width), height=int(win.height),
                       transform=src.window_transform(win), compress="deflate",
                       tiled=True, blockxsize=256, blockysize=256)
        profile.pop("photometric", None)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data)
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
        return {"crs": str(src.crs), "dtype": src.dtypes[0], "nodata": src.nodata,
                "source_shape": [src.height, src.width],
                "window": [int(win.col_off), int(win.row_off), int(win.width), int(win.height)],
                "res": list(src.res), "bytes": out.stat().st_size}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(Path(os.environ.get("EOCUBE_DATA", "data")) / "open_cube"))
    args = ap.parse_args()
    out = Path(args.out)
    manifest = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "window_lonlat": WINDOW, "s1": [], "s2": [], "ground_truth": []}

    tokens = {c: _json(SAS.format(c))["token"] for c in ("sentinel-1-rtc", "sentinel-2-l2a")}
    with rasterio.Env(**GDAL_ENV):
        for item in _select_s1():
            p = item["properties"]
            for a in S1_ASSETS:
                href = item["assets"][a]["href"] + "?" + tokens["sentinel-1-rtc"]
                name = (f"S1_{p['datetime'][:16].replace(':', '')}_rel{p['sat:relative_orbit']}"
                        f"_{p['sat:orbit_state'][:4]}_EPSG{p['proj:epsg']}_{a.upper()}.tif")
                tags = {"item_id": item["id"], "collection": "sentinel-1-rtc", "asset": a,
                        "datetime": p["datetime"], "relative_orbit": p["sat:relative_orbit"],
                        "orbit_state": p["sat:orbit_state"], "platform": p["platform"],
                        "units": "gamma0 linear power", "terrain_corrected": "RTC"}
                info = _read_window(href, out / "raw" / "s1" / name, tags)
                manifest["s1"].append({"file": name, **tags, **info})
                print(f"S1 {name}  {info['window'][2]}x{info['window'][3]} px  {info['bytes']/1e3:.0f} kB")
        for item in _select_s2():
            p = item["properties"]
            for a in S2_ASSETS:
                href = item["assets"][a]["href"] + "?" + tokens["sentinel-2-l2a"]
                name = (f"S2_{p['datetime'][:16].replace(':', '')}_{p['s2:mgrs_tile']}"
                        f"_EPSG{p['proj:epsg']}_{a}.tif")
                tags = {"item_id": item["id"], "collection": "sentinel-2-l2a", "asset": a,
                        "datetime": p["datetime"], "mgrs_tile": p["s2:mgrs_tile"],
                        "platform": p["platform"], "tile_cloud_cover": p["eo:cloud_cover"],
                        "processing_baseline": p.get("s2:processing_baseline")}
                info = _read_window(href, out / "raw" / "s2" / name, tags)
                manifest["s2"].append({"file": name, **tags, **info})
                print(f"S2 {name}  {info['window'][2]}x{info['window'][3]} px  {info['bytes']/1e3:.0f} kB")

    gt = out / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    for name, fid in GROUND_TRUTH.items():
        dest = gt / name
        if not dest.exists():
            urllib.request.urlretrieve(DANS.format(fid), dest)
        manifest["ground_truth"].append({"file": name, "dans_file_id": fid,
                                         "bytes": dest.stat().st_size})
        print(f"GT {name}  {dest.stat().st_size/1e6:.1f} MB")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total = sum(e["bytes"] for k in ("s1", "s2", "ground_truth") for e in manifest[k])
    print(f"\nwrote {out / 'manifest.json'}; {total/1e6:.1f} MB in total")


if __name__ == "__main__":
    main()
