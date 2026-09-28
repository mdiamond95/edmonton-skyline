#!/usr/bin/env python3
"""Generate footprints for data/proposals.csv -> data/proposals.geojson (Phase 3B).

  make proposals     # promote_candidates.py, this script, then make build

Lots. The City stopped publishing parcel polygons in November 2021 (they moved to AltaLIS), so a lot
is rebuilt from what data.edmonton.ca still has:
  - Assessment parcel centroids with their areas (`dm3i-bp8w`, current), and
  - the latest Zoning Bylaw Map polygons (`67p2-r285`), which follow lot lines and leave out roads
    and lanes.
The lot for a development permit is the Voronoi cell of its nearest assessment-parcel centroid,
clipped to the zoning polygon that holds it; if the cell is more than 30% larger than the parcel's
recorded area (a corner or edge cell), it is scaled down about its centroid to that area. A rezoning
row takes its own rezoned zoning polygon (adjacent polygons of one Direct Control bylaw are merged),
and a permit inside a site-specific Direct Control polygon (<= 15,000 m2) takes the whole DC polygon
(unless another row sits in the same polygon: then each takes its own parcel cell).
Fallbacks: the convex hull of base buildings within 15 m, then a 30 x 30 m square.

Footprints (the brief):
  existing rows      the building's own outline(s) in dist/base.glb inside the lot, so the official
                     height replaces the measured one (build_proposals.py hides what is under it)
  storeys <= 11      the lot inset 3 m, simplified to at most 8 vertices
  storeys >= 12      N tower floorplate rectangles on the lot's long axis: 750 m2 residential,
                     1,500 m2 office / hotel / mixed-use, capped at 70% of the lot; N from the permit
                     text or SkyriseCities (promote_candidates.py). No podiums in this pass.
                     Phase 3C: a single tower from a development permit is centred on the permit's
                     own coordinate (or as near as it fits), still clipped to the lot; rezonings have
                     no such coordinate and stay centred on the lot. Multi-tower rows carry
                     `part_heights` (one height per tower) when the DP or a hand correction gives them.
footprint_source = needs_trace (the auto footprint is still written, so the row renders) when the lot
was not found, a single building not anchored on a permit coordinate sits on more than 8,000 m2, the
footprint overlaps a genuine park or water by more than 10%, or the lot is too narrow for the footprint
(under about 8 m wide, as an equivalent rectangle; narrow lots try a 1.5 m inset first).
Park check (Phase 3C): dist/landuse.json's green class includes vacant land (landuse=grass, greenfield,
meadow; natural=scrub). For a flagged footprint the OSM tags of the Overture land-use polygons under it
are read (cached in data/raw/footprints/landuse_tags/): vacant land clears the flag; a genuine park with
the permit coordinate outside it trims the lot to the part off the park; otherwise the flag stays.
Features whose footprint_source is not auto-generated (P001, P104, anything traced by hand in the
viewer) are kept as they are. footprint_source is copied back into proposals.csv.
"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from shapely import affinity
from shapely.ops import voronoi_diagram
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.wkt import loads as wkt_loads
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, DIST, RAW, ROOT, to_local, to_wgs84  # noqa: E402
from build_proposals import read_base_buildings  # noqa: E402
from promote_candidates import COLUMNS, parcel_rows, zoning_snapshot  # noqa: E402

CACHE = RAW / "footprints"
REPORT = ROOT / "docs" / "footprints-report.md"
AUTO = ("auto:", "needs_trace")
INSET_M = 3.0
MAX_VERTS = 8
FLOORPLATE = {"residential": 750.0, "office": 1500.0, "hotel": 1500.0, "mixed-use": 1500.0}
MAX_COVER = 0.70
BIG_LOT_M2 = 8000.0
DC_SITE_MAX_M2 = 15000.0
PARK_WATER_MAX = 0.10
PARCEL_SNAP_M = 40.0
MIN_WIDTH_M = 8.0


def local(geom):
    return transform(lambda x, y, z=None: to_local(x, y), geom)


def wgs(geom):
    return transform(lambda x, y, z=None: to_wgs84(x, y), geom)


def load_parcels():
    rows = parcel_rows()
    lon = np.array([float(r["longitude"]) for r in rows])
    lat = np.array([float(r["latitude"]) for r in rows])
    e, n = to_local(lon, lat)
    area = np.array([float(r.get("area") or 0) for r in rows])
    # condo units and stacked titles share a centroid: keep one point per location (largest area)
    key = np.round(e, 1) * 1e7 + np.round(n, 1)
    order = np.lexsort((-area, key))
    _, first = np.unique(key[order], return_index=True)
    keep = order[first]
    return np.c_[e[keep], n[keep]], area[keep]


def simplify_to(poly, n=MAX_VERTS):
    """Simplify until the exterior has at most n vertices; fall back to the minimum rotated rectangle."""
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    p = Polygon(poly.exterior)
    tol = 0.25
    while len(p.exterior.coords) - 1 > n and tol < 20:
        q = p.simplify(tol, preserve_topology=True)
        if q.is_valid and not q.is_empty and q.geom_type == "Polygon":
            p = q
        tol *= 1.6
    if len(p.exterior.coords) - 1 > n or not p.is_valid:
        p = poly.minimum_rotated_rectangle
    return p


def axes(poly):
    """(centre, unit long-axis vector, long length, short length) of the lot's minimum rotated rectangle."""
    r = poly.minimum_rotated_rectangle
    c = np.array(r.exterior.coords[:4])
    e1, e2 = c[1] - c[0], c[2] - c[1]
    l1, l2 = np.hypot(*e1), np.hypot(*e2)
    u = e1 / l1 if l1 >= l2 else e2 / l2
    ctr = np.array(poly.centroid.coords[0]) if poly.contains(poly.centroid) else np.array(poly.representative_point().coords[0])
    return ctr, u, max(l1, l2), min(l1, l2)


