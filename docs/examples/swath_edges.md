# Worked example: swath and frame edges at the Twente stations

**Gates and code involved:** `fetch_year.py` (frame and tile selection);
`check_quality_missingness` (new `plausible_range` check).
**Data:** Sentinel-1 RTC and Sentinel-2 L2A over the Twente soil-moisture network, 2019,
from Microsoft Planetary Computer.
**Reproduce:** `python experiments/open_cube/edge_cases.py` (catalogue search,
metadata only), then `python experiments/open_cube/edge_verify.py` (checks each case
against the raw pixel at the station, in every frame or tile of the acquisition).

## Why this was checked

Before fetching a full year of chips, the frame and tile choice was tested on cases
chosen to be hard. "The station is inside the footprint" is a statement about
catalogue metadata. The pixel at the station is the ground truth. Every case below
compares the two.

**11 cases, all verified against raw pixels.** A planned 12th category (a station
0–1 km inside a detailed footprint while a tile-square footprint contains it) had no
candidate in 2019.

## How the cases were found

`edge_cases.py` measured, for every 2019 acquisition and every one of the 17 stations,
the distance from the station to the nearest footprint edge:

- **Sentinel-1:** 1,328 station-passes lie within 2 km of the containing frame's edge,
  or inside a frame's bounding box but outside its footprint (a "bbox trap"). On one
  pass a station lies in **no** frame.
- **Sentinel-2:** 420 station-dates have the best tile's data edge within 2 km, or the
  station outside every tile's data footprint.
- **287 of 578** 2019 Sentinel-2 footprints are bare 5-point tile squares, not data
  footprints.

## Sentinel-1: 4 cases, footprint agreed with the pixels in all 8 frame checks

| Pass (UTC), orbit | Station | Frame | Footprint contains station? | Raw γ⁰ VV at station |
|---|---|---|---|---|
| 2019-03-23 05:42, rel 139 | ITCSM_16 | `…054224` | no | NODATA |
| | | `…054159` | no | NODATA |
| 2019-03-11 05:42, rel 139 | ITCSM_16 | `…054224` | no (bbox trap) | NODATA |
| | | `…054159` | **yes**, 726 m inside | **0.126** |
| 2019-03-23 05:42, rel 139 | ITCSM_03 | `…054224` | no (bbox trap) | NODATA |
| | | `…054159` | **yes**, 976 m inside | **0.0860** |
| 2019-01-31 17:16, rel 15 | ITCSM_09 | `…171641` | **yes** | **0.155** |
| | | `…171616` | no (bbox trap) | NODATA |

- **The 111 m case (ITCSM_16, 03-23):** the station is 111 m outside both frames'
  footprints but inside both bounding boxes. Both frames are NODATA there.
  `fetch_year.py` records `no_frame` and loses no data. The bounding-box selection it
  replaced would have written two all-nodata chips.
- **Every bbox trap was NODATA,** and every footprint that claimed the station had data
  there. **No change to S1 selection was needed.**
- **Chips can still be partly empty.** With the station 726 m and 976 m inside the
  footprint, the 1 km chip crosses the frame edge, and the chips were **25.2% and
  36.8% nodata**. The station pixel is valid, but a layer-level quality figure for
  that chip would be over the 30% threshold. See the last section.

## Sentinel-2: 7 cases

| Date | Station | Tile | Footprint (points) | Tile nodata | Contains? | Raw B02 at station (DN) |
|---|---|---|---|---|---|---|
| **2019-06-25** | ITCSM_08 | 31UGT | 12 | 58% | yes | **1260** |
| | | **32ULC** | 12 | 69% | yes | **1 — fill that reads as data** |
| | | 31UGU, 32ULD | 12 | — | no | off-raster |
| 2019-10-03 | ITCSM_08 | 31UGT | 11 | 58% | yes | 4124 (bright cloud) |
| | | 32ULC | 12 | 69% | yes | 4116 (bright cloud) |
| 2019-08-29 | ITCSM_08 | 31UGT | 12 | 58% | yes | 7736 (bright cloud) |
| | | 32ULC | 12 | 69% | yes | 7648 (bright cloud) |
| 2019-12-27 | ITCSM_04 | all four | 12–14 | 33–69% | no | NODATA in all four |
| 2019-02-07 | ITCSM_17 | 31UGU / 32ULD | **5 (tile square)** | 0% | yes | 422 / 386 |
| 2019-02-07 | ITCSM_18 | 31UGU / 32ULD | **5 (tile square)** | 0% | yes | 3460 / 3356 (cloud) |
| 2019-02-07 | ITCSM_01 | 31UGU / 32ULD | **5 (tile square)** | 0% | yes | 500 / 479 |

**The placeholder (5-point) footprints were honest** in all three trap cases: each sat on
a tile with no nodata, and the pixels had data. The nearby edge was another tile's
border, not the swath. **Footprints with real data edges (11–14 points) were also
consistent with the pixels.** The failure was elsewhere.

### 2019-06-25, ITCSM_08: a fill value that reads as data

- **Why it is a fill value:** 32ULC's B02 at the station is **1 DN**, a reflectance of
  0.0001. That is not a land surface. SCL there does not say "no data", and B02 is not
  0, so it is not flagged as nodata either. Neither nodata nor SCL can see it.
