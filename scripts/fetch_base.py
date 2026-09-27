#!/usr/bin/env python3
"""Fetch the base city for the scope bbox and write dist/ assets.

Outputs
  dist/base.glb       merged building mesh (KHR_mesh_quantization, COLOR_0 per building)
  dist/terrain.png    16-bit greyscale heightmap, row 0 = north edge
  dist/terrain.json   heightmap extent/scale + local frame definition
  dist/landuse.json   simplified polygons by class (park, forest, water, road)
  dist/base_meta.json sources used, fallbacks, height-source coverage

Source chains (first that works wins; every step is logged):
  footprints : City of Edmonton open data (jpxi-a9a5 Rooflines 2019 -> 6n9r-ddf8 Footprints 2017)
               OR the OSM chain: Overpass -> Geofabrik Alberta PBF + osmium -> Overture Maps.
               One footprint set is used, never a blend of City and OSM geometry
               (--footprints auto|city|osm; auto = City when reachable).
  landuse    : Overpass -> Geofabrik PBF -> Overture Maps
  terrain    : City DEM points (nppw-6ykk) -> NRCan HRDEM 1 m LiDAR DTM
               -> NRCan MRDEM 30 m -> AWS Terrain Tiles (terrarium)
  LiDAR DSM  : NRCan HRDEM 1 m LiDAR DSM (DSM - DTM gives building heights)

Base-building height priority (docs/scope.md): City height field > LiDAR DSM-DEM
> OSM building:levels x 3.2 > default (8 m if footprint < 300 m2 else 12 m).

Downloads are cached in data/raw/ (gitignored); pass --refresh to refetch.
Pass --skip city,overpass,... to skip sources (also env SKYLINE_SKIP).
Pass --dist DIR to write somewhere other than dist/ (used by scripts/compare_footprints.py).
"""
import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import requests
import shapely
from shapely import wkb as shp_wkb
from shapely.geometry import LineString, MultiPolygon, Polygon, box, shape
from shapely.ops import polygonize, transform as shp_transform, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (BBOX_WSEN, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN,  # noqa: E402
                    ORIGIN_E, ORIGIN_N, RAW, CENTRE_LAT, CENTRE_LON, STATUS_HEX,
                    TO_UTM, UTM_EPSG, local_extent, write_json)

DIST = common.DIST  # rebound by --dist
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "edmonton-skyline/1 (github.com/mdiamond95/edmonton-skyline)"

LOG = {"sources": {}, "attempts": [], "fallbacks": []}


def log(msg):
    print(msg, flush=True)


def attempt(kind, source, ok, detail=""):
    LOG["attempts"].append({"kind": kind, "source": source, "ok": ok, "detail": str(detail)[:300]})
    log(f"  [{'OK' if ok else 'FAIL'}] {kind}: {source}{' - ' + str(detail)[:200] if detail else ''}")


def cached(name):
    return RAW / name


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _proj_local(x, y, z=None):
    e, n = TO_UTM.transform(x, y)
    return e - ORIGIN_E, n - ORIGIN_N


def wgs_to_local(geom):
    return shp_transform(_proj_local, geom)


def polygons_of(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out += polygons_of(g)
        return out
    return []


def clean_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    polys = [p for p in polygons_of(geom) if p.area > 0]
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


# ---------------------------------------------------------------------------
# OSM tag classification (shared by Overpass, Geofabrik and Overture source_tags)
# ---------------------------------------------------------------------------

GREEN_LANDUSE = {"grass", "recreation_ground", "cemetery", "meadow", "village_green", "allotments",
                 "orchard", "greenfield", "flowerbed", "plant_nursery", "conservation"}
GREEN_LEISURE = {"park", "garden", "golf_course", "pitch", "playground", "nature_reserve", "common",
                 "dog_park", "recreation_ground"}
GREEN_NATURAL = {"scrub", "grassland", "heath", "wetland", "meadow", "shrubbery"}
FOREST = {"forest", "wood"}
ROAD_WIDTH = {  # metres, full carriageway width used to buffer centrelines
    "motorway": 22, "trunk": 18, "primary": 16, "secondary": 13, "tertiary": 11,
    "motorway_link": 8, "trunk_link": 8, "primary_link": 8, "secondary_link": 8, "tertiary_link": 7,
    "residential": 8, "unclassified": 8, "living_street": 6, "service": 4.5, "pedestrian": 5,
    "busway": 7,
}
WATERWAY_WIDTH = {"river": 20, "canal": 8, "stream": 4, "drain": 2.5, "ditch": 2}


def classify_area(tags):
    t = tags
    if t.get("natural") == "water" or "water" in t or t.get("waterway") == "riverbank" \
            or t.get("landuse") in ("reservoir", "basin"):
        return "water"
    if t.get("landuse") == "forest" or t.get("natural") in FOREST:
        return "forest"
    if t.get("landuse") in GREEN_LANDUSE or t.get("leisure") in GREEN_LEISURE \
            or t.get("natural") in GREEN_NATURAL:
        return "park"
    return None


def classify_line(tags):
    """-> (class, width) for linear features, or None."""
    hw = tags.get("highway")
    if hw in ROAD_WIDTH and tags.get("tunnel") not in ("yes", "building_passage") \
            and tags.get("area") != "yes":
        return "road", ROAD_WIDTH[hw]
    ww = tags.get("waterway")
    if ww in WATERWAY_WIDTH and tags.get("tunnel") != "yes" and tags.get("tunnel") != "culvert":
        return "water", WATERWAY_WIDTH[ww]
    return None


def levels_from_tags(tags):
    for k in ("building:levels", "levels"):
        v = tags.get(k)
        if v is None:
            continue
        m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(v))
        if m:
            return float(m.group(1))
    return None


def height_from_tags(tags):
    v = tags.get("height")
    if v is None:
        return None
    s = str(v).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|ft|')?", s)
    if not m:
        return None
    h = float(m.group(1))
    return h * 0.3048 if m.group(2) in ("ft", "'") else h


# ---------------------------------------------------------------------------
# Record type
# ---------------------------------------------------------------------------

def rec(geom, source, osm_levels=None, osm_height=None, city_height=None, cls=None,
        is_part=False, parent=None, rid=None):
    return {"geom": geom, "source": source, "osm_levels": osm_levels, "osm_height": osm_height,
            "city_height": city_height, "class": cls, "is_part": is_part, "parent": parent, "id": rid}


# ---------------------------------------------------------------------------
# Source: City of Edmonton open data (Socrata)
# ---------------------------------------------------------------------------

CITY = "https://data.edmonton.ca"
# Footprint layers, newest first. jpxi-a9a5 carries building_height (roof - ground, 2019 LiDAR);
# 6n9r-ddf8 (May 2017 rooflines) has only the_geom and area.
CITY_FOOTPRINT_LAYERS = [("jpxi-a9a5", "City of Edmonton - Rooflines (as of 2019)"),
                         ("6n9r-ddf8", "Building Footprint (rooflines as of May 2017)")]
