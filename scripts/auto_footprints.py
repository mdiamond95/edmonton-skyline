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
footprint_source = needs_trace (the auto footprint is still written, so the row renders) when the lot
was not found, a single building sits on more than 8,000 m2, the footprint overlaps park or water
land use by more than 10%, or the lot is too narrow for the footprint (under about 8 m wide). Features whose footprint_source is not auto-generated (P001, anything traced
by hand in the viewer) are kept as they are. footprint_source is copied back into proposals.csv.
"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import requests
from shapely import affinity
from shapely.ops import voronoi_diagram
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, DIST, RAW, ROOT, to_local, to_wgs84  # noqa: E402
from build_proposals import read_base_buildings  # noqa: E402
from promote_candidates import COLUMNS, zoning_snapshot  # noqa: E402

CACHE = RAW / "footprints"
PARCELS = CACHE / "parcels_assessment.json"
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


def local(geom):
    return transform(lambda x, y, z=None: to_local(x, y), geom)


def wgs(geom):
    return transform(lambda x, y, z=None: to_wgs84(x, y), geom)


def load_parcels():
    if not PARCELS.exists():
        rows = requests.get("https://data.edmonton.ca/resource/dm3i-bp8w.json", timeout=180, params={
            "$select": "id,area,latitude,longitude", "$limit": 200000,
            "$where": "within_box(geometry_point,53.585,-113.560,53.505,-113.430)"}).json()
        CACHE.mkdir(parents=True, exist_ok=True)
        PARCELS.write_text(json.dumps(rows))
    rows = json.loads(PARCELS.read_text())
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


def towers(lot, n, area_each):
    ctr, u, L, S = axes(lot)
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
        clip = r.intersection(lot)
        rects.append(r if clip.area >= 0.9 * r.area else simplify_to(clip))
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
        # --- footprint
        if r["status"] == "existing":
            fp = existing_outline(blds, boxes, lot, e, n)
            method = "base-building outline(s) in the lot (official height replaces LiDAR)"
            if fp is None:
                fp, method = simplify_to(lot.buffer(-INSET_M, join_style="mitre")), f"lot inset {INSET_M:g} m"
        elif storeys <= 11:
            ins = lot.buffer(-INSET_M, join_style="mitre")
            if ins.is_empty or ins.area < 0.25 * lot.area:
                ins, method = lot.buffer(-1.0, join_style="mitre"), "lot inset 1 m (too narrow for 3 m)"
            else:
                method = f"lot inset {INSET_M:g} m"
            fp = simplify_to(ins)
        else:
            use = m.get("use", "residential")
            plate = FLOORPLATE.get(use, 1500.0)
            if m.get("floorplate_max"):   # the Direct Control text's own maximum tower floor plate
                plate = min(plate, float(m["floorplate_max"]))
            rects, a = towers(lot, nb, plate)
            fp = rects[0] if len(rects) == 1 else MultiPolygon(rects)
            method = (f"{nb} x {a:.0f} m2 {use} floorplate on the lot's long axis" if nb > 1
                      else f"{a:.0f} m2 {use} floorplate centred on the lot")
            if m.get("floorplate_max") and plate < FLOORPLATE.get(use, 1500.0):
                method += f" (DC maximum floor plate {plate:.0f} m2)"
            if a < plate - 1:
                method += f" (capped at {MAX_COVER:.0%} of the lot)"
        if not fp.is_valid:
            fp = fp.buffer(0)
        widths = [2 * g.area / g.length for g in getattr(fp, "geoms", [fp])]   # ~ width of a long thin shape
        if min(widths) < 8.0 and r["status"] != "existing":
            flags.append(f"lot too narrow (footprint about {min(widths):.0f} m wide)")
        if lot.area > BIG_LOT_M2 and nb == 1 and r["status"] != "existing":
            flags.append(f"lot {lot.area:,.0f} m2 > {BIG_LOT_M2:,.0f} m2 for a single building")
        gi = gtree.query(fp)
        wet = sum(fp.intersection(green[i]).area for i in gi) if len(gi) else 0.0
        if wet > PARK_WATER_MAX * fp.area:
            flags.append(f"{wet / fp.area:.0%} of the footprint on park / water land use")
        src = "needs_trace" if flags else f"auto: {how}; {method}"
        r["footprint_source"] = src
        g = wgs(fp)
        g = transform(lambda x, y, z=None: (round(x, 7), round(y, 7)), g)
        props = {"id": pid, "name": r["name"], "footprint_source": src}
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
