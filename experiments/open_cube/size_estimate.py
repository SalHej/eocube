"""
Estimate download (bytes transferred) and storage for one full year, all stations.

    python experiments/open_cube/size_estimate.py [--year 2019] [--chip-m 1000]

Measured, not assumed: for one real S1-RTC and one real S2-L2A item it reads the
compressed byte size of every internal COG block that a station chip touches
(GDAL's TIFF BLOCK_SIZE metadata, a header read only), then scales by the year's
acquisition counts from the STAC catalogue. Stored size uses the bytes per pixel
measured on the Step 3 sample.
"""
import argparse
import json
import math
import sys
import urllib.request

import rasterio
from rasterio.warp import transform

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
BBOX = [6.30, 52.06, 6.98, 52.43]
#: the 17 stations usable in 2017-2020 (PLAN.md, "Power check"); first WGS84 location
STATIONS = {
    "ITCSM_01": (6.96722, 52.41472), "ITCSM_02": (6.85722, 52.39000), "ITCSM_03": (6.78992, 52.35061),
    "ITCSM_04": (6.92111, 52.27167), "ITCSM_05": (6.69944, 52.27333), "ITCSM_07": (6.96528, 52.37167),
    "ITCSM_08": (6.74500, 52.13583), "ITCSM_09": (6.84306, 52.14639), "ITCSM_10": (6.65944, 52.20000),
    "ITCSM_11": (6.55889, 52.23111), "ITCSM_12": (6.55972, 52.14028), "ITCSM_13": (6.41750, 52.19389),
    "ITCSM_14": (6.31722, 52.19167), "ITCSM_15": (6.61250, 52.30944), "ITCSM_16": (6.53525, 52.36781),
    "ITCSM_17": (6.54056, 52.42056), "ITCSM_18": (6.37991, 52.40528)}
S1_ASSETS = ("vv", "vh")
S2_ASSETS = ("B02", "B03", "B04", "B08", "B11", "B12", "SCL")
#: compressed bytes per pixel measured on the Step 3 sample (deflate, 256-px tiles)
STORED_BPP = {"s1": 3.6, "s2_10m": 1.55, "s2_20m": 1.75, "SCL": 0.07}


def _json(url, body=None):
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _search(coll, year):
    body = {"collections": [coll], "bbox": BBOX, "datetime": f"{year}-01-01/{year}-12-31", "limit": 1000}
    out = []
    while body:
        d = _json(f"{STAC}/search", body)
        out += d["features"]
        nxt = [l for l in d.get("links", []) if l.get("rel") == "next"]
        body = nxt[0].get("body") if nxt else None
    return out


def _chip_block_bytes(href, chip_m):
    """Compressed bytes of the blocks a chip around each station touches."""
    total, blocks_n = 0, 0
    with rasterio.open(href) as src:
        bw, bh = src.block_shapes[0][1], src.block_shapes[0][0]
        for lon, lat in STATIONS.values():
            x, y = transform("EPSG:4326", src.crs, [lon], [lat])
            r, c = src.index(x[0], y[0])
            half = chip_m / 2 / abs(src.res[0])
            rows = range(max(0, int((r - half) // bh)), min(math.ceil(src.height / bh), int((r + half) // bh) + 1))
            cols = range(max(0, int((c - half) // bw)), min(math.ceil(src.width / bw), int((c + half) // bw) + 1))
            for br in rows:
                for bc in cols:
                    size = src.get_tag_item(f"BLOCK_SIZE_{bc}_{br}", "TIFF", bidx=1)
                    if size:
                        total += int(size); blocks_n += 1
        return total, blocks_n, (bw, bh), src.res[0]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--chip-m", type=float, default=1000.0)
    a = ap.parse_args()

    s1 = _search("sentinel-1-rtc", a.year)
    s2 = _search("sentinel-2-l2a", a.year)
    passes = {(f["properties"]["datetime"][:13], f["properties"]["sat:relative_orbit"]) for f in s1}
    s2_dates = {f["properties"]["datetime"][:10] for f in s2}
    print(f"{a.year}: {len(passes)} S1 passes ({len(s1)} frames), {len(s2_dates)} S2 dates ({len(s2)} tile-items)")

    tok1 = _json(SAS.format("sentinel-1-rtc"))["token"]
    tok2 = _json(SAS.format("sentinel-2-l2a"))["token"]
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff")
    # one representative item each: S1 frame covering most stations, S2 tile 32ULC
    s1_item = max(s1, key=lambda f: sum(f["bbox"][0] <= lo <= f["bbox"][2] and f["bbox"][1] <= la <= f["bbox"][3]
                                        for lo, la in STATIONS.values()))
    s2_item = next(f for f in s2 if f["properties"]["s2:mgrs_tile"] == "32ULC")
    per_pass, per_date = 0, 0
    with rasterio.Env(**env):
        for asset in S1_ASSETS:
            b, n, blk, res = _chip_block_bytes(s1_item["assets"][asset]["href"] + "?" + tok1, a.chip_m)
            per_pass += b
            print(f"  S1 {asset}: {n} blocks of {blk} px for 17 chips, {b/1e6:.1f} MB per pass")
        for asset in S2_ASSETS:
            b, n, blk, res = _chip_block_bytes(s2_item["assets"][asset]["href"] + "?" + tok2, a.chip_m)
            per_date += b
            print(f"  S2 {asset} ({res:.0f} m): {n} blocks of {blk} px, {b/1e6:.1f} MB per date")

    transfer_s1 = per_pass * len(passes)
    transfer_s2 = per_date * len(s2_dates)
    px = (a.chip_m / 10) ** 2
    stored_s1 = len(passes) * 17 * 2 * px * STORED_BPP["s1"]
    stored_s2 = len(s2_dates) * 17 * (4 * px * STORED_BPP["s2_10m"] + 2 * px / 4 * STORED_BPP["s2_20m"]
                                       + px / 4 * STORED_BPP["SCL"])
    gt = 20 * (58.4e6 + 24.6e6)
    print(f"\nTransfer: S1 {transfer_s1/1e9:.1f} GB + S2 {transfer_s2/1e9:.1f} GB "
          f"= {(transfer_s1 + transfer_s2)/1e9:.1f} GB  (+ ground truth {gt/1e9:.2f} GB, all years in one file)")
    print(f"Stored chips: S1 {stored_s1/1e6:.0f} MB + S2 {stored_s2/1e6:.0f} MB; "
          f"ground truth {gt/1e9:.2f} GB")


if __name__ == "__main__":
    main()