CITY_DEM_POINTS = "nppw-6ykk"   # "Digital Elevation Model Points 3TM (DEM)"
HEIGHT_RE = re.compile(r"(^|_)(height|hgt|bldg_?ht|z_?max|roof_?height)($|_)", re.I)
STOREY_RE = re.compile(r"storey|stories|floors|levels", re.I)
ELEV_RE = re.compile(r"(^|_)(elev|elevation|z|height|value)($|_)", re.I)


def socrata_columns(dsid):
    r = HTTP.get(f"{CITY}/api/views/{dsid}.json", timeout=30)
    r.raise_for_status()
    return [(c.get("fieldName"), c.get("dataTypeName", "")) for c in r.json().get("columns", [])]


def bbox_wkt():
    return (f"POLYGON(({LON_MIN} {LAT_MIN}, {LON_MAX} {LAT_MIN}, {LON_MAX} {LAT_MAX}, "
            f"{LON_MIN} {LAT_MAX}, {LON_MIN} {LAT_MIN}))")


def socrata_rows(dsid, where, fmt="geojson", page=50000, select=None):
    rows, offset = [], 0
    while True:
        params = {"$where": where, "$limit": page, "$offset": offset, "$order": ":id"}
        if select:
            params["$select"] = select
        r = HTTP.get(f"{CITY}/resource/{dsid}.{fmt}", params=params, timeout=300)
        r.raise_for_status()
        batch = r.json()["features"] if fmt == "geojson" else r.json()
        rows += batch
        log(f"    {dsid}: {len(rows)} rows")
        if len(batch) < page:
            return rows
        offset += page


def fetch_city_footprints(dsid, refresh):
    path = cached(f"city_footprints_{dsid}.json")
    if path.exists() and not refresh:
        data = json.loads(path.read_text())
    else:
        cols = socrata_columns(dsid)
        geom_col = next((n for n, t in cols if t.lower() in ("multipolygon", "polygon")), None)
        if not geom_col:
            raise RuntimeError(f"no polygon column in {dsid}: {cols}")
        feats = socrata_rows(dsid, f"intersects({geom_col}, '{bbox_wkt()}')")
        data = {"columns": cols, "features": feats}
        RAW.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    cols = data["columns"]
    num = {n for n, t in cols if t.lower() in ("number", "double", "money")}
    hfield = next((n for n, _ in cols if HEIGHT_RE.search(n or "") and n in num), None)
    sfield = next((n for n, _ in cols if STOREY_RE.search(n or "") and n in num), None)
    LOG["sources"]["city_footprint_fields"] = {"dataset": dsid, "columns": [n for n, _ in cols],
                                               "height_field": hfield, "storeys_field": sfield}
    out = []
    for f in data["features"]:
        if not f.get("geometry"):
            continue
        g = clean_polygon(wgs_to_local(shape(f["geometry"])))
        if g is None:
            continue
        p = f.get("properties") or {}

        def num_or_none(k):
            try:
                return float(p[k]) if k and p.get(k) not in (None, "") else None
            except (TypeError, ValueError):
                return None
        out.append(rec(g, f"city:{dsid}", city_height=num_or_none(hfield), osm_levels=num_or_none(sfield)))
    return out


def fetch_city_dem(refresh, grid):
    """Grid the City DEM mass points (3TM) onto the terrain grid."""
    from scipy.interpolate import LinearNDInterpolator
    from pyproj import Transformer
    path = cached("city_dem_points.json")
    if path.exists() and not refresh:
        data = json.loads(path.read_text())
    else:
        cols = socrata_columns(CITY_DEM_POINTS)
        pt_col = next((n for n, t in cols if t.lower() in ("point", "location")), None)
        z_col = next((n for n, t in cols if ELEV_RE.search(n or "") and t.lower() == "number"), None)
        x_col = next((n for n, t in cols if re.fullmatch(r"(x|easting|x_coord\w*)", n or "", re.I)), None)
        y_col = next((n for n, t in cols if re.fullmatch(r"(y|northing|y_coord\w*)", n or "", re.I)), None)
        if not z_col:
            raise RuntimeError(f"no elevation column in {CITY_DEM_POINTS}: {cols}")
        if pt_col:
            where = f"within_box({pt_col}, {LAT_MAX}, {LON_MIN}, {LAT_MIN}, {LON_MAX})"
            rows = socrata_rows(CITY_DEM_POINTS, where, fmt="json", select=f"{pt_col},{z_col}")
        elif x_col and y_col:  # 3TM coordinates (EPSG:3776, NAD83 / Alberta 3TM ref merid 114 W)
            tr = Transformer.from_crs(4326, 3776, always_xy=True)
            xs, ys = tr.transform([LON_MIN, LON_MAX, LON_MIN, LON_MAX], [LAT_MIN, LAT_MIN, LAT_MAX, LAT_MAX])
            where = (f"{x_col} between {min(xs)} and {max(xs)} and "
                     f"{y_col} between {min(ys)} and {max(ys)}")
            rows = socrata_rows(CITY_DEM_POINTS, where, fmt="json", select=f"{x_col},{y_col},{z_col}")
        else:
            raise RuntimeError(f"no location columns in {CITY_DEM_POINTS}: {cols}")
        data = {"columns": cols, "pt": pt_col, "x": x_col, "y": y_col, "z": z_col, "rows": rows}
        RAW.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    LOG["sources"]["city_dem_fields"] = {"columns": [n for n, _ in data["columns"]], "z": data["z"]}
    rows = data["rows"]
    if data["pt"]:
        lon = np.array([float(r[data["pt"]]["coordinates"][0]) for r in rows if r.get(data["pt"])])
        lat = np.array([float(r[data["pt"]]["coordinates"][1]) for r in rows if r.get(data["pt"])])
        z = np.array([float(r[data["z"]]) for r in rows if r.get(data["pt"])])
        e, n = TO_UTM.transform(lon, lat)
    else:
        tr = Transformer.from_crs(3776, UTM_EPSG, always_xy=True)
        e, n = tr.transform(np.array([float(r[data["x"]]) for r in rows]),
                            np.array([float(r[data["y"]]) for r in rows]))
        z = np.array([float(r[data["z"]]) for r in rows])
    ok = np.isfinite(z) & (z > 400) & (z < 1200)
    if ok.sum() < 1000:
        raise RuntimeError(f"only {ok.sum()} usable DEM points")
    # Mass points sparser than ~1 per 25 m2 would be coarser than the 1 m LiDAR DTM next in the chain.
    xmin, ymin, xmax, ymax = local_extent()
    density = ok.sum() / ((xmax - xmin) * (ymax - ymin))
    LOG["sources"]["city_dem_density_pts_per_m2"] = round(float(density), 4)
    if density < 1 / 25:
        raise RuntimeError(f"City DEM too sparse ({density:.4f} pts/m2); using LiDAR DTM instead")
    interp = LinearNDInterpolator(np.c_[e[ok] - ORIGIN_E, n[ok] - ORIGIN_N], z[ok])
    gx, gy = grid.coords()
    out = interp(gx, gy).astype(np.float32)
    return fill_nans(out)


