"""Shared constants and helpers for the build scripts (see docs/scope.md).

Local scene frame: UTM zone 12N (EPSG:26912) metres, origin at the bbox centre.
Scripts work in (east, north) metres from that origin; the web viewer maps
east -> +x, elevation -> +y, north -> -z.
"""
import json
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"          # download cache, gitignored
DIST = ROOT / "dist"

# docs/scope.md bounding box (WGS84)
LAT_MIN, LAT_MAX = 53.505, 53.585
LON_MIN, LON_MAX = -113.560, -113.430
BBOX_WSEN = (LON_MIN, LAT_MIN, LON_MAX, LAT_MAX)
CENTRE_LON = (LON_MIN + LON_MAX) / 2
CENTRE_LAT = (LAT_MIN + LAT_MAX) / 2

UTM_EPSG = 26912
TO_UTM = Transformer.from_crs(4326, UTM_EPSG, always_xy=True)
FROM_UTM = Transformer.from_crs(UTM_EPSG, 4326, always_xy=True)
ORIGIN_E, ORIGIN_N = TO_UTM.transform(CENTRE_LON, CENTRE_LAT)

# Status legend (exact hex from docs/scope.md)
STATUSES = ("existing", "construction", "approved", "proposed", "stalled")
STATUS_HEX = {
    "existing": "#E4E3DF",
    "construction": "#4E8FD1",
    "approved": "#A995C9",
    "proposed": "#E59CA5",
    "stalled": "#E4E3DF",  # drawn at 25% opacity
}


def to_local(lon, lat):
    """WGS84 -> local (east, north) metres. Accepts scalars or numpy arrays."""
    e, n = TO_UTM.transform(lon, lat)
    return e - ORIGIN_E, n - ORIGIN_N


def to_wgs84(east, north):
    return FROM_UTM.transform(east + ORIGIN_E, north + ORIGIN_N)


def local_extent():
    """Axis-aligned local rectangle (xmin, ymin, xmax, ymax) enclosing the WGS84 bbox."""
    import numpy as np
    lons = np.array([LON_MIN, LON_MAX, LON_MAX, LON_MIN, CENTRE_LON, CENTRE_LON])
    lats = np.array([LAT_MIN, LAT_MIN, LAT_MAX, LAT_MAX, LAT_MIN, LAT_MAX])
    e, n = to_local(lons, lats)
    return (float(np.floor(e.min())), float(np.floor(n.min())),
            float(np.ceil(e.max())), float(np.ceil(n.max())))


def write_json(path, obj, indent=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, separators=None if indent else (",", ":"))
        f.write("\n")
