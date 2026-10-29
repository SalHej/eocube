"""
Assemble the open cube for one year and run the five contract gates on every sample.

    python experiments/open_cube/build_cube.py --raw ~/work/data/raw/open_cube/2019 \
        --out ~/work/data/cubes/open_cube/2019

Input is what fetch_year.py wrote: s1/, s2/, ground_truth/ per station and
manifest.jsonl. One sample per S1 pass per station (PLAN.md, "Asynchrony"):

- target time   = the S1 pass time
- S1 features   = VV, VH at the station pixel (gamma0, linear power)
- S2 features   = B02 B03 B04 B08 B11 B12 at the station pixel, from the newest S2
                  observation AT OR BEFORE the pass that is usable at the station pixel
- ground truth  = mean of flag-0 5 cm readings within +/-30 min of the pass
                  (CSV times are CET = UTC+1), frozen soil (5 cm ST <= 0 C) excluded

Per-pixel masking at the station (PLAN.md, matching rules 6-7). A layer-level gate
cannot protect the one pixel that is the sample. An S2 observation is usable at the
station only if:
  - the fetch did not tag it station_measured: false (edge or fill),
  - SCL at the station pixel is 4 (vegetation) or 5 (not vegetated), and
  - B02 >= 50 DN.
SCL 6 (water) and 7 (unclassified) are excluded from training and counted separately
per station. Cloud, shadow, snow and saturated pixels make the scene unusable there,
and the next older scene is tried.

The gates then run on each sample's real layers (the chips it was read from), with
properties declared from the source metadata, never adjusted to make a gate pass.
Geometry is checked twice, undecided between the two (docs/cube_2019.md):
  - grid mode, the default: the layers must share one grid. This cube reads each
    sensor at the station in its own CRS, so mixed UTM zones are refused.
  - point mode, reported alongside: every layer must have been read at the same
    declared ground point, consistently, wherever its grid lies.
Outputs: samples.csv, dropped.csv, gates.json, summary.json.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eocube import check_geometry_crs, run_all_gates   # noqa: E402
from eocube.io import describe              # noqa: E402

S2_BANDS = ("B02", "B03", "B04", "B08", "B11", "B12")
SCL_USABLE = (4, 5)                          # vegetation, not vegetated
SCL_WATER_UNCLASSIFIED = (6, 7)
B02_FLOOR = 50
S1_NODATA = -32768.0
CET = timedelta(hours=1)                     # in-situ CSV times, no daylight saving
MATCH = timedelta(minutes=30)
LOOKBACK = timedelta(days=30)                # how far back an S2 observation is searched
EAST = ("ITCSM_01", "ITCSM_04", "ITCSM_07", "ITCSM_09")

#: Declared properties, from the products' documentation, not from the pixels.
#: Plausible ranges are physical bounds (flagged guesses for S1; see docs/cube_2019.md).
DECLARED_S1 = {"modality": "sar", "sensor": "Sentinel-1", "units": "linear", "calibrated": True,
               "terrain_corrected": True, "processing_level": "RTC gamma0",
               "plausible_range": (1e-5, 10.0)}
DECLARED_S2 = {"modality": "multispectral", "sensor": "Sentinel-2", "reflectance": True,
               "processing_level": "L2A", "qa_mask_available": True, "qa_mask_applied": True,
               "plausible_range": (B02_FLOOR, 12000)}


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ------------------------------------------------------------------- ground truth
def load_insitu(gt: Path, station: str, year: int) -> dict[datetime, tuple[float, float, str]]:
    """{UTC time: (5 cm SM, 5 cm ST, SM flag of the 5 cm probe)} for the year."""
    def rows(path):
        with open(path, encoding="latin-1") as fh:
            for line in fh:
                if line.startswith("Date time"):
                    yield [h.strip() for h in line.split(",")]
                    break
            for line in fh:
                p = line.split(",")
                if len(p) > 2 and len(p[0]) >= 16 and p[0][6:10] == str(year):
                    yield [x.strip() for x in p]
    cd = rows(gt / f"{station}_cd.csv")
    hdr = next(cd)
    if "5 cm SM" not in hdr:
        return {}
    c_sm, c_st = hdr.index("5 cm SM"), hdr.index("5 cm ST")
    probe = [h for h in hdr if h.endswith(" SM")].index("5 cm SM")
    flags = {}
    fg = rows(gt / f"{station}_fg.csv")
    next(fg)
    for p in fg:
        flags[p[0]] = p[1][2 + probe] if len(p[1]) > 2 + probe else "9"
    out = {}
    for p in cd:
        t = datetime.strptime(p[0], "%d-%m-%Y %H:%M").replace(tzinfo=timezone.utc) - CET
        out[t] = (float(p[c_sm]), float(p[c_st]), flags.get(p[0], "9"))
    return out


def target(insitu, t: datetime):
    """(soil moisture, reason): mean flag-0 5 cm SM within +/-30 min, unfrozen."""
    near = [v for k, v in insitu.items() if abs(k - t) <= MATCH]
    if not near:
        return None, "no_insitu"
    good = [v for v in near if v[2] == "0" and v[0] > -99]
    if not good:
        return None, "insitu_flagged"
    temps = [v[1] for v in good if v[1] > -99]
    if temps and st.mean(temps) <= 0:
        return None, "frozen"
    return st.mean(v[0] for v in good), "ok"


# ------------------------------------------------------------------- pixels
def pixel(path: Path, lon: float, lat: float):
    with rasterio.open(path) as src:
        x, y = transform("EPSG:4326", src.crs, [lon], [lat])
        r, c = src.index(x[0], y[0])
        if not (0 <= r < src.height and 0 <= c < src.width):
            return None
        return src.read(1, window=((r, r + 1), (c, c + 1)))[0, 0].item()


def scl_fractions(path: Path) -> tuple[float, float]:
    """Fraction of the chip that is water/unclassified, and that is SCL-usable."""
    with rasterio.open(path) as src:
        a = src.read(1)
    return float(np.isin(a, SCL_WATER_UNCLASSIFIED).mean()), float(np.isin(a, SCL_USABLE).mean())


# ------------------------------------------------------------------- assembly
def assemble(raw: Path, year: int):
    recs = [json.loads(l) for l in open(raw / "manifest.jsonl", encoding="utf-8")]
    s1 = collections.defaultdict(dict)        # station -> {pass time: {VV: path, VH: path}}
    s2 = collections.defaultdict(dict)        # station -> {time: {"files": {...}, "measured": bool}}
    lonlat = {}
    for r in recs:
        if r.get("status") != "ok" or r["kind"] not in ("s1", "s2"):
            continue
        stn, t = r["station"], utc(r["datetime"])
        path = raw / r["kind"] / stn / r["file"]
        if r["kind"] == "s1":
            s1[stn].setdefault(t, {})[r["file"].rsplit("_", 1)[1][:2]] = path
        else:
            e = s2[stn].setdefault(t, {"files": {}, "measured": True})
            e["files"][r["file"].rsplit("_", 1)[1].split(".")[0]] = path
            if r.get("station_measured") is False:
                e["measured"] = False
    for stn in s1:
        any_file = next(iter(next(iter(s1[stn].values())).values()))
        with rasterio.open(any_file) as src:
            lon, lat = map(float, src.tags()["station_lonlat"].split(","))
        lonlat[stn] = (lon, lat)

    samples, dropped = [], []
    water = collections.Counter()             # station -> station pixels excluded as SCL 6/7
    chip_water = collections.defaultdict(list)
    s2_status = {}                            # (station, time) -> usable / reason
    for stn in sorted(s1):
        lon, lat = lonlat[stn]
        insitu = load_insitu(raw / "ground_truth", stn, year)
        for t2, e in s2[stn].items():
            if "SCL" not in e["files"]:
                s2_status[(stn, t2)] = "incomplete"
                continue
            wf, _ = scl_fractions(e["files"]["SCL"])
            chip_water[stn].append(wf)
            if not e["measured"]:
                s2_status[(stn, t2)] = "edge_or_fill"
                continue
            scl = pixel(e["files"]["SCL"], lon, lat)
            b02 = pixel(e["files"]["B02"], lon, lat)
            if scl in SCL_WATER_UNCLASSIFIED:
                s2_status[(stn, t2)] = "water_unclassified"
            elif scl not in SCL_USABLE:
                s2_status[(stn, t2)] = f"scl_{scl}"
            elif b02 is None or b02 < B02_FLOOR:
                s2_status[(stn, t2)] = "b02_below_floor"
            else:
                s2_status[(stn, t2)] = "usable"
        water[stn] = sum(1 for (s, _), v in s2_status.items() if s == stn and v == "water_unclassified")
        s2_times = sorted(s2[stn])
        for t1, pol in sorted(s1[stn].items()):
            base = {"station": stn, "s1_time": t1.isoformat()}
            if set(pol) != {"VV", "VH"}:
                dropped.append({**base, "reason": "s1_incomplete"}); continue
            vv, vh = pixel(pol["VV"], lon, lat), pixel(pol["VH"], lon, lat)
            if any(v is None or not math.isfinite(v) or v == S1_NODATA or v <= 0 for v in (vv, vh)):
                dropped.append({**base, "reason": "s1_nodata_at_station"}); continue
            sm, why = target(insitu, t1)
            if sm is None:
                dropped.append({**base, "reason": why}); continue
            chosen = next((t2 for t2 in reversed(s2_times)
                           if t2 <= t1 and t1 - t2 <= LOOKBACK and s2_status.get((stn, t2)) == "usable"),
                          None)
            if chosen is None:
                dropped.append({**base, "reason": "no_usable_s2_in_30d"}); continue
            f2 = s2[stn][chosen]["files"]
            row = {**base, "s2_time": chosen.isoformat(),
                   "s2_age_days": round((t1 - chosen).total_seconds() / 86400, 3),
                   "sm": round(sm, 4), "vv": vv, "vh": vh,
                   **{b: pixel(f2[b], lon, lat) for b in S2_BANDS},
                   "s1_file": pol["VV"].name, "s2_tile": f2["B02"].name.split("_")[2],
                   "east": stn in EAST}
            samples.append((row, pol, f2))
    return samples, dropped, water, chip_water, s2_status, lonlat


# ------------------------------------------------------------------- gates
def run_gates(samples, window_days: float, lonlat: dict):
    cache = {}

    def desc(path: Path, name: str, declared: dict, station: str):
        if path not in cache:
            cache[path] = describe(path, name,
                                   declared={**declared, "sample_point": lonlat[station]})
        return cache[path]

    verdicts = collections.defaultdict(collections.Counter)
    reasons = collections.defaultdict(collections.Counter)
    decisions = collections.defaultdict(collections.Counter)
    per_sample = []
    for row, pol, f2 in samples:
        stn = row["station"]
        layers = [desc(pol["VV"], "S1_VV", DECLARED_S1, stn), desc(pol["VH"], "S1_VH", DECLARED_S1, stn)]
        layers += [desc(f2[b], f"S2_{b}", {**DECLARED_S2, **({} if b == "B02" else {"plausible_range": (0, 12000)})}, stn)
                   for b in S2_BANDS]
        t1, t2 = utc(row["s1_time"]), utc(row["s2_time"])
        rep = run_all_gates(layers, acquisitions={"S1": [t1], "S2": [t2]}, target_time=t1,
                            window_days=window_days)
        v = {}
        for g in rep.results:
            verdicts[g.gate][g.verdict.value] += 1
            v[g.gate] = g.verdict.value
            for r in g.reasons:
                reasons[g.gate][_generic(r)] += 1
            for d in g.decisions:
                decisions[g.gate][_generic(d)] += 1
        point = check_geometry_crs(layers, mode="point")
        verdicts["geometry_crs[point]"][point.verdict.value] += 1
        for r in point.reasons:
            reasons["geometry_crs[point]"][_generic(r)] += 1
        crs_pair = (layers[0]["crs"], layers[2]["crs"])
        verdicts["crs_pair S1|S2"][" | ".join(map(str, crs_pair))] += 1
        per_sample.append({"station": row["station"], "s1_time": row["s1_time"], **v,
                           "geometry_crs[point]": point.verdict.value,
                           "point_max_pairwise_centre_m": point.evidence.get("max_pairwise_centre_m"),
                           "crs_pair": list(crs_pair)})
    return verdicts, reasons, decisions, per_sample


def _generic(msg: str) -> str:
    """Collapse per-layer and per-value detail so identical findings count together."""
    import re
    msg = re.sub(r"'S[12]_[A-Z0-9]+'", "'<layer>'", msg)
    msg = re.sub(r"\d+\.\d+(%| m| d| px)?", "<n>\\1", msg)
    return msg[:240]


# ------------------------------------------------------------------- main
def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--window-days", type=float, default=7.0)
    a = ap.parse_args()
    raw, out = a.raw.expanduser(), a.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)

    samples, dropped, water, chip_water, s2_status, lonlat = assemble(raw, a.year)
    rows = [s[0] for s in samples]
    with open(out / "samples.csv", "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with open(out / "dropped.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["station", "s1_time", "reason"]); w.writeheader(); w.writerows(dropped)

    verdicts, reasons, decisions, per_sample = run_gates(samples, a.window_days, lonlat)

    stations = sorted({r["station"] for r in rows} | {d["station"] for d in dropped})
    per_station = {}
    for s in stations:
        ages = [r["s2_age_days"] for r in rows if r["station"] == s]
        per_station[s] = {
            "samples": len(ages),
            "dropped": dict(collections.Counter(d["reason"] for d in dropped if d["station"] == s)),
            "s2_status": dict(collections.Counter(v for (t, _), v in s2_status.items() if t == s)),
            "s2_dates_water_unclassified_at_station": water[s],
            "chip_water_unclassified_fraction_median": round(st.median(chip_water[s]), 4) if chip_water[s] else None,
            "s2_age_days_mean": round(st.mean(ages), 3) if ages else None,
            "east": s in EAST}
    east = [r["s2_age_days"] for r in rows if r["east"]]
    rest = [r["s2_age_days"] for r in rows if not r["east"]]
    summary = {
        "year": a.year, "samples": len(rows), "dropped": dict(collections.Counter(d["reason"] for d in dropped)),
        "window_days": a.window_days,
        "east_west_s2_age_cloud_aware": {
            "east_mean": round(st.mean(east), 3) if east else None,
            "east_median": round(st.median(east), 3) if east else None,
            "rest_mean": round(st.mean(rest), 3) if rest else None,
            "rest_median": round(st.median(rest), 3) if rest else None,
            "ratio": round(st.mean(east) / st.mean(rest), 3) if east and rest else None},
        "per_station": per_station}
    gates = {"verdicts": {g: dict(c) for g, c in verdicts.items()},
             "reasons": {g: c.most_common(12) for g, c in reasons.items()},
             "decisions": {g: c.most_common(12) for g, c in decisions.items()}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "gates.json").write_text(json.dumps({**gates, "per_sample": per_sample}, indent=1), encoding="utf-8")
    print(json.dumps({"summary": {k: v for k, v in summary.items() if k != "per_station"},
                      **gates}, indent=1))


if __name__ == "__main__":
    main()
