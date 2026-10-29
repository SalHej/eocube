"""End-to-end test of experiments/open_cube/build_cube.py on a synthetic raw dataset.

One station, chips in fetch_year.py's layout, a manifest, and DANS-format in-situ
CSVs. Each rule in PLAN.md has a case built to trigger it. No real imagery is used.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import transform  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "open_cube"))
import build_cube as bc  # noqa: E402

STN, LON, LAT = "ITCSM_08", 6.745, 52.13583
YEAR = 2019


def _chip(path, value, crs, res, n, dtype, nodata, tags):
    x, y = transform("EPSG:4326", crs, [LON], [LAT])
    tr = from_origin(x[0] - res * n / 2, y[0] + res * n / 2, res, res)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype=dtype,
                       crs=crs, transform=tr, nodata=nodata) as dst:
        dst.write(np.full((1, n, n), value, dtype=dtype))
        dst.update_tags(station_lonlat=f"{LON},{LAT}", **tags)


@pytest.fixture
def raw(tmp_path):
    root = tmp_path / "2019"
    man = []
    # S2 observations (EPSG:32631, as for the real western tiles); SCL value at station
    s2 = [("2019-11-01T10:50:00Z", 4, 800, True),      # usable
          ("2019-11-05T10:40:00Z", 9, 6000, True),     # cloud
          ("2019-11-08T10:50:00Z", 6, 30, True),       # water
          ("2019-11-12T10:40:00Z", 4, 900, False),     # edge/fill: station_measured false
          ("2019-11-15T10:50:00Z", 4, 850, True)]      # usable, later
    for t, scl, b02, measured in s2:
        stamp = t[:16].replace(":", "")
        for band in bc.S2_BANDS + ("SCL",):
            res, n = (20, 5) if band in ("B11", "B12", "SCL") else (10, 10)
            val = scl if band == "SCL" else (b02 if band == "B02" else 1500)
            name = f"S2_{stamp}_31UGT_EPSG32631_{band}.tif"
            _chip(root / "s2" / STN / name, val, "EPSG:32631", res, n,
                  "uint8" if band == "SCL" else "uint16", 0, {"datetime": t})
            man.append({"kind": "s2", "station": STN, "file": name, "datetime": t,
                        "status": "ok", "station_measured": measured})
    # S1 passes (EPSG:32632)
    s1 = [("2019-11-06T05:45:00Z", 0.05),   # -> S2 of 11-01 (11-05 is cloud)
          ("2019-11-13T17:20:00Z", 0.06),   # -> 11-01 again: 11-12 edge, 11-08 water, 11-05 cloud
          ("2019-11-16T05:45:00Z", 0.07),   # -> 11-15
          ("2019-11-20T05:45:00Z", 0.05),   # in-situ flagged
          ("2019-11-21T05:45:00Z", 0.05),   # frozen
          ("2019-11-22T05:45:00Z", bc.S1_NODATA)]   # S1 nodata at the station
    for t, v in s1:
        stamp = t[:16].replace(":", "")
        for pol in ("VV", "VH"):
            name = f"S1_{stamp}_rel37_desc_EPSG32632_{pol}.tif"
            _chip(root / "s1" / STN / name, v, "EPSG:32632", 10, 10, "float32",
                  bc.S1_NODATA, {"datetime": t})
            man.append({"kind": "s1", "station": STN, "file": name, "datetime": t, "status": "ok"})
    (root / "manifest.jsonl").write_text("\n".join(json.dumps(m) for m in man), encoding="utf-8")
    # in-situ, CET, every 15 min around each pass; flags SM probe 1
    gt = root / "ground_truth"
    gt.mkdir()
    head = "-------------------\nheader\n-------------------\n"
    cd = [head + "Date time, 5 cm SM, 5 cm ST, 10 cm SM, 10 cm ST\n"]
    fg = [head + "Date time, Soil M. QFlag, Soil T. QFlag, Meas S. Qflag, Probe Qflag\n"
          "dd-mm-yyyy hh:mm, SMxxxxx, STxxxxx, MSxxxxx, PRxxxxx\n"]
    for t, _ in s1:
        tc = datetime.fromisoformat(t[:19]) + timedelta(hours=1)
        flagged = t.startswith("2019-11-20")
        frozen = t.startswith("2019-11-21")
        for m in (-15, 0, 15):
            k = (tc + timedelta(minutes=m)).strftime("%d-%m-%Y %H:%M")
            cd.append(f"{k},     0.250,  {-2.0 if frozen else 5.0},     0.300,       6.0\n")
            fg.append(f"{k}, SM{3 if flagged else 0}0000, ST00000, MS00000, PR11000\n")
    (gt / f"{STN}_cd.csv").write_text("".join(cd), encoding="latin-1")
    (gt / f"{STN}_fg.csv").write_text("".join(fg), encoding="latin-1")
    return root


def test_assembly_rules(raw):
    samples, dropped, water, _, status, _ = bc.assemble(raw, YEAR)
    rows = {s[0]["s1_time"][:10]: s[0] for s in samples}
    assert set(rows) == {"2019-11-06", "2019-11-13", "2019-11-16"}
    assert rows["2019-11-06"]["s2_time"].startswith("2019-11-01")    # cloud skipped
    assert rows["2019-11-13"]["s2_time"].startswith("2019-11-01")    # edge, water, cloud skipped
    assert rows["2019-11-16"]["s2_time"].startswith("2019-11-15")
    for r in rows.values():                                          # never a later S2
        assert r["s2_time"] <= r["s1_time"]
    assert rows["2019-11-13"]["s2_age_days"] == pytest.approx(12.271, abs=0.01)
    assert rows["2019-11-06"]["sm"] == pytest.approx(0.25)
    reasons = {d["s1_time"][:10]: d["reason"] for d in dropped}
    assert reasons == {"2019-11-20": "insitu_flagged", "2019-11-21": "frozen",
                       "2019-11-22": "s1_nodata_at_station"}
    assert water[STN] == 1                                           # SCL 6 counted
    assert sorted(status.values()) == ["edge_or_fill", "scl_9", "usable", "usable",
                                       "water_unclassified"]


def test_gates_see_mixed_crs_and_stale_s2(raw):
    samples, _, _, _, _, lonlat = bc.assemble(raw, YEAR)
    verdicts, reasons, decisions, per_sample = bc.run_gates(samples, 7.0, lonlat)
    assert verdicts["geometry_crs"] == {"refuse": 3}                 # 32632 S1 vs 32631 S2
    assert verdicts["geometry_crs[point]"] == {"pass_with_decision": 3}   # same point, read consistently
    assert verdicts["crs_pair S1|S2"] == {"EPSG:32632 | EPSG:32631": 3}
    by_day = {p["s1_time"][:10]: p for p in per_sample}
    assert by_day["2019-11-13"]["temporal_alignment"] == "pass_with_decision"   # 12.3 d > 7
    assert by_day["2019-11-16"]["temporal_alignment"] == "pass"
    assert set(verdicts["resolution"]) == {"pass_with_decision"}     # 10 m and 20 m


def test_main_writes_outputs(raw, tmp_path, monkeypatch):
    out = tmp_path / "cube"
    monkeypatch.setattr(sys, "argv", ["build_cube", "--raw", str(raw), "--out", str(out)])
    bc.main()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["samples"] == 3
    assert summary["per_station"][STN]["s2_dates_water_unclassified_at_station"] == 1
    assert (out / "samples.csv").read_text(encoding="utf-8").count("\n") == 4
