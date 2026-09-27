#!/usr/bin/env python3
"""Join data/proposals.csv onto data/proposals.geojson and write dist/proposals.json.

Also validates data/cameras.json and copies it to dist/cameras.json (the site only
serves web/, dist/ and renders/). Exits 1 on any validation error.

Height fallback when height_m is blank: storeys x 3.1 m (scope.md residential rate).
Rows without a footprint in the GeoJSON get a 30 m square around lat/lon (warning).
"""
import csv
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from shapely.geometry import Point, box, shape
from shapely.geometry.polygon import orient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, DIST, STATUS_HEX, STATUSES, local_extent, to_local, to_wgs84,  # noqa: E402
                    write_json)

COLUMNS = ["id", "name", "address", "lat", "lon", "height_m", "storeys", "status", "developer",
           "source_url", "last_checked"]


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
        height, hsrc = None, "height_m"
        if r["height_m"].strip():
            try:
                height = float(r["height_m"])
            except ValueError:
                errors.append(f"{where}: height_m not numeric")
        elif storeys:
            height, hsrc = storeys * 3.1, "storeys x 3.1"
        if not height or height <= 0:
            errors.append(f"{where}: needs height_m or storeys")
            continue
        if not r["source_url"].strip().startswith("http"):
            errors.append(f"{where}: source_url required (scope.md)")
        try:
            dates.append(date.fromisoformat(r["last_checked"].strip()))
        except ValueError:
            errors.append(f"{where}: last_checked must be YYYY-MM-DD")
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
            "height_m": round(height, 2), "height_source": hsrc, "storeys": storeys,
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
    errors, warnings = [], []
    proposals = build_proposals(errors, warnings)
    cams = build_cameras(errors)
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1
    write_json(DIST / "proposals.json", proposals, indent=1)
    write_json(DIST / "cameras.json", cams, indent=1)
    by = {}
    for p in proposals["proposals"]:
        by[p["status"]] = by.get(p["status"], 0) + 1
    print(f"dist/proposals.json: {len(proposals['proposals'])} proposals {by}, data date {proposals['data_date']}")
    set_cams = sum(1 for c in cams["cameras"] if c.get("position"))
    print(f"dist/cameras.json: {len(cams['cameras'])} cameras ({set_cams} with positions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
