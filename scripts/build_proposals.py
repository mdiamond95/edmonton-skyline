#!/usr/bin/env python3
"""Join data/proposals.csv onto data/proposals.geojson and write dist/proposals.json.

Also validates data/cameras.json and copies it to dist/cameras.json (the site only
serves web/, dist/ and renders/). Exits 1 on any validation error.

Height fallback when height_m is blank: storeys x 3.1 m (scope.md residential rate).
height_confidence (high / medium / low) is required; the viewer draws `low` rows as translucent
envelopes (a height ceiling, no confirmed design). footprint_source = needs_trace rows are listed in
the viewer's Trace mode for manual tracing.
Rows without a footprint in the GeoJSON get a 30 m square around lat/lon (warning).

Base buildings under a footprint (docs/scope.md): every base building in dist/base.glb with
at least half its outline inside a proposal footprint, or its centre inside it, is listed in
that proposal's `hide_base` and hidden by the viewer. For `existing` rows this is how the
official height replaces the measured (LiDAR) one: towers finished after the LiDAR survey,
or mis-measured, are entered as status=existing with their footprint and official height.
The indices are tied to the base.glb they were computed from (`base_glb` fingerprint), so
rerun `make build` after every `make fetch` (the Makefile does this).
"""
import csv
import json
import re
import struct
import sys
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPoint, Point, box, shape
from shapely.geometry.polygon import orient
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (DATA, STATUS_HEX, STATUSES, local_extent, to_local, to_wgs84,  # noqa: E402
                    write_json)

DIST = common.DIST
COLUMNS = ["id", "name", "address", "lat", "lon", "height_m", "storeys", "status", "developer",
           "source_url", "last_checked", "height_confidence", "height_source", "footprint_source", "candidate_id"]