def towers(lot, n, area_each, anchor=None):
    """n floorplate rectangles on the lot's long axis. With an anchor (a single tower on the permit's own
    coordinate), the tower is centred there, or as close to it as it fits (slid towards the lot centre
    until 97% of it is on the lot), and clipped to the lot. Returns (rects, area each, anchored)."""
    ctr, u, L, S = axes(lot)
    if anchor is not None and n == 1:
        a0 = np.asarray(anchor, float)
        for t in np.linspace(0.0, 1.0, 21):
            c = a0 + t * (ctr - a0)
            if not lot.contains(Point(c)):
                continue
            rects, a = _towers_at(lot, c, u, L, S, 1, area_each, clip=False)
            clip = rects[0].intersection(lot)
            if clip.area >= 0.97 * rects[0].area:
                return [rects[0] if clip.area > rects[0].area - 0.5 else simplify_to(clip)], a, True
    return (*_towers_at(lot, ctr, u, L, S, n, area_each), False)


def _towers_at(lot, ctr, u, L, S, n, area_each, clip=True):
    v = np.array([-u[1], u[0]])
    a = min(area_each, MAX_COVER * lot.area / n)
    w = math.sqrt(a * 1.3)             # along the long axis, a slightly long rectangle
    d = a / w
    if d > 0.85 * S:
        d = 0.85 * S
        w = a / d
    if n * w > 0.9 * L:                # keep a gap between towers
        w = 0.9 * L / n
        d = min(a / w, 0.85 * S)
    rects = []
    for i in range(n):
        off = (i + 0.5) / n * L - L / 2 if n > 1 else 0.0
        c = ctr + u * off
        corners = [c + u * sx * w / 2 + v * sy * d / 2 for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        r = Polygon(corners)
        c2 = r.intersection(lot) if clip else r
        rects.append(r if c2.area >= 0.9 * r.area else simplify_to(c2))
    return rects, w * d


class Lots:
    def __init__(self):
        self.pts, self.parea = load_parcels()
        z = zoning_snapshot()
        self.zpolys = {}
        geoms, meta = [], []
        for p in z["polygons"]:
            g = local(shape(p["the_geom"]))
            if not g.is_valid:
                g = g.buffer(0)
            self.zpolys[p["polygon_id"]] = (g, p["zoning"])
            geoms.append(g)
            meta.append(p)
        self.ztree, self.zgeoms, self.zmeta = STRtree(geoms), geoms, meta
        self.ptree = STRtree([Point(xy) for xy in self.pts])

    def zone_at(self, pt):
        idx = [i for i in self.ztree.query(pt) if self.zgeoms[i].covers(pt)]
        if not idx:
            near = self.ztree.query(pt.buffer(15))
            idx = sorted(near, key=lambda i: self.zgeoms[i].distance(pt))[:1]
        return (self.zgeoms[idx[0]], self.zmeta[idx[0]]) if idx else (None, None)

    def rezoned(self, cids):
        parts = []
        for cid in cids.split(";"):
            pid = cid.rsplit("-", 1)[1]
            if pid in self.zpolys:
                parts.append(self.zpolys[pid][0])
        return unary_union(parts) if parts else None

    def cell(self, k, vor, zg, shrink=True):
        c = Point(self.pts[k])
        cell = next((g for g in vor.geoms if g.covers(c)), None)
        if cell is None:
            return None
        if zg is not None:
            cell = cell.intersection(zg.buffer(0.5))
        if cell.geom_type != "Polygon":
            cell = max(getattr(cell, "geoms", [cell]), key=lambda g: g.area) if not cell.is_empty else None
        if cell is None or cell.area < 50:
            return None
        return shrink_to(cell, float(self.parea[k])) if shrink else cell

    def nearest(self, e, n):
        near = self.ptree.query(Point(e, n).buffer(PARCEL_SNAP_M))
        return min(near, key=lambda i: math.hypot(*(self.pts[i] - (e, n)))) if len(near) else None

    def lot_at(self, e, n, need_m2=0.0, reserved=()):
        """Voronoi lot around the assessment parcel nearest (e, n): (polygon, parcel area, note) or None.
        While the lot is smaller than need_m2, adjacent parcels in the same zoning polygon are merged in,
        nearest first (at most 8): a permit often spans lots that were never consolidated. Parcels in
        `reserved` (another row's own parcel) are never merged in."""
        k = self.nearest(e, n)
        if k is None:
            return None
        c = Point(self.pts[k])
        zg, zm = self.zone_at(c)
        ring = self.ptree.query(c.buffer(250))
        vor = voronoi_diagram(MultiPoint([Point(self.pts[i]) for i in ring]), envelope=c.buffer(600))
        pa = float(self.parea[k])
        lot = self.cell(k, vor, zg)
        if lot is None:
            return None
        note = f"Voronoi cell of assessment parcel {pa:.0f} m2"
        if lot.area < need_m2 and zg is not None:
            raw, total, merged = self.cell(k, vor, zg, shrink=False), pa, 1
            order = sorted((i for i in ring if i != k and i not in reserved and zg.covers(Point(self.pts[i]))),
                           key=lambda i: math.hypot(*(self.pts[i] - self.pts[k])))
            for i in order[:12]:
                if min(raw.area, 1.3 * total) >= need_m2 or merged >= 9:
                    break
                ci = self.cell(i, vor, zg, shrink=False)
                if ci is None or ci.distance(raw) > 1.0:
                    continue
                u = unary_union([raw, ci.buffer(0.3)]).buffer(-0.3)
                if u.geom_type == "Polygon":
                    raw, total, merged = u, total + float(self.parea[i]), merged + 1
            if merged > 1:
                lot = shrink_to(raw, total)
                note = f"{merged} adjacent assessment-parcel cells merged ({lot.area:.0f} m2)"
        return lot, pa, note


def shrink_to(cell, parcel_m2):
    """An edge cell reaching into the road is scaled down about its centroid to the recorded parcel area."""
    if parcel_m2 > 50 and cell.area > 1.3 * parcel_m2:
        f = math.sqrt(parcel_m2 / cell.area)
        return affinity.scale(cell, f, f, origin=cell.centroid)
    return cell


def widths(fp):
    """Width of each part as its equivalent rectangle (same area and perimeter); exact for a rectangle,
    where 2 A / P under-reads (9 x 40 m -> 7.3 m)."""
    out = []
    for g in getattr(fp, "geoms", [fp]):
        h = g.length / 2
        disc = h * h - 4 * g.area
        out.append((h - math.sqrt(disc)) / 2 if disc > 0 else math.sqrt(g.area))
    return out


# OSM tags that make "park" land use in dist/landuse.json (fetch_base.classify_area) but mean vacant land.
VACANT_LANDUSE = {"grass", "greenfield", "meadow", "brownfield", "flowerbed"}
VACANT_NATURAL = {"scrub", "grassland", "shrubbery"}


def overture_green(pid, fp):
    """Overture base/land_use, land and water polygons (OSM tags) around a footprint, cached per row in
    data/raw/footprints/landuse_tags/. None when Overture is unreachable."""
    path = CACHE / "landuse_tags" / f"{pid}.json"
    w, s_, e_, n_ = wgs(fp.buffer(20)).bounds
    if path.exists():
        d = json.loads(path.read_text())
        if d.get("bbox") == [round(v, 6) for v in (w, s_, e_, n_)]:
            return [(t, wkt_loads(g)) for t, g in d["polys"]]
    try:
        import pyarrow.compute as pc
        import pyarrow.dataset as pds
        from shapely import wkb
        import fetch_base as fb
        rel, fs = fb.overture_release(), fb.overture_fs()
        polys = []
        for type_ in ("land_use", "land", "water"):
            ds = pds.dataset(f"{fb.OVERTURE_BUCKET}/release/{rel}/theme=base/type={type_}/", filesystem=fs, format="parquet")
            flt = ((pc.field("bbox", "xmin") < e_) & (pc.field("bbox", "xmax") > w) &
                   (pc.field("bbox", "ymin") < n_) & (pc.field("bbox", "ymax") > s_))
            for row in ds.to_table(filter=flt, columns=["geometry", "source_tags"]).to_pylist():
                g = wkb.loads(row["geometry"])
                tags = dict(row.get("source_tags") or []) or ({"natural": "water"} if type_ == "water" else {})
                if g.geom_type.endswith("Polygon") and fb.classify_area(tags) in ("park", "water"):
                    polys.append((tags, g))
    except Exception as ex:   # noqa: BLE001 - network or S3 failure: keep the flag
        print(f"  ! {pid}: Overture land use not reachable ({ex.__class__.__name__}); park flag kept")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bbox": [round(v, 6) for v in (w, s_, e_, n_)],
                                "polys": [(t, g.wkt) for t, g in polys]}))
    return polys