- **Same acquisition, two grids:** 31UGT and 32ULC show the **same acquisition** on two
  UTM grids, with the swath edge running through the 1 km chip, close to the station.
  One grid puts the station on the 1-pixel fill strip along the edge, the other one
  pixel inside it (1260).
- **The fill is a thin strip:** 0.7% of the chip (`0 < B02 < 50`); 48.8% of the chip
  is nodata.
- **How each version of the code fared:**

| Selection rule | Choice | Station B02 |
|---|---|---|
| Bounding box, then "first tile not all-nodata, zone 32 first" | 32ULC | **1** |
| "Tile with most real-measurement pixels in the chip" | 32ULC (tie: 0.512 vs 0.512) | **1** |
| **Final rule:** station pixel, then 3 × 3 window, then chip, with a 2-pixel edge buffer | see below | |

The chip-level rule failed because the two grids of one acquisition score the same.
The final rule judges **the station's own pixel first**. It counts a pixel as a real
measurement only if:
- SCL is not 0 or 1,
- B02 ≥ 50 DN (water exempt), and
- it lies at least 2 pixels from the nodata boundary, since a value beside the edge is
  resampled from the edge.

Then the 3 × 3 window, then the whole chip. **Cloud counts as a measurement for tile
choice.** On 10-03 and 08-29 both tiles show the same bright cloud (~4100 and ~7700
DN). Cloud is a per-pixel validity question for the cube. Letting it steer tile choice,
or dropping cloudy dates, would bias the sample toward clear, dry weather, which
correlates with the target.

**Final rule on the real data:** see "Result of the final rule" below.

## What the gate now does about it

A fetcher that avoids the problem does not help anyone whose data already contains it.
So `check_quality_missingness` gained a plausibility check:

- **Declare a range:** `describe(path, declared={"plausible_range": (50, 12000)})`
  (S2 L2A DN over land).
- **What is measured:** `implausible_fraction`, the pixels that are **not nodata** but
  lie outside the range.
- **Above `max_implausible_fraction` (default 1%): REFUSE.** A declared gap-fill policy
  does not excuse it, because these pixels are not flagged as gaps, so no fill would
  touch them.
- **0 < fraction ≤ tolerance:** a decision, "must be masked, not used".
- **A range declared but not measured:** reported as unknown, not clean.

Regression tests (synthetic GeoTIFF: B02 = 1200 with a 3-column strip of 1 DN,
nodata 0): `tests/test_io.py::test_swath_edge_fill_is_refused_by_quality_gate` and
five more. `tests/test_open_cube_fetch.py` covers tile scoring on a rebuilt 06-25
geometry.

**What the gate cannot do, and the cube must.** On the real 06-25 chip the fill strip
is **0.7%**, inside the 1% tolerance. So the gate records the decision ("must be
masked, not used") and does not refuse, which is correct for the layer as a whole. But
the masked pixel **is** the sample pixel. A layer-level gate cannot protect one
particular pixel. Per-pixel masking at cube assembly (the measured mask above, applied
to the station pixel) is what keeps that value out of training. The same applies to
the S1 chips that are 25–37% nodata around a valid station pixel. **This is recorded
for cube assembly (PLAN.md), not yet implemented.**

## Result of the final rule

`fetch_year.s2_task` with the final scoring, rerun on the five Sentinel-2 dates.
Scores are **[station pixel, 3 × 3 window, chip]**:

| Date | Station | 32U… | 31U… | Outcome |
|---|---|---|---|---|
| 2019-06-25 | ITCSM_08 | 32ULC [0, 0.111, 0.486] | 31UGT [0, 0, 0.486] | chip written; **`station_measured: false`** |
| 2019-10-03 | ITCSM_08 | 32ULC [0, 0.222, 0.494] | 31UGT [0, 0.222, 0.493] | chip written; **`station_measured: false`** |
| 2019-08-29 | ITCSM_08 | 32ULC [0, 0.333, 0.502] | 31UGT [0, 0.333, 0.501] | chip written; **`station_measured: false`** |
| 2019-02-07 | ITCSM_17 | 32ULD [1, 1, 0.915] | 31UGU [1, 1, **0.927**] | **31UGU**: wins on coverage, not zone |
| 2019-12-27 | ITCSM_04 | — | — | `no_measurement` (no tile contains the station) |

- **06-25:** neither grid's station pixel is trusted, because both lie within 2 pixels
  of the swath edge. The 1 DN fill **and** the 1260 beside it are flagged, not used.
  The chip is still written, so the cube records the date and why it is unusable at
  the station, rather than the date silently vanishing.
- **ITCSM_08 sits on one orbit's swath edge.** All three of its edge dates (06-25,
  08-29, 10-03, at 10:50 UTC) come from the same Sentinel-2 orbit, whose swath edge
  runs through the station. The other orbit (10:40 UTC) covers it normally.
  ITCSM_08 therefore systematically loses that orbit's dates. That is a property of
  the station's position, to be reported per station at cube assembly; it is not an
  error.
- **Cloud did not steer any choice:** 08-29 and 10-03 are bright cloud at the station
  in both grids, and the scores ignore it.