CONFIDENCES = ("high", "medium", "low")


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def local_rings(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    out = []
    for p in polys:
        p = orient(p, 1.0)
        rings = [p.exterior] + list(p.interiors)
        out.append([[round(v, 2) for xy in list(r.coords)[:-1] for v in to_local(*xy)] for r in rings])
    return out


def build_proposals(errors, warnings):
    with (DATA / "proposals.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            errors.append(f"proposals.csv header must be {','.join(COLUMNS)}; got {reader.fieldnames}")
            return None
        rows = list(reader)
    gj = json.loads((DATA / "proposals.geojson").read_text(encoding="utf-8"))
    feats = {}
    for f in gj.get("features", []):
        fid = str((f.get("properties") or {}).get("id", "")).strip()
        if not fid:
            errors.append("proposals.geojson feature without properties.id")
            continue
        if fid in feats:
            errors.append(f"proposals.geojson duplicate id {fid}")
        feats[fid] = f

    ext = box(*local_extent())
    seen, out, dates = set(), [], []
    for n, r in enumerate(rows, start=2):
        pid = r["id"].strip()
        where = f"proposals.csv line {n} ({pid or 'no id'})"
        if not pid:
            errors.append(f"{where}: empty id")
            continue
        if pid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(pid)
        status = r["status"].strip().lower()
        if status not in STATUSES:
            errors.append(f"{where}: status '{r['status']}' not in {{{','.join(STATUSES)}}}")
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except ValueError:
            errors.append(f"{where}: lat/lon not numeric")
            continue
        storeys = None
        if r["storeys"].strip():
            try:
                storeys = int(r["storeys"])
            except ValueError:
                errors.append(f"{where}: storeys not an integer")
        height, hsrc = None, r["height_source"].strip()
        if r["height_m"].strip():
            try:
                height = float(r["height_m"])
            except ValueError:
                errors.append(f"{where}: height_m not numeric")
        elif storeys:
            height, hsrc = storeys * 3.1, (hsrc + "; " if hsrc else "") + "storeys x 3.1"
        conf = r["height_confidence"].strip().lower()
        if conf not in CONFIDENCES:
            errors.append(f"{where}: height_confidence '{r['height_confidence']}' not in {{{','.join(CONFIDENCES)}}}")
        if not height or height <= 0:
            errors.append(f"{where}: needs height_m or storeys")
            continue
        if not r["source_url"].strip().startswith("http"):
            errors.append(f"{where}: source_url required (scope.md)")
        try:
            dates.append(date.fromisoformat(r["last_checked"].strip()))
        except ValueError:
            errors.append(f"{where}: last_checked must be YYYY-MM-DD")
        fprops = (feats.get(pid) or {}).get("properties") or {}
        if pid in feats:
            geom = shape(feats[pid]["geometry"])
            if not geom.is_valid or geom.geom_type not in ("Polygon", "MultiPolygon"):
                errors.append(f"{where}: footprint must be a valid Polygon/MultiPolygon")
                continue
        else:
            warnings.append(f"{where}: no footprint in proposals.geojson; using 30 m square")
            e, nn = to_local(lon, lat)
            x0, y0 = to_wgs84(e - 15, nn - 15)
            x1, y1 = to_wgs84(e + 15, nn + 15)
            geom = box(x0, y0, x1, y1)
        e, nn = to_local(lon, lat)
        if not ext.contains(Point(e, nn)):
            warnings.append(f"{where}: outside scope bbox")
        out.append({
            "id": pid, "name": r["name"].strip(), "address": r["address"].strip(), "status": status,
            "height_m": round(height, 2), "height_source": hsrc, "height_confidence": conf, "storeys": storeys,
            "footprint_source": r["footprint_source"].strip() or fprops.get("footprint_source", ""),
            "trace_reason": fprops.get("trace_reason", ""), "candidate_id": r["candidate_id"].strip(),
            "developer": r["developer"].strip(), "source_url": r["source_url"].strip(),
            "last_checked": r["last_checked"].strip(), "lat": lat, "lon": lon,
            "local": [round(e, 2), round(nn, 2)], "footprint": local_rings(geom),
        })
    for fid in feats:
        if fid not in seen:
            warnings.append(f"proposals.geojson id {fid} has no CSV row (ignored)")
    return {
        "data_date": max(dates).isoformat() if dates else None,
        "status_colours": STATUS_HEX,
        "frame": "footprint rings are flat [east, north, ...] metres from origin (see terrain.json)",
        "proposals": out,
    }


def read_base_buildings(path):
    """dist/base.glb -> (fingerprint, [(bbox, xy points, top_y, bottom_y)] in table order), local EN metres."""
    buf = path.read_bytes()
    jlen = struct.unpack_from("<I", buf, 12)[0]
    gltf = json.loads(buf[20:20 + jlen])
    bin0 = 20 + jlen + 8
    node = gltf["nodes"][0]
    T, S = np.array(node.get("translation", [0, 0, 0])), np.array(node.get("scale", [1, 1, 1]))
    mesh = gltf["meshes"][node["mesh"]]

    def view(i):
        v = gltf["bufferViews"][i]
        return bin0 + v.get("byteOffset", 0), v["byteLength"], v.get("byteStride")
    prims = []
    for pr in mesh["primitives"]:
        acc = gltf["accessors"][pr["attributes"]["POSITION"]]
        off, ln, stride = view(acc["bufferView"])
        q = np.frombuffer(buf, np.uint16, ln // 2, off).reshape(-1, (stride or 6) // 2)[:acc["count"], :3]
        prims.append(q * S + T)
    tb = mesh["extras"]["buildingTable"]
    off, ln, _ = view(tb["bufferView"])
    table = np.frombuffer(buf, np.uint32, ln // 4, off).reshape(-1, 4)
    out = []
    for prim, first, count, _src in table:
        v = prims[prim][first:first + count]
        xy = np.c_[v[:, 0], -v[:, 2]]
        out.append(((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()), xy,
                    float(v[:, 1].max()), float(v[:, 1].min())))
    fingerprint = {"buildings": int(len(table)), "bytes": len(buf)}
    return fingerprint, out


def hide_base_under(proposals, warnings):
    """Mark base buildings under each proposal footprint (see module docstring)."""
    from shapely.geometry import Polygon
    glb = DIST / "base.glb"
    if not glb.exists():
        warnings.append("dist/base.glb missing; viewer will find base buildings to hide itself")
        return None
    fingerprint, blds = read_base_buildings(glb)
    boxes = np.array([b[0] for b in blds])
    for p in proposals:
        polys = []
        for rings in p["footprint"]:
            r = [np.asarray(x).reshape(-1, 2) for x in rings]
            polys.append(Polygon(r[0], r[1:]))
        hide, measured, kept = [], [], []
        for poly in polys:
            x0, y0, x1, y1 = poly.bounds
            cand = np.nonzero((boxes[:, 0] < x1) & (boxes[:, 2] > x0) & (boxes[:, 1] < y1) & (boxes[:, 3] > y0))[0]
            pp = prep(poly)
            for k in cand:
                _, xy, top, bot = blds[k]
                hull = MultiPoint(xy).convex_hull
                if hull.area <= 0:
                    continue
                overlap = poly.intersection(hull).area
                inside = pp.contains(Point(xy.mean(0))) or overlap >= 0.5 * hull.area
                if inside and int(k) not in hide:
                    hide.append(int(k))
                    measured.append(top - bot - 1.0)  # base sits 1 m below ground (fetch_base.py)
                elif not inside and overlap > 5 and top - bot - 1.0 > p["height_m"] - 1:
                    kept.append(round(overlap))  # partly under the footprint and taller: pokes through
        p["hide_base"] = sorted(hide)
        if kept:
            warnings.append(f"{p['id']}: {len(kept)} base building(s) overlap the footprint but stay visible "
                            f"(overlap m2: {kept}) and are taller, so they poke through")
        p["replaced_height_m"] = round(max(measured), 1) if measured else None
    return fingerprint


def build_cameras(errors):
    cams = json.loads((DATA / "cameras.json").read_text(encoding="utf-8"))
    ids = []
    for c in cams.get("cameras", []):
        cid = c.get("id", "")
        if not re.fullmatch(r"(0[1-9]|1[0-9]|20)", cid):
            errors.append(f"cameras.json: bad id {cid!r}")
        ids.append(cid)
        want = slugify(c.get("name", ""))
        if c.get("slug") != want:
            errors.append(f"cameras.json {cid}: slug should be {want!r}")
        for k in ("position", "target"):
            v = c.get(k)
            if v is not None and (not isinstance(v, list) or len(v) != 3):
                errors.append(f"cameras.json {cid}: {k} must be [east, north, elevation] or null")
        if (c.get("position") is None) != (c.get("target") is None):
            errors.append(f"cameras.json {cid}: set both position and target, or neither")
    if sorted(ids) != [f"{i:02d}" for i in range(1, 21)]:
        errors.append("cameras.json must define ids 01..20 exactly once")
    return cams


def main():
    global DIST
    if "--dist" in sys.argv:  # alternate output dir (scripts/compare_footprints.py)
        DIST = Path(sys.argv[sys.argv.index("--dist") + 1]).resolve()
    errors, warnings = [], []
    proposals = build_proposals(errors, warnings)
    cams = build_cameras(errors)
    if proposals is not None:
        proposals["base_glb"] = hide_base_under(proposals["proposals"], warnings)
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1
    write_json(DIST / "proposals.json", proposals, indent=1)
    write_json(DIST / "cameras.json", cams, indent=1)
    by, low, nt = {}, 0, []
    for p in proposals["proposals"]:
        by[p["status"]] = by.get(p["status"], 0) + 1
        low += p["height_confidence"] == "low"
        if p["footprint_source"] == "needs_trace":
            nt.append(p["id"])
    print(f"dist/proposals.json: {len(proposals['proposals'])} proposals {by}, data date {proposals['data_date']}; "
          f"{low} low-confidence (envelopes), {len(nt)} needs_trace {' '.join(nt)}")
    for p in proposals["proposals"]:
        hb = p.get("hide_base")
        if hb is None:
            continue
        note = ""
        if p["status"] == "existing" and hb:
            note = f"; official {p['height_m']:.1f} m replaces measured {p['replaced_height_m']:.1f} m"
        print(f"  {p['id']} {p['status']:<12} hides {len(hb)} base building(s){note}")
    set_cams = sum(1 for c in cams["cameras"] if c.get("position"))
    print(f"dist/cameras.json: {len(cams['cameras'])} cameras ({set_cams} with positions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
