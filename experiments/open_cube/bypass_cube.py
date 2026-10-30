"""
Do the gates catch what the pipeline prevents upstream? (docs/open_ablation.md, "Why four
gates refused nothing")

    python experiments/open_cube/bypass_cube.py fetch    --raw ~/work/data/raw/open_cube/2019 \
        --bypass ~/work/data/raw/open_cube_bypass/2019
    python experiments/open_cube/bypass_cube.py assemble --raw ~/work/data/raw/open_cube/2019 \
        --bypass ~/work/data/raw/open_cube_bypass/2019 --out ~/work/results/open_cube/bypass_2019

A diagnostic, not a change to the method: fetch_year.py and build_cube.py are untouched.

The protections, and where they live:
  fetch_year.py  - S2: of the tiles containing a station, the best-scoring one (station
                   pixel, 3 x 3 window, chip; 2-pixel edge buffer; B02 >= 50 floor);
                   dates where no tile has a measured pixel are not written.
                 - S1: if the first containing frame is empty at the station, the next.
                 - S2 chips tagged station_measured: false (edge or fill at the station).
  build_cube.py  - S2 at or before the S1 pass (temporal ordering).
                 - Per-pixel rules at the station: SCL 4/5, B02 >= 50, S1 not nodata.

``fetch`` reads, for exactly the station-dates where a fetcher protection changed or
withheld a chip, the chip the naive rule would have taken: the first containing tile
(zone 32 first, as the fetcher orders them) or frame (by item id). Everything else is
already on disk and identical under both rules.

``assemble`` builds the cube under six rules and runs the five gates on each:
  protected     - as build_cube.py; must reproduce the documented cube
  first_tile    - naive first containing S2 tile and S1 frame
  future_s2     - the nearest usable S2 in either direction, so after the pass is allowed
  no_edge       - station_measured: false chips are usable
  bypass_all    - first_tile + future_s2 + no_edge
  bypass_pixel  - bypass_all, and build_cube's per-pixel rules off as well
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cube as bc                     # noqa: E402
import fetch_year as fy                     # noqa: E402

GATES = ("geometry_crs[point]", "geometry_crs", "resolution", "temporal_alignment",
         "quality_missingness[point]", "quality_missingness", "sensor_physics")
#: The method: geometry and quality in point mode. CHIP_METHOD is the method before
#: point-mode quality, kept so the before and after come from one run.
METHOD = ("geometry_crs[point]", "resolution", "temporal_alignment",
          "quality_missingness[point]", "sensor_physics")
CHIP_METHOD = ("geometry_crs[point]", "resolution", "temporal_alignment",
               "quality_missingness", "sensor_physics")
ARMS = {"protected": {}, "first_tile": {"first_tile": True}, "future_s2": {"future": True},
        "no_edge": {"no_edge": True},
        "bypass_all": {"first_tile": True, "future": True, "no_edge": True},
        "bypass_pixel": {"first_tile": True, "future": True, "no_edge": True, "no_pixel": True}}


def _first(tile_scores: dict) -> str:
    return sorted(tile_scores, key=lambda t: (not t.startswith("32"), t))[0]


# ------------------------------------------------------------------- fetch
def plan(raw: Path):
    """Station-dates where a fetcher protection changed or withheld the naive choice."""
    recs = [json.loads(l) for l in open(raw / "manifest.jsonl", encoding="utf-8")]
    s2, s1 = [], []
    for r in recs:
        if r["kind"] == "s2" and r.get("tile_scores"):
            if r["status"] == "ok" and r["file"].endswith("_SCL.tif"):
                chosen = r["file"].split("_")[2]
                if _first(r["tile_scores"]) != chosen:
                    s2.append({"station": r["station"], "date": r["datetime"][:10],
                               "tile": _first(r["tile_scores"]), "replaces": r["datetime"],
                               "why": "best_tile_not_first"})
            elif r["status"] == "no_measurement":
                s2.append({"station": r["station"], "date": r["datetime"][:10],
                           "tile": _first(r["tile_scores"]), "replaces": None,
                           "why": "no_tile_measured"})
        if r["kind"] == "s1" and r["status"] == "ok" and r["file"].endswith("_VV.tif") \
                and r["frames_tried"] > 1:
            s1.append({"station": r["station"], "item_id": r["item_id"], "replaces": r["datetime"],
                       "why": "first_frame_empty"})
    return s2, s1


def fetch(raw: Path, bypass: Path, year: int, workers: int):
    s2_plan, s1_plan = plan(raw)
    print(f"plan: {len(s2_plan)} S2 station-dates "
          f"({collections.Counter(p['why'] for p in s2_plan)}), {len(s1_plan)} S1 station-passes")
    bypass.mkdir(parents=True, exist_ok=True)
    man = fy.Manifest(bypass / "manifest.jsonl")
    pos = fy.station_positions(year)
    tokens = fy.Tokens()
    s2_items: dict[str, dict] = {}
    for f in fy.search("sentinel-2-l2a", year):
        s2_items.setdefault(f["properties"]["datetime"][:10], {})[f["properties"]["s2:mgrs_tile"]] = f
    s1_groups: dict[tuple, list] = {}
    by_id = {}
    for f in fy.search("sentinel-1-rtc", year):
        p = f["properties"]
        s1_groups.setdefault((p["datetime"][:13], p["sat:relative_orbit"]), []).append(f)
        by_id[f["id"]] = f

    def one_s2(task):
        stn, item = task["station"], s2_items.get(task["date"], {}).get(task["tile"])
        if item is None:
            man.add(kind="s2", **task, status="item_not_found"); return 0
        lon, lat = pos[stn]
        p = item["properties"]
        stamp = p["datetime"][:16].replace(":", "")
        n = 0
        for a in fy.S2_ASSETS:
            with rasterio.open(item["assets"][a]["href"] + "?" + tokens("sentinel-2-l2a")) as src:
                data, tr = fy.chip(src, lon, lat, 1000.0)
                if data is None:
                    man.add(kind="s2", **task, status="outside_tile"); return n
                name = f"S2_{stamp}_{task['tile']}_EPSG{p['proj:epsg']}_{a}.tif"
                fy.write(bypass / "s2" / stn / name, data, src.profile.copy(), tr,
                         {"item_id": item["id"], "asset": a, "datetime": p["datetime"],
                          "station": stn, "station_lonlat": f"{lon},{lat}", "bypass": task["why"]})
                n += 1
        man.add(kind="s2", **task, file=name, datetime=p["datetime"], status="ok")
        return n

    def one_s1(task):
        stn = task["station"]
        lon, lat = pos[stn]
        chosen = by_id[task["item_id"]]["properties"]
        group = s1_groups[(chosen["datetime"][:13], chosen["sat:relative_orbit"])]
        item = sorted((f for f in group if fy._inside(f, lon, lat)), key=lambda f: f["id"])[0]
        p = item["properties"]
        stamp = p["datetime"][:16].replace(":", "")
        base = (f"S1_{stamp}_rel{p['sat:relative_orbit']}_{p['sat:orbit_state'][:4]}"
                f"_EPSG{p['proj:epsg']}")
        for a in fy.S1_ASSETS:
            with rasterio.open(item["assets"][a]["href"] + "?" + tokens("sentinel-1-rtc")) as src:
                data, tr = fy.chip(src, lon, lat, 1000.0)
                if data is None:
                    man.add(kind="s1", **task, status="outside_frame"); return 0
                fy.write(bypass / "s1" / stn / f"{base}_{a.upper()}.tif", data, src.profile.copy(), tr,
                         {"item_id": item["id"], "asset": a, "datetime": p["datetime"],
                          "station": stn, "station_lonlat": f"{lon},{lat}", "bypass": task["why"]})
        man.add(kind="s1", **task, file=f"{base}_VV.tif", datetime=p["datetime"], status="ok")
        return 2

    with rasterio.Env(**fy.GDAL_ENV), ThreadPoolExecutor(workers) as pool:
        futs = [pool.submit(one_s2, t) for t in s2_plan] + [pool.submit(one_s1, t) for t in s1_plan]
        files = sum(f.result() for f in as_completed(futs))
    size = sum(f.stat().st_size for f in bypass.rglob("*.tif"))
    print(f"fetched {files} chips, {size / 1e6:.1f} MB stored")


# ------------------------------------------------------------------- assemble
def index(raw: Path, bypass: Path | None):
    """build_cube's S1/S2 index, with the naive chips substituted where they differ."""
    s1, s2 = collections.defaultdict(dict), collections.defaultdict(dict)
    for r in (json.loads(l) for l in open(raw / "manifest.jsonl", encoding="utf-8")):
        if r.get("status") != "ok" or r["kind"] not in ("s1", "s2"):
            continue
        stn, t = r["station"], bc.utc(r["datetime"])
        path = raw / r["kind"] / stn / r["file"]
        if r["kind"] == "s1":
            s1[stn].setdefault(t, {})[r["file"].rsplit("_", 1)[1][:2]] = path
        else:
            e = s2[stn].setdefault(t, {"files": {}, "measured": True})
            e["files"][r["file"].rsplit("_", 1)[1].split(".")[0]] = path
            if r.get("station_measured") is False:
                e["measured"] = False
    if bypass is not None:
        for r in (json.loads(l) for l in open(bypass / "manifest.jsonl", encoding="utf-8")):
            if r["status"] != "ok":
                continue
            stn, t = r["station"], bc.utc(r["datetime"])
            folder = bypass / r["kind"] / stn
            prefix = r["file"].rsplit("_", 1)[0]
            if r["replaces"]:
                (s1 if r["kind"] == "s1" else s2)[stn].pop(bc.utc(r["replaces"]), None)
            if r["kind"] == "s1":
                s1[stn][t] = {a: folder / f"{prefix}_{a}.tif" for a in ("VV", "VH")}
            else:                                         # the naive rule has no measured flag
                s2[stn][t] = {"files": {a: folder / f"{prefix}_{a}.tif" for a in fy.S2_ASSETS},
                              "measured": True}
    lonlat = {}
    for stn in s1:
        with rasterio.open(next(iter(next(iter(s1[stn].values())).values()))) as src:
            lonlat[stn] = tuple(map(float, src.tags()["station_lonlat"].split(",")))
    return s1, s2, lonlat


