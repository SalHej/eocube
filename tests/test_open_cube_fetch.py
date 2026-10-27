"""Tile scoring in experiments/open_cube/fetch_year.py, on synthetic chips.

Reproduces the 2019-06-25 ITCSM_08 case (docs/examples/swath_edges.md): one Sentinel-2
acquisition on two UTM grids, the swath edge running through the station. One grid
puts the station on a 1-pixel fill strip (B02 = 1), the other one pixel inside it.
Chip-level counts tied, and the fill was taken.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "open_cube"))
import fetch_year as fy  # noqa: E402

CRS = "EPSG:32632"
X0, Y0 = 350000.0, 5780000.0                  # chip origin; 20 m SCL pixels


def _chip(edge_col: int, fill_strip: bool, n: int = 50):
    """B02 (10 m, 2n x 2n) and SCL (20 m, n x n). Columns left of edge_col are beyond
    the swath (0); optionally the first column inside reads B02 = 1 with SCL = 4."""
    scl = np.full((1, n, n), 4, dtype="uint8")
    scl[0, :, :edge_col] = 0
    b02 = np.full((1, 2 * n, 2 * n), 1200, dtype="uint16")
    b02[0, :, : 2 * edge_col] = 0
    if fill_strip:
        b02[0, :, 2 * edge_col: 2 * edge_col + 2] = 1
    return b02, scl


def _station_lonlat(col: int, row: int = 25):
    """lon/lat of the centre of SCL pixel (row, col)."""
    from rasterio.warp import transform
    x, y = X0 + (col + 0.5) * 20, Y0 - (row + 0.5) * 20
    lon, lat = transform(CRS, "EPSG:4326", [x], [y])
    return lon[0], lat[0]


TR = from_origin(X0, Y0, 20, 20)


def test_station_on_fill_strip_scores_zero():
    b02, scl = _chip(edge_col=25, fill_strip=True)
    lon, lat = _station_lonlat(col=25)
    station, window, chip = fy.tile_score(b02, scl, TR, CRS, lon, lat)
    assert station == 0.0 and window == 0.0
    assert chip > 0.4                      # the chip as a whole looks half usable


def test_pixel_next_to_edge_is_not_trusted():
    b02, scl = _chip(edge_col=25, fill_strip=False)
    lon, lat = _station_lonlat(col=26)      # one pixel inside the swath, plausible value
    station, window, _ = fy.tile_score(b02, scl, TR, CRS, lon, lat)
    assert station == 0.0                   # within EDGE_BUFFER_PX of the nodata boundary
    assert 0 < window < 1                   # the window reaches past the buffer; the pixel decides


def test_station_well_inside_wins_over_tied_chip():
    near_b02, near_scl = _chip(edge_col=25, fill_strip=True)
    far_b02, far_scl = _chip(edge_col=15, fill_strip=True)
    lon, lat = _station_lonlat(col=25)
    near = fy.tile_score(near_b02, near_scl, TR, CRS, lon, lat)
    far = fy.tile_score(far_b02, far_scl, TR, CRS, lon, lat)
    assert far[0] == 1.0 and near[0] == 0.0
    assert max([near, far]) == far


def test_cloud_counts_as_measured():
    b02, scl = _chip(edge_col=0, fill_strip=False)
    scl[0, 20:30, 20:30] = 9                # cloud, high probability
    b02[0, 40:60, 40:60] = 6000
    lon, lat = _station_lonlat(col=25)
    station = fy.tile_score(b02, scl, TR, CRS, lon, lat)[0]
    assert station == 1.0                   # cloud is a cube-time question, not tile choice


def test_water_exempt_from_reflectance_floor():
    b02, scl = _chip(edge_col=0, fill_strip=False)
    scl[0, 20:30, 20:30] = fy.SCL_WATER
    b02[0, 40:60, 40:60] = 30               # dark water, below the land floor
    lon, lat = _station_lonlat(col=25)
    assert fy.tile_score(b02, scl, TR, CRS, lon, lat)[0] == 1.0


def test_dark_land_below_floor_is_not_measured():
    b02, scl = _chip(edge_col=0, fill_strip=False)
    b02[0, 40:60, 40:60] = 30               # same value, but SCL says vegetation
    lon, lat = _station_lonlat(col=25)
    assert fy.tile_score(b02, scl, TR, CRS, lon, lat)[0] == 0.0