def is_vacant(tags):
    return "leisure" not in tags and (tags.get("landuse") in VACANT_LANDUSE or tags.get("natural") in VACANT_NATURAL)


def genuine_parks(pid, fp):
    """Local polygons of the parks / water under a footprint, leaving out vacant land; None if unknown."""
    polys = overture_green(pid, fp)
    return None if polys is None else [local(g) for t, g in polys if not is_vacant(t)]


def vacant_tags(pid, fp):
    polys = overture_green(pid, fp) or []
    tags = sorted({f"{k}={v}" for t, g in polys if is_vacant(t) and local(g).intersects(fp)
                   for k, v in t.items() if k in ("landuse", "natural")})
    return ", ".join(tags) or "no park tag"


def base_hull(blds, boxes, e, n, r=15.0):
    pt = Point(e, n)
    near = np.nonzero((boxes[:, 0] < e + r) & (boxes[:, 2] > e - r) & (boxes[:, 1] < n + r) & (boxes[:, 3] > n - r))[0]
    hulls = [MultiPoint(blds[k][1]).convex_hull for k in near]
    hulls = [h for h in hulls if h.area > 20 and h.distance(pt) <= r]
    return unary_union(hulls).convex_hull if hulls else None


def existing_outline(blds, boxes, lot, e, n):
    """Base buildings of the completed project: those at least half inside the lot (or the one at the point)."""
    x0, y0, x1, y1 = lot.bounds
    near = np.nonzero((boxes[:, 0] < x1) & (boxes[:, 2] > x0) & (boxes[:, 1] < y1) & (boxes[:, 3] > y0))[0]
    parts = []
    for k in near:
        h = MultiPoint(blds[k][1]).convex_hull
        if h.area > 40 and (lot.intersection(h).area >= 0.5 * h.area or h.contains(Point(e, n))):
            parts.append(h)
    if not parts:
        return None
    u = unary_union(parts)
    return u if u.geom_type == "Polygon" else MultiPolygon([g for g in u.geoms if g.area > 40])