def build(raw: Path, bypass: Path, year: int, first_tile=False, future=False, no_edge=False,
          no_pixel=False):
    s1, s2, lonlat = index(raw, bypass if first_tile else None)
    samples, dropped = [], collections.Counter()
    for stn in sorted(s1):
        lon, lat = lonlat[stn]
        insitu = bc.load_insitu(raw / "ground_truth", stn, year)
        usable = []
        for t2, e in s2[stn].items():
            if "SCL" not in e["files"] or (not no_edge and not e["measured"]):
                continue
            if not no_pixel:
                scl = bc.pixel(e["files"]["SCL"], lon, lat)
                b02 = bc.pixel(e["files"]["B02"], lon, lat)
                if scl not in bc.SCL_USABLE or b02 is None or b02 < bc.B02_FLOOR:
                    continue
            usable.append(t2)
        for t1, pol in sorted(s1[stn].items()):
            if set(pol) != {"VV", "VH"}:
                dropped["s1_incomplete"] += 1; continue
            vv, vh = bc.pixel(pol["VV"], lon, lat), bc.pixel(pol["VH"], lon, lat)
            if not no_pixel and any(v is None or not math.isfinite(v) or v == bc.S1_NODATA or v <= 0
                                     for v in (vv, vh)):
                dropped["s1_nodata_at_station"] += 1; continue
            sm, why = bc.target(insitu, t1)
            if sm is None:
                dropped[why] += 1; continue
            cand = [t2 for t2 in usable if abs(t1 - t2) <= bc.LOOKBACK and (future or t2 <= t1)]
            if not cand:
                dropped["no_usable_s2_in_30d"] += 1; continue
            t2 = min(cand, key=lambda t: (abs(t1 - t), t > t1))
            f2 = s2[stn][t2]["files"]
            row = {"station": stn, "s1_time": t1.isoformat(), "s2_time": t2.isoformat(),
                   "s2_age_days": round((t1 - t2).total_seconds() / 86400, 3),
                   "s1_file": pol["VV"].name, "s2_file": f2["B02"].name}
            samples.append((row, pol, f2))
    return samples, dropped, lonlat