# ---------------------------------------------------------------------------
# Source: Overpass API
# ---------------------------------------------------------------------------

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter"]
OSM_BBOX = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"
Q_BUILDINGS = f"""[out:json][timeout:600][maxsize:1073741824];
(way["building"]({OSM_BBOX}); relation["building"]["type"="multipolygon"]({OSM_BBOX});
 way["building:part"]({OSM_BBOX}); relation["building:part"]({OSM_BBOX}););
out body geom;"""
Q_LANDUSE = f"""[out:json][timeout:600][maxsize:1073741824];
(nwr["landuse"]({OSM_BBOX}); nwr["leisure"]({OSM_BBOX}); nwr["natural"]({OSM_BBOX});
 nwr["water"]({OSM_BBOX}); way["waterway"]({OSM_BBOX}); way["highway"]({OSM_BBOX}););
out body geom;"""


def overpass(query, cache_name, refresh):
    path = cached(cache_name)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    last = None
    for url in OVERPASS_URLS:
        try:
            r = HTTP.post(url, data={"data": query}, timeout=900)
            r.raise_for_status()
            data = r.json()
            RAW.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
            return data
        except Exception as e:  # try the next mirror
            last = e
            log(f"    overpass {url}: {e}")
    raise RuntimeError(f"all Overpass endpoints failed: {last}")


def _ring(geometry):
    return [(p["lon"], p["lat"]) for p in geometry if p]


def osm_json_features(elements):
    """Overpass `out geom` JSON -> [(tags, kind, shapely WGS84 geometry)], kind in area/line."""
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        if el["type"] == "way" and el.get("geometry"):
            pts = _ring(el["geometry"])
            if len(pts) < 2:
                continue
            closed = len(pts) >= 4 and pts[0] == pts[-1]
            is_line = ("highway" in tags and tags.get("area") != "yes") or \
                      (tags.get("waterway") in WATERWAY_WIDTH)
            if closed and not is_line:
                out.append((tags, "area", Polygon(pts)))
            elif not closed or is_line:
                out.append((tags, "line", LineString(pts)))
        elif el["type"] == "relation" and tags.get("type") in ("multipolygon", "building", None):
            outers, inners = [], []
            for m in el.get("members", []):
                if m.get("type") != "way" or not m.get("geometry"):
                    continue
                pts = _ring(m["geometry"])
                if len(pts) < 2:
                    continue
                (inners if m.get("role") == "inner" else outers).append(LineString(pts))
            if not outers:
                continue
            outer = unary_union(list(polygonize(unary_union(outers))))
            if inners:
                outer = outer.difference(unary_union(list(polygonize(unary_union(inners)))))
            if not outer.is_empty:
                out.append((tags, "area", outer))
    return out


def osm_buildings_from_features(feats, source):
    recs = []
    for tags, kind, g in feats:
        if kind != "area":
            continue
        is_part = "building:part" in tags and "building" not in tags
        if not is_part and ("building" not in tags or tags.get("building") == "no"):
            continue
        if tags.get("location") == "underground" or str(tags.get("layer", "0")).startswith("-"):
            continue
        g = clean_polygon(wgs_to_local(g))
        if g is None:
            continue
        recs.append(rec(g, source, osm_levels=levels_from_tags(tags), osm_height=height_from_tags(tags),
                        cls=tags.get("building") if not is_part else "part", is_part=is_part))
    return recs


def fetch_overpass_buildings(refresh):
    data = overpass(Q_BUILDINGS, "overpass_buildings.json", refresh)
    return osm_buildings_from_features(osm_json_features(data["elements"]), "overpass")


def fetch_overpass_landuse(refresh):
    data = overpass(Q_LANDUSE, "overpass_landuse.json", refresh)
    return landuse_from_osm_features(osm_json_features(data["elements"]))


def landuse_from_osm_features(feats):
    out = defaultdict(list)
    for tags, kind, g in feats:
        if kind == "area":
            c = classify_area(tags)
            if c:
                out[c].append(wgs_to_local(g))
        else:
            cw = classify_line(tags)
            if cw:
                out[cw[0]].append(wgs_to_local(g).buffer(cw[1] / 2, cap_style="flat", join_style="round",
                                                          quad_segs=2))
    return out


# ---------------------------------------------------------------------------
# Source: Geofabrik Alberta PBF + osmium
# ---------------------------------------------------------------------------

GEOFABRIK_URL = "https://download.geofabrik.de/north-america/canada/alberta-latest.osm.pbf"
OSM_KEYS = ("building", "building:part", "landuse", "leisure", "natural", "water", "waterway", "highway")


