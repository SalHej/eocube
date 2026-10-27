"""Dynamic range of Twente 5 cm soil moisture, 2017-2020, flag-0 readings only.

    python sm_stats.py local                 # stations in data/open_cube/ground_truth
    python sm_stats.py stream ITCSM_01 ...   # range-read 2017-2020 from DANS, nothing saved

Samples at S1-like times (05:45 and 17:20 UTC, i.e. 06:45 / 18:20 CET) so the
statistics describe what the ablation would actually see, not 15-min noise.
Writes sm_series_<station>.json (small) for power_check.py, in the current directory.
"""
import json
import statistics as stt
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
GT = Path("data/open_cube/ground_truth")
DANS = "https://phys-techsciences.datastations.nl/api/access/datafile/{}"
DATASET = ("https://phys-techsciences.datastations.nl/api/datasets/:persistentId/"
           "?persistentId=doi:10.17026/dans-znj-wyg5")
with urllib.request.urlopen(DATASET, timeout=120) as _r:
    FILES = json.load(_r)["data"]["latestVersion"]["files"]
IDS = {f["label"]: f["dataFile"]["id"] for f in FILES}
TIMES = {"06:45", "18:15", "18:30"}       # CET slots nearest the S1 passes
YEARS = ("2017", "2018", "2019", "2020")
SENSOR_RMSE = 0.025                        # calibrated accuracy, readme 1.c (0.023-0.028)


def _get(url, start, end=None):
    rng = f"bytes={start}-" + ("" if end is None else str(end))
    return urllib.request.urlopen(urllib.request.Request(url, headers={"Range": rng}), timeout=300)


def _offset_of_2017(url, size):
    """Byte offset of a line before 01-01-2017, by binary search on small range reads."""
    lo, hi = 0, size
    while hi - lo > 200_000:
        mid = (lo + hi) // 2
        chunk = _get(url, mid, mid + 400).read().decode("latin-1").split("\n")
        yr = next((c[6:10] for c in chunk[1:] if len(c) > 10 and c[2] == "-"), None)
        if yr is None or yr >= "2017":
            hi = mid
        else:
            lo = mid
    return lo


def lines(station, kind, mode):
    name = f"{station}_{kind}.csv"
    if mode == "local":
        with open(GT / name, encoding="latin-1") as fh:
            yield from fh
        return
    url = DANS.format(IDS[name])
    with urllib.request.urlopen(url, timeout=300) as r:      # header block only
        size = int(r.headers["Content-Length"])
        for raw in r:
            l = raw.decode("latin-1")
            yield l
            if l.startswith("Date time"):
                break
    with _get(url, _offset_of_2017(url, size)) as r:
        next(r)                                               # partial first line
        for raw in r:
            yield raw.decode("latin-1")


def series(station, mode):
    """{(date, slot): sm} for flag-0 5 cm readings at S1-like times."""
    flags, out, col = {}, {}, None
    it = lines(station, "fg", mode)
    for l in it:
        if l.startswith("Date time"):
            break
    for l in it:
        p = l.split(",")
        if len(p) > 1 and p[0][6:10] in YEARS and p[0][-5:].strip() in TIMES:
            flags[p[0].strip()] = p[1].strip()[2]           # 'SM' + 5 digits; probe 1
    it = lines(station, "cd", mode)
    for l in it:
        if l.startswith("Date time"):
            hdr = [h.strip() for h in l.split(",")]
            col = hdr.index("5 cm SM") if "5 cm SM" in hdr else None
            break
    if col is None:
        return {}, "no 5 cm probe"
    for l in it:
        p = l.split(",")
        key = p[0].strip()
        if key in flags and flags[key] == "0":
            v = float(p[col])
            if v > -99:
                out[key] = v
    return out, f"probe1 is {hdr[1]}"


def describe(station, s):
    v = list(s.values())
    if len(v) < 50:
        return None
    by_month = defaultdict(list)
    for k, x in s.items():
        by_month[int(k[3:5])].append(x)
    q = sorted(v)
    p5, p95 = q[int(.05 * len(q))], q[int(.95 * len(q))]
    sd = stt.pstdev(v)
    # day-to-day change between consecutive slots: how much moves between passes
    keys = sorted(s, key=lambda k: (k[6:10], k[3:5], k[:2], k[11:]))
    steps = [abs(s[b] - s[a]) for a, b in zip(keys, keys[1:])]
    return {"n": len(v), "mean": round(stt.mean(v), 3), "sd": round(sd, 3),
            "p5": round(p5, 3), "p95": round(p95, 3),
            "snr_var": round(sd**2 / SENSOR_RMSE**2, 1),
            "median_step": round(stt.median(steps), 4),
            "summer_sd": round(stt.pstdev([x for m in (6, 7, 8) for x in by_month.get(m, [])] or [0]), 3),
            "winter_sd": round(stt.pstdev([x for m in (11, 12, 1, 2, 3) for x in by_month.get(m, [])] or [0]), 3),
            "monthly_mean": {m: round(stt.mean(v), 3) for m, v in sorted(by_month.items()) if v}}


if __name__ == "__main__":
    mode, stations = sys.argv[1], sys.argv[2:] or ["ITCSM_08", "ITCSM_09"]
    allv = {}
    for st in stations:
        s, note = series(st, mode)
        d = describe(st, s)
        print(st, note, json.dumps(d))
        if d:
            json.dump(s, open(f"sm_series_{st}.json", "w"))
    json.dump({k: v for k, v in allv.items()},
              open(f"sm_{'_'.join(s[-2:] for s in stations)}.json", "w"))