def run_arm(args):
    name, flags, raw, bypass, year = args
    samples, dropped, lonlat = build(raw, bypass, year, **flags)
    _, reasons, _, per = bc.run_gates(samples, 7.0, lonlat)
    keys = [(s[0]["station"], s[0]["s1_time"], s[0]["s2_time"], s[0]["s1_file"], s[0]["s2_file"])
            for s in samples]
    return name, keys, per, dict(dropped), {g: c.most_common(6) for g, c in reasons.items()}


def assemble(raw: Path, bypass: Path, out: Path, year: int, workers: int):
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(n, f, raw, bypass, year) for n, f in ARMS.items()]
    res = {}
    with ProcessPoolExecutor(workers) as pool:
        for name, keys, per, dropped, reasons in pool.map(run_arm, jobs):
            res[name] = (keys, per, dropped, reasons)
            print(f"  {name}: {len(keys)} samples", flush=True)
    protected = set(res["protected"][0])
    summary = {}
    for name, (keys, per, dropped, reasons) in res.items():
        changed = [k not in protected for k in keys]
        arm = {"samples": len(keys), "changed_vs_protected": sum(changed), "dropped": dropped,
               "reasons": reasons, "gates": {}}
        for g in GATES:
            v_all = collections.Counter(p[g] for p in per)
            v_chg = collections.Counter(p[g] for p, c in zip(per, changed) if c)
            arm["gates"][g] = {"all": dict(v_all), "changed": dict(v_chg)}
        for key, gates in (("", METHOD), ("_chip_quality", CHIP_METHOD)):
            arm[f"refused_any_all{key}"] = sum(any(p[g] == "refuse" for g in gates) for p in per)
            arm[f"refused_any_changed{key}"] = sum(any(p[g] == "refuse" for g in gates)
                                                   for p, c in zip(per, changed) if c)
        summary[name] = arm
    (out / "bypass.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    print(f"\n{'arm':14s} {'samples':>7} {'changed':>7} {'refused':>14} " +
          " ".join(f"{g[:14]:>16}" for g in GATES))
    for name, s in summary.items():
        n, c = s["samples"], s["changed_vs_protected"]
        ref = f"{s['refused_any_all']} ({s['refused_any_changed']} chg)"
        cells = []
        for g in GATES:
            a, ch = s["gates"][g]["all"].get("refuse", 0), s["gates"][g]["changed"].get("refuse", 0)
            cells.append(f"{a:>5}/{ch:<4}chg")
        print(f"{name:14s} {n:7d} {c:7d} {ref:>14} " + " ".join(f"{x:>16}" for x in cells))
    print("\nrefusals per gate: all samples / of which changed vs protected. 'refused' counts "
          "the five gates with geometry and quality in point mode.")
    for name, s in summary.items():
        print(f"  {name}: changed samples refused {s['refused_any_changed']} of "
              f"{s['changed_vs_protected']} (point quality), "
              f"{s['refused_any_changed_chip_quality']} with chip quality")


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=("fetch", "assemble"))
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--bypass", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 4)))
    a = ap.parse_args()
    raw, bypass = a.raw.expanduser(), a.bypass.expanduser()
    if a.stage == "fetch":
        fetch(raw, bypass, a.year, a.workers)
    else:
        assemble(raw, bypass, a.out.expanduser(), a.year, a.workers)


if __name__ == "__main__":
    main()