def main():
    with (DATA / "proposals.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    meta = json.loads((CACHE / "promote_meta.json").read_text())
    old = json.loads((DATA / "proposals.geojson").read_text(encoding="utf-8"))
    keep = {}
    for ft in old.get("features", []):
        src = str(ft["properties"].get("footprint_source", ""))
        if not src.startswith(AUTO):
            keep[ft["properties"]["id"]] = ft
    lots = Lots()
    _, blds = read_base_buildings(DIST / "base.glb")
    boxes = np.array([b[0] for b in blds])
    lu = json.loads((DIST / "landuse.json").read_text())
    green = []
    for cls in ("park", "water"):
        for poly in lu["classes"].get(cls, []):
            rr = [np.asarray(r).reshape(-1, 2) for r in poly]
            if len(rr[0]) >= 3:
                g = Polygon(rr[0], [r for r in rr[1:] if len(r) >= 3])
                green.append(g if g.is_valid else g.buffer(0))
    gtree = STRtree(green)

    # a Direct Control polygon is one row's whole site only if no other row sits in it
    pts = {r["id"]: Point(*to_local(float(r["lon"]), float(r["lat"]))) for r in rows}

    own = {q: lots.nearest(pt.x, pt.y) for q, pt in pts.items()}

    def alone_in(poly, pid):
        return not any(q != pid and poly.covers(pt) for q, pt in pts.items())

    feats, report, lot_feats = [], [], []
    for r in rows:
        pid = r["id"]
        if pid in keep or not r.get("candidate_id"):
            if pid in keep:
                feats.append(keep[pid])
                r["footprint_source"] = keep[pid]["properties"].get("footprint_source", "manual")
            continue
        m = meta.get(pid, {})
        e, n = to_local(float(r["lon"]), float(r["lat"]))
        storeys = int(r["storeys"]) if r["storeys"] else max(1, int(float(r["height_m"]) / 3.1))
        nb = int(m.get("buildings") or 1)
        flags = []
        # --- lot
        lot, how, parcel_area = None, None, None
        cid = r["candidate_id"]
        if cid.startswith("REZ-"):
            lot = lots.rezoned(cid)
            how = "rezoned zoning polygon" + (" (adjacent DC polygons merged)" if ";" in cid else "")
        else:
            zg, zm = lots.zone_at(Point(e, n))
            if zg is not None and zm["zoning"].split(" ")[0].startswith("DC") and zg.area <= DC_SITE_MAX_M2 \
                    and zg.covers(Point(e, n)) and alone_in(zg, pid):
                lot, how = zg, f"Direct Control polygon ({zm['zoning']})"
            else:
                du = m.get("dwellings") or 0
                # land the permit needs: ~90 m2 gross per dwelling over its storeys, at 60% site coverage
                need = du * 90.0 / max(storeys, 1) / 0.6 if du else 0.0
                got = lots.lot_at(e, n, need, {v for q, v in own.items() if q != pid})
                if got:
                    lot, parcel_area, how = got
        if lot is None or lot.is_empty:
            hull = base_hull(blds, boxes, e, n)
            if hull is not None and hull.area >= 100:
                lot, how = hull, "convex hull of base buildings within 15 m"
            else:
                lot, how = box(e - 15, n - 15, e + 15, n + 15), "30 x 30 m square"
            flags.append("parcel not found")
        if lot.geom_type == "MultiPolygon":
            lot = max(lot.geoms, key=lambda g: g.area)
        opened = lot.buffer(-4, join_style="mitre").buffer(4, join_style="mitre")   # drop slivers < 8 m wide
        if opened.geom_type == "MultiPolygon":
            opened = max(opened.geoms, key=lambda g: g.area)
        if not opened.is_empty and opened.area >= 0.6 * lot.area:
            lot = opened
        # --- footprint (a permit's own coordinate anchors a single tower; see towers())
        anchor = (e, n) if m.get("from_permit", not cid.startswith("REZ-")) else None

        def footprint(lot):
            if r["status"] == "existing":
                fp = existing_outline(blds, boxes, lot, e, n)
                method = "base-building outline(s) in the lot (official height replaces LiDAR)"
                if fp is None:
                    fp, method = simplify_to(lot.buffer(-INSET_M, join_style="mitre")), f"lot inset {INSET_M:g} m"
                return fp, method, False
            if storeys <= 11:
                method = f"lot inset {INSET_M:g} m"
                fp = simplify_to(lot.buffer(-INSET_M, join_style="mitre"))
                if fp.is_empty or fp.area < 0.25 * lot.area or min(widths(fp)) < MIN_WIDTH_M:
                    # a narrow lot: typical side yards are ~1.5 m, not 3
                    for inset in (1.5, 1.0):
                        q = lot.buffer(-inset, join_style="mitre")
                        if not q.is_empty and q.area >= 0.25 * lot.area:
                            fp, method = simplify_to(q), f"lot inset {inset:g} m (narrow lot)"
                            if min(widths(fp)) >= MIN_WIDTH_M:
                                break
                return fp, method, False
            use = m.get("use", "residential")
            plate = FLOORPLATE.get(use, 1500.0)
            if m.get("floorplate_max"):   # the Direct Control text's own maximum tower floor plate
                plate = min(plate, float(m["floorplate_max"]))
            rects, a, anchored = towers(lot, nb, plate, anchor)
            fp = rects[0] if len(rects) == 1 else MultiPolygon(rects)
            method = (f"{nb} x {a:.0f} m2 {use} floorplate on the lot's long axis" if nb > 1
                      else f"{a:.0f} m2 {use} floorplate " + ("on the permit coordinate" if anchored else "centred on the lot"))
            if m.get("floorplate_max") and plate < FLOORPLATE.get(use, 1500.0):
                method += f" (DC maximum floor plate {plate:.0f} m2)"
            if a < plate - 1:
                method += f" (capped at {MAX_COVER:.0%} of the lot)"
            return fp, method, anchored

        fp, method, anchored = footprint(lot)
        if not fp.is_valid:
            fp = fp.buffer(0)
        # --- park / water: only a genuine park counts; vacant land tagged grass / greenfield does not
        gi = gtree.query(fp)
        wet = sum(fp.intersection(green[i]).area for i in gi) if len(gi) else 0.0
        if wet > PARK_WATER_MAX * fp.area:
            parks = genuine_parks(pid, fp)
            if parks is None:
                flags.append(f"{wet / fp.area:.0%} of the footprint on park / water land use (OSM tags not reachable)")
            else:
                real = unary_union(parks) if parks else Polygon()
                share = fp.intersection(real).area / fp.area
                if share <= PARK_WATER_MAX:
                    method += (f"; {wet / fp.area:.0%} on green land use that is vacant land, not a park "
                               f"({vacant_tags(pid, fp)})")
                elif not real.covers(Point(e, n)):
                    # the permit coordinate is off the park: keep the lot's non-park part around it
                    rest = lot.difference(real)
                    pieces = [g for g in getattr(rest, "geoms", [rest]) if g.area > 50]
                    if pieces:
                        lot = min(pieces, key=lambda g: g.distance(Point(e, n)))
                        fp, method, anchored = footprint(lot)
                        method += "; lot trimmed to the part off the park (permit coordinate is outside it)"
                        share = fp.intersection(real).area / fp.area
                    if share > PARK_WATER_MAX:
                        flags.append(f"{share:.0%} of the footprint on a park / water")
                else:
                    flags.append(f"{share:.0%} of the footprint on a park / water (the permit coordinate is in it too)")
        if min(widths(fp)) < 8.0 and r["status"] != "existing":
            flags.append(f"lot too narrow (footprint about {min(widths(fp)):.0f} m wide)")
        if lot.area > BIG_LOT_M2 and nb == 1 and r["status"] != "existing" and not anchored:
            flags.append(f"lot {lot.area:,.0f} m2 > {BIG_LOT_M2:,.0f} m2 for a single building")
        # per-tower heights (multi-tower rows): part_heights in the feature, tallest = height_m
        part_heights = None
        if fp.geom_type == "MultiPolygon" and len(fp.geoms) == nb:
            hm = float(r["height_m"])
            if m.get("tower_heights"):
                part_heights = [min(float(h), hm) for h in m["tower_heights"]]
            elif m.get("tower_storeys"):
                ts = m["tower_storeys"]
                part_heights = [round(hm * s / max(ts), 1) for s in ts]
            if part_heights and m.get("tallest_near"):   # tallest tower nearest a given point, then descending
                te, tn = to_local(m["tallest_near"][1], m["tallest_near"][0])
                order = sorted(range(nb), key=lambda i: fp.geoms[i].distance(Point(te, tn)))
                hs = sorted(part_heights, reverse=True)
                part_heights = [hs[order.index(i)] for i in range(nb)]
            if part_heights:
                method += " (tower heights " + " / ".join(f"{h:g}" for h in part_heights) + " m)"
        src = "needs_trace" if flags else f"auto: {how}; {method}"
        r["footprint_source"] = src
        g = wgs(fp)
        g = transform(lambda x, y, z=None: (round(x, 7), round(y, 7)), g)
        props = {"id": pid, "name": r["name"], "footprint_source": src}
        if part_heights:
            props["part_heights"] = part_heights
        if flags:
            props["trace_reason"] = "; ".join(flags)
            props["auto_footprint"] = f"{how}; {method}"
        props["lot_m2"] = round(lot.area)
        if parcel_area:
            props["parcel_m2"] = round(parcel_area)
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(g)})
        report.append((pid, r, how, method, lot.area, fp.area, flags, cid))
        lot_feats.append({"type": "Feature", "properties": {"id": pid, "lot": how},
                          "geometry": mapping(transform(lambda x, y, z=None: (round(x, 7), round(y, 7)), wgs(lot)))})

    # overlapping auto footprints (two rows describing one site)
    shapes = {f["properties"]["id"]: local(shape(f["geometry"])) for f in feats}
    ids = list(shapes)
    tree = STRtree([shapes[i] for i in ids])
    overlaps = []
    for a_i, a in enumerate(ids):
        for b_i in tree.query(shapes[a]):
            b = ids[b_i]
            if b <= a:
                continue
            ov = shapes[a].intersection(shapes[b]).area
            if ov > 0.2 * min(shapes[a].area, shapes[b].area):
                overlaps.append((a, b, ov))

    feats.sort(key=lambda f: int(f["properties"]["id"][1:]))
    out = {"type": "FeatureCollection", "features": feats}
    (DATA / "proposals.geojson").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with (DATA / "proposals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in COLUMNS} for r in rows)
    write_report(report, overlaps)
    # the reconstructed lots, for checking (not used by the build)
    (CACHE / "lots.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": lot_feats}))
    nt = [x for x in report if x[6]]
    print(f"data/proposals.geojson: {len(feats)} footprints ({len(report)} auto, {len(nt)} needs_trace, "
          f"{len(feats) - len(report)} kept as entered)")
    for pid, r, *_rest in nt:
        print(f"  needs_trace {pid} {r['name']}: {_rest[4]}")
    for a, b, ov in overlaps:
        print(f"  WARNING overlap {a} / {b}: {ov:.0f} m2")
    return 0


def write_report(report, overlaps):
    L = ["# Auto footprints (Phase 3B)", "",
         "Generated by `scripts/auto_footprints.py` (`make proposals`). Lots are rebuilt from City assessment-parcel "
         "centroids and zoning polygons (the City no longer publishes parcel polygons); see the script docstring.", "",
         "## needs_trace", "", "| id | name | address | why | auto footprint used meanwhile |", "|---|---|---|---|---|"]
    for pid, r, how, method, la, fa, flags, cid in report:
        if flags:
            L.append(f"| {pid} | {r['name']} | {r['address']} | {'; '.join(flags)} | {how}; {method} |")
    L += ["", "## All auto footprints", "", "| id | storeys | lot m² | footprint m² | lot | footprint |", "|---|---|---|---|---|---|"]
    for pid, r, how, method, la, fa, flags, cid in report:
        L.append(f"| {pid} | {r['storeys']} | {la:,.0f} | {fa:,.0f} | {how} | {method} |")
    if overlaps:
        L += ["", "## Overlapping footprints", ""] + [f"- {a} / {b}: {ov:,.0f} m²" for a, b, ov in overlaps]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