def geofabrik_pbf(refresh):
    """Download the Alberta extract, then cut it to the bbox with osmium-tool if installed."""
    full = cached("alberta-latest.osm.pbf")
    if not full.exists() or refresh:
        RAW.mkdir(parents=True, exist_ok=True)
        with HTTP.get(GEOFABRIK_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = full.with_suffix(".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(full)
    small = cached("edmonton-bbox.osm.pbf")
    if shutil.which("osmium") and (not small.exists() or refresh):
        subprocess.run(["osmium", "extract", "-b", f"{LON_MIN},{LAT_MIN},{LON_MAX},{LAT_MAX}",
                        "--strategy=smart", "-O", "-o", str(small), str(full)], check=True)
    return small if small.exists() else full


def osmium_features(path):
    """Read an OSM file (pbf or xml) -> [(tags, kind, WGS84 geometry)] inside the bbox."""
    import osmium
    wkb = osmium.geom.WKBFactory()
    bb = box(*BBOX_WSEN)
    fp = (osmium.FileProcessor(str(path))
          .with_locations()
          .with_areas(osmium.filter.KeyFilter(*OSM_KEYS))
          .with_filter(osmium.filter.KeyFilter(*OSM_KEYS)))
    out = []
    for obj in fp:
        tags = {t.k: t.v for t in obj.tags}
        try:
            if obj.is_area():
                g = shp_wkb.loads(wkb.create_multipolygon(obj), hex=True)
                kind = "area"
            elif isinstance(obj, osmium.osm.Way) and ("highway" in tags or "waterway" in tags):
                if obj.is_closed() and tags.get("area") == "yes":
                    continue
                g = shp_wkb.loads(wkb.create_linestring(obj), hex=True)
                kind = "line"
            else:
                continue
        except Exception:  # broken geometry / missing locations
            continue
        if g.intersects(bb):
            out.append((tags, kind, g))
    return out


_GEOFABRIK_CACHE = {}


def fetch_geofabrik_features(refresh):
    if "feats" not in _GEOFABRIK_CACHE:
        _GEOFABRIK_CACHE["feats"] = osmium_features(geofabrik_pbf(refresh))
    return _GEOFABRIK_CACHE["feats"]


# ---------------------------------------------------------------------------
# Source: Overture Maps (GeoParquet on S3; OSM-derived, reachable from locked-down sandboxes)
# ---------------------------------------------------------------------------

OVERTURE_BUCKET = "overturemaps-us-west-2"


def overture_release():
    r = HTTP.get(f"https://{OVERTURE_BUCKET}.s3.us-west-2.amazonaws.com/",
                 params={"list-type": "2", "prefix": "release/", "delimiter": "/"}, timeout=60)
    r.raise_for_status()
    rel = sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", r.text))
    if not rel:
        raise RuntimeError("no Overture releases listed")
    return rel[-1]


def overture_table(theme, type_, columns, refresh):
    import pyarrow.compute as pc
    import pyarrow.dataset as pds
    import pyarrow.fs as pfs
    import pyarrow.parquet as pq
    path = cached(f"overture_{type_}.parquet")
    if path.exists() and not refresh:
        return pq.read_table(path).to_pylist()
    release = overture_release()
    LOG["sources"]["overture_release"] = release
    kw = {"anonymous": True, "region": "us-west-2"}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        from urllib.parse import urlparse
        p = urlparse(proxy)
        kw["proxy_options"] = {"scheme": p.scheme or "http", "host": p.hostname, "port": p.port}
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        if os.environ.get(var) and not os.environ.get("AWS_CA_BUNDLE"):
            os.environ["AWS_CA_BUNDLE"] = os.environ[var]
    fs = pfs.S3FileSystem(**kw)
    d = pds.dataset(f"{OVERTURE_BUCKET}/release/{release}/theme={theme}/type={type_}/",
                    filesystem=fs, format="parquet")
    xmin, ymin, xmax, ymax = BBOX_WSEN
    flt = ((pc.field("bbox", "xmin") < xmax) & (pc.field("bbox", "xmax") > xmin) &
           (pc.field("bbox", "ymin") < ymax) & (pc.field("bbox", "ymax") > ymin))
    t0 = time.time()
    tb = d.to_table(filter=flt, columns=[c for c in columns if c in d.schema.names])
    log(f"    overture {theme}/{type_}: {tb.num_rows} rows in {time.time() - t0:.0f}s")
    RAW.mkdir(parents=True, exist_ok=True)
    pq.write_table(tb, path)
    return tb.to_pylist()


def _overture_osm_height(r):
    """Overture `height` only when its provenance is OpenStreetMap (not ML-estimated)."""
    if r.get("height") is None:
        return None
    for s in r.get("sources") or []:
        if s.get("property") in ("/properties/height",) and s.get("dataset") != "OpenStreetMap":
            return None
    return float(r["height"])


def fetch_overture_buildings(refresh):
    cols = ["id", "geometry", "height", "num_floors", "class", "has_parts", "is_underground", "sources"]
    recs = []
    ml_heights = 0
    for r in overture_table("buildings", "building", cols, refresh):
        if r.get("is_underground"):
            continue
        g = clean_polygon(wgs_to_local(shp_wkb.loads(r["geometry"])))
        if g is None:
            continue
        h = _overture_osm_height(r)
        ml_heights += r.get("height") is not None and h is None
        src = (r.get("sources") or [{}])[0].get("dataset", "?")
        recs.append(rec(g, f"overture:{src}", osm_levels=r.get("num_floors"), osm_height=h,
                        cls=r.get("class"), rid=r["id"]))
    LOG["sources"]["overture_ml_heights_ignored"] = ml_heights
    pcols = ["id", "geometry", "height", "num_floors", "building_id", "is_underground", "sources"]
    for r in overture_table("buildings", "building_part", pcols, refresh):
        if r.get("is_underground"):
            continue
        g = clean_polygon(wgs_to_local(shp_wkb.loads(r["geometry"])))
        if g is None:
            continue
        recs.append(rec(g, "overture:part", osm_levels=r.get("num_floors"), osm_height=_overture_osm_height(r),
                        cls="part", is_part=True, parent=r.get("building_id"), rid=r["id"]))
    return recs


def fetch_overture_landuse(refresh):
    out = defaultdict(list)
    tagcols = ["geometry", "subtype", "class", "source_tags"]
    for type_ in ("land_use", "land", "water"):
        for r in overture_table("base", type_, tagcols, refresh):
            g = shp_wkb.loads(r["geometry"])
            tags = dict(r.get("source_tags") or [])
            if type_ == "water" and not tags:
                tags = {"natural": "water"} if g.geom_type.endswith("Polygon") else {"waterway": r.get("class")}
            if g.geom_type.endswith("Polygon"):
                c = classify_area(tags) or ("water" if type_ == "water" else None)
                if c:
                    out[c].append(wgs_to_local(g))
            elif g.geom_type.endswith("LineString"):
                cw = classify_line(tags)
                if cw:
                    out[cw[0]].append(wgs_to_local(g).buffer(cw[1] / 2, cap_style="flat", quad_segs=2))
    seg_cols = ["geometry", "subtype", "class", "road_flags"]
    for r in overture_table("transportation", "segment", seg_cols, refresh):
        if r.get("subtype") != "road" or r.get("class") not in ROAD_WIDTH:
            continue
        flags = {v for rf in (r.get("road_flags") or []) for v in (rf.get("values") or [])}
        if "is_tunnel" in flags or "is_underground" in flags:
            continue
        g = wgs_to_local(shp_wkb.loads(r["geometry"]))
        out["road"].append(g.buffer(ROAD_WIDTH[r["class"]] / 2, cap_style="flat", quad_segs=2))
    return out


# ---------------------------------------------------------------------------
# Terrain grid + DEM sources
# ---------------------------------------------------------------------------

class Grid:
    """Regular grid over the local extent; row 0 is the north edge."""

    def __init__(self, res):
        self.xmin, self.ymin, self.xmax, self.ymax = local_extent()
        self.res = res
        self.w = int(round((self.xmax - self.xmin) / res)) + 1
        self.h = int(round((self.ymax - self.ymin) / res)) + 1
        self.xmax = self.xmin + (self.w - 1) * res
        self.ymin = self.ymax - (self.h - 1) * res

    def coords(self):
        xs = self.xmin + np.arange(self.w) * self.res
        ys = self.ymax - np.arange(self.h) * self.res
        return np.meshgrid(xs, ys)

    def utm_transform(self, pixel_is_area=False):
        from rasterio.transform import Affine
        half = self.res / 2 if not pixel_is_area else 0
        return Affine(self.res, 0, self.xmin + ORIGIN_E - half, 0, -self.res, self.ymax + ORIGIN_N + half)

    def sample(self, arr, x, y):
        """Bilinear sample at local coords."""
        fx = np.clip((np.asarray(x) - self.xmin) / self.res, 0, self.w - 1.001)
        fy = np.clip((self.ymax - np.asarray(y)) / self.res, 0, self.h - 1.001)
        x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        tx, ty = fx - x0, fy - y0
        a, b = arr[y0, x0], arr[y0, x0 + 1]
        c, d = arr[y0 + 1, x0], arr[y0 + 1, x0 + 1]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def fill_nans(a):
    bad = ~np.isfinite(a)
    if not bad.any():
        return a
    if bad.all():
        raise RuntimeError("raster is entirely nodata")
    from scipy import ndimage
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]


HRDEM = "https://canelevation-dem.s3.ca-central-1.amazonaws.com"
HRDEM_TILE = "3_4"   # hrdem-mosaic-1m tile whose extent covers the scope bbox (found via *-extent.geojson)
MRDEM_DTM = f"{HRDEM}/mrdem-30/mrdem-30-dtm.tif"


def warp_cog(url, grid, cache_name, refresh, resampling="bilinear"):
    """Warp a remote COG onto `grid` (UTM, local extent). Cached as a float32 GeoTIFF."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    path = cached(cache_name)
    if path.exists() and not refresh:
        with rasterio.open(path) as ds:
            a = ds.read(1)
            return np.where(a == -9999, np.nan, a).astype(np.float32)
    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "5",
           "GDAL_HTTP_RETRY_DELAY": "2", "GDAL_HTTP_MULTIPLEX": "YES", "GDAL_HTTP_VERSION": "2",
           "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": 512 << 20, "GDAL_CACHEMAX": 1024}
    t0 = time.time()
    with rasterio.Env(**env), rasterio.open(f"/vsicurl/{url}") as src:
        with WarpedVRT(src, crs=f"EPSG:{UTM_EPSG}", transform=grid.utm_transform(), width=grid.w,
                       height=grid.h, resampling=getattr(Resampling, resampling),
                       src_nodata=src.nodata, nodata=-9999, dtype="float32") as vrt:
            a = np.full((grid.h, grid.w), -9999, np.float32)
            step = 1024
            for r0 in range(0, grid.h, step):
                win = ((r0, min(grid.h, r0 + step)), (0, grid.w))
                a[r0:r0 + step] = vrt.read(1, window=win)
    log(f"    {url.rsplit('/', 1)[-1]}: {grid.w}x{grid.h} in {time.time() - t0:.0f}s")
    RAW.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=grid.w, height=grid.h, count=1, dtype="float32",
                       crs=f"EPSG:{UTM_EPSG}", transform=grid.utm_transform(), nodata=-9999,
                       compress="deflate", predictor=3, tiled=True) as dst:
        dst.write(a, 1)
    a = np.where(a == -9999, np.nan, a)
    return a


def fetch_hrdem(kind, grid, refresh):
    a = warp_cog(f"{HRDEM}/hrdem-mosaic-1m/{HRDEM_TILE}-mosaic-1m-{kind}.tif", grid,
                 f"hrdem_{kind}_{grid.res:g}m.tif", refresh)
    valid = np.isfinite(a).mean()
    if valid < 0.5:
        raise RuntimeError(f"HRDEM {kind} only {valid:.0%} valid over bbox")
    return a


def fetch_terrarium(grid, refresh, z=13):
    """AWS Terrain Tiles (Mapzen terrarium PNG, ~15 m at z13) resampled onto the grid."""
    from PIL import Image
    gx, gy = grid.coords()
    from common import to_wgs84
    lon, lat = to_wgs84(gx, gy)
    n = 2 ** z
    px = (lon + 180) / 360 * n * 256
    py = (1 - np.log(np.tan(np.radians(lat)) + 1 / np.cos(np.radians(lat))) / math.pi) / 2 * n * 256
    tx0, tx1 = int(px.min() // 256), int(px.max() // 256)
    ty0, ty1 = int(py.min() // 256), int(py.max() // 256)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            path = cached(f"terrarium/{z}_{tx}_{ty}.png")
            if not path.exists() or refresh:
                r = HTTP.get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png",
                             timeout=60)
                r.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
            rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
            elev = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = elev
    from scipy.ndimage import map_coordinates
    return map_coordinates(mosaic, [py - ty0 * 256 - 0.5, px - tx0 * 256 - 0.5], order=1,
                           mode="nearest").astype(np.float32)



# ---------------------------------------------------------------------------
# Height assignment
# ---------------------------------------------------------------------------

SRC_CODES = {"city": 0, "lidar": 1, "osm": 2, "default": 3}
SMALL_CLASSES = {"house", "detached", "garage", "garages", "shed", "carport", "semidetached_house",
                 "hut", "cabin", "bungalow", "terrace", "roof"}


def lidar_heights(items, ndsm, grid1):
    """Per-item LiDAR height (m above local ground) from the 1 m nDSM. Splits tall
    towers off their podiums so a tower on a large base is not one giant block."""
    import rasterio.features
    from rasterio.transform import Affine
    from scipy import ndimage
    out = []
    for it in items:
        g = it["geom"]
        xmin, ymin, xmax, ymax = g.bounds
        c0 = max(0, int(math.floor(xmin - grid1.xmin)))
        c1 = min(grid1.w, int(math.ceil(xmax - grid1.xmin)) + 1)
        r0 = max(0, int(math.floor(grid1.ymax - ymax)))
        r1 = min(grid1.h, int(math.ceil(grid1.ymax - ymin)) + 1)
        it["lidar"] = None
        if c1 - c0 < 1 or r1 - r0 < 1:
            out.append(it)
            continue
        tr = Affine(1, 0, grid1.xmin + c0 - 0.5, 0, -1, grid1.ymax - r0 + 0.5)
        m = rasterio.features.geometry_mask([g], (r1 - r0, c1 - c0), tr, invert=True, all_touched=False)
        if m.sum() < 3:
            m = rasterio.features.geometry_mask([g], (r1 - r0, c1 - c0), tr, invert=True, all_touched=True)
        nd = ndsm[r0:r1, c0:c1]
        v = nd[m]
        v = v[np.isfinite(v)]
        if v.size < max(3, 0.5 * m.sum()):
            out.append(it)
            continue
        area = g.area
        pct = 50 if area < 300 else 75
        h = float(np.percentile(v, pct))
        it["lidar"] = h
        # podium / tower split for large footprints
        if area > 800 and not it.get("split_done"):
            p95, p20 = np.percentile(v, [95, 20])
            if p95 > 20 and p95 - p20 > 12:
                thr = (p95 + p20) / 2
                hi = m & np.nan_to_num(nd > thr)
                hi = ndimage.binary_opening(hi, iterations=2)
                towers = []
                for geom, val in rasterio.features.shapes(hi.astype(np.uint8), mask=hi, transform=tr):
                    t = shape(geom).simplify(1.0).intersection(g)
                    t = clean_polygon(t)
                    if t is not None and t.area >= max(60, 0.03 * area):
                        towers.append(t)
                if towers:
                    lo = nd[m & ~hi]
                    lo = lo[np.isfinite(lo)]
                    it["lidar"] = float(np.percentile(lo, 60)) if lo.size > 10 else float(p20)
                    for t in towers:
                        tm = rasterio.features.geometry_mask([t], m.shape, tr, invert=True)
                        tv = nd[tm & np.isfinite(nd)]
                        if tv.size < 5:
                            continue
                        child = dict(it, geom=t, lidar=float(np.percentile(tv, 75)), split_done=True,
                                     is_part=True)
                        out.append(child)
        out.append(it)
    return out


def assign_height(it):
    area = it["geom"].area
    small_cls = (it.get("class") or "") in SMALL_CLASSES
    if it.get("city_height") and it["city_height"] > 1.5:
        return it["city_height"], "city"
    h = it.get("lidar")
    if h is not None and h >= 2.0:
        if area < 60:
            h = min(h, 7.0)
        elif small_cls or area < 150:
            h = min(h, 13.0)
        return h, "lidar"
    if it.get("osm_height") and it["osm_height"] > 1.5:
        return it["osm_height"], "osm"
    if it.get("osm_levels") and it["osm_levels"] > 0:
        return it["osm_levels"] * 3.2, "osm"
    return (8.0 if area < 300 else 12.0), "default"


# ---------------------------------------------------------------------------
# Footprint merging
# ---------------------------------------------------------------------------

def resolve_parts(items):
    """Replace buildings that have parts by their parts (+ residual outline)."""
    outlines = [i for i in items if not i["is_part"]]
    parts = [i for i in items if i["is_part"]]
    if not parts:
        return outlines
    by_id = {o["id"]: k for k, o in enumerate(outlines) if o.get("id")}
    tree = STRtree([o["geom"] for o in outlines])
    children = defaultdict(list)
    for p in parts:
        k = by_id.get(p.get("parent"))
        if k is None:
            c = p["geom"].representative_point()
            hits = [h for h in tree.query(c, predicate="within")]
            k = hits[0] if hits else None
        if k is not None:
            children[k].append(p)
    out = []
    for k, o in enumerate(outlines):
        if k in children:
            residual = clean_polygon(o["geom"].difference(unary_union([p["geom"] for p in children[k]])))
            if residual is not None and residual.area > max(20, 0.1 * o["geom"].area):
                out.append(dict(o, geom=residual))
            for p in children[k]:
                p["class"] = o["class"]
                p["city_height"] = p.get("city_height")
                out.append(p)
        else:
            out.append(o)
    orphans = sum(1 for p in parts if not any(p is c for ch in children.values() for c in ch))
    log(f"    parts: {len(parts)} ({orphans} orphans dropped), {len(children)} outlines replaced")
    return out


# ---------------------------------------------------------------------------
# Writers: glb, terrain png, landuse json
# ---------------------------------------------------------------------------

def srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hex_rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float64)


def extrude(poly, base, top):
    """Polygon (local EN) -> (verts Nx3 in three.js frame, tri indices Mx3). Walls + roof, no floor."""
    import mapbox_earcut as earcut
    poly = shapely.geometry.polygon.orient(poly, 1.0)  # exterior CCW, holes CW
    rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in poly.interiors]
    rings = [r[:, :2] for r in rings if len(r) >= 3]
    ring_xy = np.vstack(rings)
    n = len(ring_xy)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    roof = np.asarray(earcut.triangulate_float64(ring_xy, ends), np.int64).reshape(-1, 3)
    # three.js frame: x = east, y = up, z = -north
    bot = np.c_[ring_xy[:, 0], np.full(n, base), -ring_xy[:, 1]]
    tp = np.c_[ring_xy[:, 0], np.full(n, top), -ring_xy[:, 1]]
    verts = np.vstack([bot, tp])
    tris = []
    start = 0
    for r in rings:
        k = len(r)
        i = np.arange(k) + start
        j = np.roll(i, -1)
        # outward is to the right of each edge; with y up and z = -north these windings face out
        tris.append(np.c_[i, j, j + n])
        tris.append(np.c_[i, j + n, i + n])
        start += k
    roof = roof + n
    # make roof triangles face up (+y)
    a, b, c = verts[roof[:, 0]], verts[roof[:, 1]], verts[roof[:, 2]]
    ny = np.cross(b - a, c - a)[:, 1]
    roof[ny < 0] = roof[ny < 0][:, [0, 2, 1]]
    return verts, np.vstack(tris + [roof])


def write_glb(path, buildings, datum, meta):
    """buildings: list of dict(polys=[Polygon], base, top, src). Sorted spatially, chunked
    into <=65535-vertex primitives with uint16 indices."""
    base_rgb = srgb_to_linear(hex_rgb(STATUS_HEX["existing"]))
    col = np.r_[np.round(base_rgb * 255), 255].astype(np.uint8)
    chunks = []          # list of (verts list, idx list, nverts)
    table = []           # per building: prim, first, count, src
    cur_v, cur_i, nv = [], [], 0
    for b in buildings:
        pieces = []
        for p in b["polys"]:
            v, t = extrude(p, b["base"] - datum, b["top"] - datum)
            pieces.append((v, t))
        cnt = sum(len(v) for v, _ in pieces)
        if cnt > 65535:
            continue
        if nv + cnt > 65535:
            chunks.append((cur_v, cur_i, nv))
            cur_v, cur_i, nv = [], [], 0
        first = nv
        for v, t in pieces:
            cur_v.append(v)
            cur_i.append(t + nv)
            nv += len(v)
        table.append((len(chunks), first, cnt, SRC_CODES[b["src"]]))
    if nv:
        chunks.append((cur_v, cur_i, nv))

    allv = np.vstack([np.vstack(c[0]) for c in chunks])
    vmin, vmax = allv.min(0), allv.max(0)
    scale = (vmax - vmin) / 65535.0
    scale[scale == 0] = 1

    bin_parts, views, accessors, prims = [], [], [], []
    offset = 0

    def add_view(data, target=None, stride=None):
        nonlocal offset
        pad = (-offset) % 4
        if pad:
            bin_parts.append(b"\0" * pad)
            offset += pad
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        if stride:
            view["byteStride"] = stride
        bin_parts.append(data)
        offset += len(data)
        views.append(view)
        return len(views) - 1

    for verts, idxs, n in chunks:
        v = np.vstack(verts)
        q = np.round((v - vmin) / scale).clip(0, 65535).astype(np.uint16)
        q4 = np.zeros((n, 4), np.uint16)
        q4[:, :3] = q
        idx = np.vstack(idxs).astype(np.uint16).ravel()
        pv = add_view(q4.tobytes(), 34962, 8)
        accessors.append({"bufferView": pv, "componentType": 5123, "count": int(n), "type": "VEC3",
                          "min": q.min(0).tolist(), "max": q.max(0).tolist()})
        pa = len(accessors) - 1
        cv = add_view(np.tile(col, (n, 1)).tobytes(), 34962)
        accessors.append({"bufferView": cv, "componentType": 5121, "normalized": True,
                          "count": int(n), "type": "VEC4"})
        ca = len(accessors) - 1
        iv = add_view(idx.tobytes(), 34963)
        accessors.append({"bufferView": iv, "componentType": 5123, "count": int(idx.size), "type": "SCALAR",
                          "min": [int(idx.min())], "max": [int(idx.max())]})
        prims.append({"attributes": {"POSITION": pa, "COLOR_0": ca}, "indices": len(accessors) - 1,
                      "material": 0, "mode": 4})
    tview = add_view(np.asarray(table, np.uint32).tobytes())
    blob = b"".join(bin_parts)
    blob += b"\0" * ((-len(blob)) % 4)
    gltf = {
        "asset": {"version": "2.0", "generator": "edmonton-skyline scripts/fetch_base.py"},
        "extensionsUsed": ["KHR_mesh_quantization"],
        "extensionsRequired": ["KHR_mesh_quantization"],
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "base_buildings", "translation": vmin.tolist(),
                   "scale": scale.tolist()}],
        "meshes": [{"name": "base_buildings", "primitives": prims,
                    "extras": {"buildingTable": {"bufferView": tview, "count": len(table),
                                                 "layout": "uint32[primitive, firstVertex, vertexCount, heightSource]",
                                                 "heightSources": list(SRC_CODES)}}}],
        "materials": [{"name": "existing", "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 0.9}}],
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
        "extras": meta,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(blob), 0x004E4942) + blob)
    return len(table), int(allv.shape[0])


def write_png16(path, a):
    """Greyscale 16-bit PNG with the Sub filter (plain zlib; decoded in-browser by web/index.html)."""
    h, w = a.shape
    be = a.astype(">u2").view(np.uint8).reshape(h, w * 2)
    sub = be.copy()
    sub[:, 2:] = (be[:, 2:].astype(np.int16) - be[:, :-2]).astype(np.uint8)
    raw = np.c_[np.ones((h, 1), np.uint8), sub].tobytes()

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 0, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def flat_rings(poly, tol):
    poly = poly.simplify(tol, preserve_topology=True)
    out = []
    for p in polygons_of(poly):
        if p.area < 4 * tol * tol:
            continue
        rings = [p.exterior] + list(p.interiors)
        out.append([np.round(np.asarray(r.coords)[:-1, :2], 1).ravel().tolist() for r in rings])
    return out


def write_landuse(path, classes, extent_box, source):
    tol = {"park": 1.5, "forest": 1.5, "water": 1.0, "road": 0.8}
    out = {"frame": "local east/north metres from origin (see terrain.json)", "source": source, "classes": {}}
    counts = {}
    for cls, geoms in classes.items():
        if not geoms:
            continue
        u = unary_union([g if g.is_valid else shapely.make_valid(g) for g in geoms]).intersection(extent_box)
        polys = []
        for p in polygons_of(u):
            if p.area >= 20:
                polys += flat_rings(p, tol.get(cls, 1.0))
        out["classes"][cls] = polys
        counts[cls] = len(polys)
    write_json(path, out)
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_chain(kind, steps, skip):
    for name, fn in steps:
        if name in skip:
            attempt(kind, name, False, "skipped (--skip)")
            continue
        try:
            res = fn()
            if res is None or (hasattr(res, "__len__") and len(res) == 0):
                raise RuntimeError("no data returned")
            attempt(kind, name, True, f"{len(res)} items" if hasattr(res, "__len__") else "")
            return name, res
        except Exception as e:
            attempt(kind, name, False, f"{type(e).__name__}: {e}")
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore data/raw cache")
    ap.add_argument("--skip", default=os.environ.get("SKYLINE_SKIP", ""),
                    help="comma list: city,overpass,geofabrik,overture,city_dem,hrdem,mrdem,terrarium,lidar")
    ap.add_argument("--terrain-res", type=float, default=4.0, help="heightmap spacing in metres")
    ap.add_argument("--glb-budget-mb", type=float, default=22.0)
    ap.add_argument("--footprints", choices=("auto", "city", "osm"), default="auto",
                    help="auto: City layer when reachable, else the OSM chain (default)")
    ap.add_argument("--dist", default=None, help="output directory (default dist/)")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    refresh = args.refresh
    global DIST
    if args.dist:
        DIST = Path(args.dist).resolve()
    DIST.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    ext = local_extent()
    extent_box = box(*ext)
    log(f"Local extent (m): {ext}  origin UTM12N E{ORIGIN_E:.1f} N{ORIGIN_N:.1f}")

    # ---- footprints -------------------------------------------------------
    # One source only: City footprints or the OSM chain, never a blend of the two geometries.
    log(f"Footprints (mode {args.footprints}):")
    fp_name = fp = None
    if args.footprints in ("auto", "city"):
        fp_name, fp = run_chain("footprints (City)", [
            (f"city:{dsid}", lambda dsid=dsid: fetch_city_footprints(dsid, refresh))
            for dsid, _ in CITY_FOOTPRINT_LAYERS if "city" not in skip], skip)
        if fp is None and args.footprints == "city":
            sys.exit("ERROR: --footprints city but no City footprint layer reachable")
        if fp is None:
            LOG["fallbacks"].append("City footprints unavailable; used the OSM chain")
    if fp is None:
        fp_name, fp = run_chain("footprints (OSM)", [
            ("overpass", lambda: fetch_overpass_buildings(refresh)),
            ("geofabrik", lambda: osm_buildings_from_features(fetch_geofabrik_features(refresh), "geofabrik")),
            ("overture", lambda: fetch_overture_buildings(refresh)),
        ], skip)
        if fp is None:
            sys.exit("ERROR: no footprint source reachable")
        if fp_name != "overpass":
            LOG["fallbacks"].append(f"Overpass unavailable; OSM footprints/tags came from {fp_name}")
    items = resolve_parts(fp)
    items = [i for i in items if i["geom"].area >= 10 and extent_box.contains(i["geom"].representative_point())]
    LOG["sources"]["footprints"] = {"primary": fp_name, "mode": args.footprints}
    log(f"  {len(items)} footprints from {fp_name}")

    # ---- terrain ----------------------------------------------------------
    log("Terrain / LiDAR:")
    grid1 = Grid(1.0)
    grid_t = Grid(args.terrain_res)
    k = int(round(args.terrain_res))
    dtm1 = dsm1 = None

    def hrdem_terrain():
        nonlocal dtm1
        dtm1 = fetch_hrdem("dtm", grid1, refresh)
        from scipy.ndimage import uniform_filter
        sm = uniform_filter(fill_nans(dtm1), size=k, mode="nearest")
        sm = np.pad(sm, ((0, k), (0, k)), mode="edge")
        return sm[::k, ::k][:grid_t.h, :grid_t.w].astype(np.float32)

    tname, terrain = run_chain("terrain", [
        ("city_dem", lambda: fetch_city_dem(refresh, grid_t)),
        ("hrdem", hrdem_terrain),
        ("mrdem", lambda: fill_nans(warp_cog(MRDEM_DTM, grid_t, "mrdem_dtm.tif", refresh))),
        ("terrarium", lambda: fetch_terrarium(grid_t, refresh)),
    ], skip)
    if terrain is None:
        sys.exit("ERROR: no DEM source reachable")
    if tname != "city_dem":
        LOG["fallbacks"].append(f"City DEM ({CITY_DEM_POINTS}) unavailable; terrain from {tname}")
    LOG["sources"]["terrain"] = tname

    if "lidar" not in skip and "hrdem" not in skip:
        try:
            if dtm1 is None:
                dtm1 = fetch_hrdem("dtm", grid1, refresh)
            dsm1 = fetch_hrdem("dsm", grid1, refresh)
            attempt("lidar dsm", "hrdem", True)
        except Exception as e:
            attempt("lidar dsm", "hrdem", False, e)
    LOG["sources"]["lidar"] = "hrdem-mosaic-1m " + HRDEM_TILE if dsm1 is not None else None

    # ---- heights ----------------------------------------------------------
    log("Heights:")
    t0 = time.time()
    if dsm1 is not None:
        ndsm = (dsm1 - dtm1).astype(np.float32)
        del dsm1
        items = lidar_heights(items, ndsm, grid1)
        del ndsm
    log(f"  LiDAR sampling {time.time() - t0:.0f}s")
    datum = float(math.floor(np.nanmin(terrain)))
    dtm1f = fill_nans(dtm1) if dtm1 is not None else None
    blds = []
    cov = Counter()
    cov_area = Counter()
    for it in items:
        h, src = assign_height(it)
        g = it["geom"].simplify(0.4, preserve_topology=True)
        polys = [p for p in polygons_of(g) if p.area >= 4]
        if not polys:
            continue
        xy = np.vstack([np.asarray(p.exterior.coords)[:, :2] for p in polys])
        ground = grid_t.sample(terrain, xy[:, 0], xy[:, 1])
        if dtm1f is not None:
            g1 = grid1.sample(dtm1f, xy[:, 0], xy[:, 1])
            ground = np.r_[ground, g1]
        base = float(np.min(ground)) - 1.0
        ref = float(np.median(ground))
        blds.append({"polys": polys, "base": base, "top": ref + max(h, 2.5), "src": src,
                     "cx": float(xy[:, 0].mean()), "cy": float(xy[:, 1].mean()), "h": h,
                     "area": sum(p.area for p in polys)})
        cov[src] += 1
        cov_area[src] += it["geom"].area
    n = sum(cov.values())
    tot_a = sum(cov_area.values())
    coverage = {s: {"count": cov[s], "pct": round(100 * cov[s] / n, 1),
                    "area_pct": round(100 * cov_area[s] / tot_a, 1)} for s in SRC_CODES}
    log("  Height-source coverage (by building count / by footprint area):")
    for s in SRC_CODES:
        log(f"    {s:<8} {coverage[s]['pct']:5.1f}%  / {coverage[s]['area_pct']:5.1f}%  ({cov[s]})")

    # ---- glb -------------------------------------------------------------
    # spatial sort (500 m cells, serpentine) so each primitive is compact for frustum culling
    cell = 500.0
    blds.sort(key=lambda b: (int((b["cy"] - ext[1]) // cell),
                             (1 if int((b["cy"] - ext[1]) // cell) % 2 else -1) * b["cx"]))
    meta = {"datum": datum, "origin": {"lon": CENTRE_LON, "lat": CENTRE_LAT, "utm_e": ORIGIN_E,
                                       "utm_n": ORIGIN_N, "epsg": UTM_EPSG},
            "frame": "x=east, y=elevation-datum, z=-north (metres)", "date": date.today().isoformat()}
    glb = DIST / "base.glb"
    budget = args.glb_budget_mb * 1048576
    tol_steps = [(0.0, 0), (0.8, 0), (1.2, 15), (1.8, 25), (2.5, 40)]
    for tol, min_area in tol_steps:
        cur = blds
        if tol:
            cur = []
            for b in blds:
                if b["area"] < min_area:
                    continue
                ps = [q for p in b["polys"] for q in polygons_of(p.simplify(tol, preserve_topology=True))
                      if q.area >= 4]
                if ps:
                    cur.append(dict(b, polys=ps))
        nb, nv = write_glb(glb, cur, datum, meta)
        size = glb.stat().st_size
        log(f"  base.glb: {nb} buildings, {nv} vertices, {size / 1048576:.1f} MB "
            f"(simplify {tol} m, min area {min_area} m2)")
        if size < budget:
            break
    else:
        sys.exit("ERROR: could not get base.glb under budget")
    LOG["glb"] = {"buildings": nb, "vertices": nv, "simplify_m": tol, "min_area_m2": min_area}

    # ---- terrain outputs ---------------------------------------------------
    hmin, hmax = float(np.nanmin(terrain)), float(np.nanmax(terrain))
    # 2 cm steps: finer than the DEM's vertical accuracy, and far smaller PNG than full 16-bit noise
    scale = max((hmax - hmin) / 65535.0, 0.02)
    q = np.round((terrain - hmin) / scale).clip(0, 65535).astype(np.uint16)
    write_png16(DIST / "terrain.png", q)
    write_json(DIST / "terrain.json", {
        "heightmap": "terrain.png", "width": grid_t.w, "height": grid_t.h,
        "extent": {"xmin": grid_t.xmin, "xmax": grid_t.xmax, "ymin": grid_t.ymin, "ymax": grid_t.ymax},
        "registration": "grid: sample (0,0) at (xmin, ymax); row 0 = north edge; col 0 = west edge",
        "elevation": {"offset": hmin, "scale": scale, "min": hmin, "max": hmax, "datum": datum,
                      "formula": "elevation_m = offset + value * scale"},
        "frame": "local east/north metres; three.js x=east, y=elevation-datum, z=-north",
        "origin": meta["origin"], "bbox_wgs84": {"lon_min": LON_MIN, "lon_max": LON_MAX,
                                                 "lat_min": LAT_MIN, "lat_max": LAT_MAX},
        "source": tname, "resolution_m": grid_t.res,
    }, indent=1)

    # ---- landuse ------------------------------------------------------------
    log("Landuse / water / roads:")
    lname, lu = run_chain("landuse", [
        ("overpass", lambda: fetch_overpass_landuse(refresh)),
        ("geofabrik", lambda: landuse_from_osm_features(fetch_geofabrik_features(refresh))),
        ("overture", lambda: fetch_overture_landuse(refresh)),
    ], skip)
    if lu is None:
        sys.exit("ERROR: no landuse source reachable")
    if lname != "overpass":
        LOG["fallbacks"].append(f"Overpass unavailable; landuse/water/roads from {lname}")
    counts = write_landuse(DIST / "landuse.json", lu, extent_box, lname)
    log(f"  landuse.json polygons: {counts}")
    LOG["sources"]["landuse"] = lname

    LOG["coverage"] = coverage
    LOG["date"] = date.today().isoformat()
    write_json(DIST / "base_meta.json", LOG, indent=1)
    log("Sizes:")
    for p in sorted(DIST.glob("*")):
        if p.is_file() and p.name != ".gitkeep":
            log(f"  {p.name:<16} {p.stat().st_size / 1048576:7.2f} MB")
    if LOG["fallbacks"]:
        log("Fallbacks used:")
        for f in LOG["fallbacks"]:
            log(f"  - {f}")


if __name__ == "__main__":
    main()
