#!/usr/bin/env python3
"""Compare City of Edmonton vs OSM/Overture footprints over the downtown core.

  make compare-footprints

Builds (or reuses) two full base variants under data/raw/compare/{city,overture}/ with
`fetch_base.py --footprints city|osm --dist ...`, then reports for the downtown core:
building count, footprint area and union coverage, how much of each set the other covers,
tall-building counts and heights from each base.glb. Renders views 01 and 03 from both
variants into data/raw/compare/renders/ for a visual check. Nothing in dist/ is touched.

Pass --no-fetch to reuse existing variant builds, --no-render to skip the renders.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_proposals import read_base_buildings  # noqa: E402
from common import RAW, ROOT, to_local  # noqa: E402

# Downtown core: 109 St to 97 St, the river-valley rim (~100 Ave) to 105 Ave.
CORE_WGS = (-113.5100, 53.5385, -113.4850, 53.5500)
CMP = RAW / "compare"
VARIANTS = {"city": "city", "overture": "osm"}


def core_box():
    x0, y0 = to_local(CORE_WGS[0], CORE_WGS[1])
    x1, y1 = to_local(CORE_WGS[2], CORE_WGS[3])
    return box(x0, y0, x1, y1)


def city_footprints():
    from fetch_base import clean_polygon, wgs_to_local
    paths = sorted(RAW.glob("city_footprints_*.json"), key=lambda p: "jpxi" not in p.name)
    data = json.loads(paths[0].read_text())
    out = [clean_polygon(wgs_to_local(shape(f["geometry"]))) for f in data["features"] if f.get("geometry")]
    return paths[0].stem.replace("city_footprints_", ""), [g for g in out if g is not None]


def overture_footprints():
    import pyarrow.parquet as pq
    from shapely import wkb
    from fetch_base import clean_polygon, wgs_to_local
    t = pq.read_table(RAW / "overture_building.parquet", columns=["geometry", "is_underground"]).to_pylist()
    out = [clean_polygon(wgs_to_local(wkb.loads(r["geometry"]))) for r in t if not r.get("is_underground")]
    return [g for g in out if g is not None]


def footprint_stats(geoms, core):
    sel = [g for g in geoms if core.contains(g.representative_point())]
    union = unary_union(sel).intersection(core) if sel else shapely.Polygon()
    return sel, {"count": len(sel), "area_sum_m2": round(sum(g.area for g in sel)),
                 "union_area_m2": round(union.area), "core_cover_pct": round(100 * union.area / core.area, 1),
                 "median_area_m2": round(float(np.median([g.area for g in sel])) if sel else 0)}, union


def glb_stats(dist, core):
    _, blds = read_base_buildings(Path(dist) / "base.glb")
    hs = []
    for (x0, y0, x1, y1), xy, top, bot in blds:
        if core.contains(shapely.Point((x0 + x1) / 2, (y0 + y1) / 2)):
            hs.append(top - bot - 1.0)
    hs = np.array(hs)
    return {"glb_buildings_in_core": int(hs.size), "over_50m": int((hs >= 50).sum()),
            "over_100m": int((hs >= 100).sum()), "max_height_m": round(float(hs.max()), 1) if hs.size else None,
            "p90_height_m": round(float(np.percentile(hs, 90)), 1) if hs.size else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()
    py = sys.executable
    for name, mode in VARIANTS.items():
        d = CMP / name
        if not args.no_fetch or not (d / "base.glb").exists():
            subprocess.run([py, "scripts/fetch_base.py", "--footprints", mode, "--dist", str(d)], cwd=ROOT, check=True)
        subprocess.run([py, "scripts/build_proposals.py", "--dist", str(d)], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)

    core = core_box()
    dsid, city = city_footprints()
    ovt = overture_footprints()
    c_sel, c_stats, c_union = footprint_stats(city, core)
    o_sel, o_stats, o_union = footprint_stats(ovt, core)
    c_stats.update(dataset=dsid, **glb_stats(CMP / "city", core))
    o_stats.update(dataset="overture buildings/building", **glb_stats(CMP / "overture", core))
    both = c_union.intersection(o_union).area
    report = {"core_wgs84": CORE_WGS, "core_area_m2": round(core.area), "city": c_stats, "overture": o_stats,
              "city_area_also_in_overture_pct": round(100 * both / max(c_union.area, 1), 1),
              "overture_area_also_in_city_pct": round(100 * both / max(o_union.area, 1), 1)}
    (CMP / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))

    if not args.no_render:
        for name in VARIANTS:
            subprocess.run([py, "scripts/render_views.py", "--views", "01,03", "--size", "600x800",
                            "--dist", str(CMP / name), "--out", str(CMP / "renders" / name)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
