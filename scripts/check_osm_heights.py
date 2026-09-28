#!/usr/bin/env python3
"""Cross-check tall base buildings whose height came from an OpenStreetMap tag against SkyriseCities.

  make osm-heights-check     # before every quarterly refresh (CLAUDE.md standing rule, Phase 4)

Where LiDAR shows ground (a tower finished after the survey), fetch_base.py falls back to OSM tags, and
those can hold a planned height rather than the built one: Connect Centre came through at 142 m (built
56.3 m, now P104) and Maclab Garneau as two towers at 108 m / 82.5 m (30 storeys / 98.14 m, now P105).
This lists every base building over 80 m on the OSM tier, whether a proposal footprint hides it, and the
nearest SkyriseCities project within 80 m with its height. A visible building with no SkyriseCities
project nearby, or one that differs by more than 10%, is marked CHECK: fix it with a `status: existing`
(or construction) row in data/proposals.csv and a footprint over it, never in fetch_base.py or base.glb.
Exits 1 when anything needs checking.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_proposals import read_base_buildings  # noqa: E402
from common import DIST, to_local, to_wgs84  # noqa: E402
import fill_heights as fh  # noqa: E402

MIN_M = 80.0
NEAR_M = 80.0
TOLERANCE = 0.10
OSM_TIER = 2   # fetch_base.SRC_CODES["osm"]


def source_tiers(path):
    buf = path.read_bytes()
    jlen = struct.unpack_from("<I", buf, 12)[0]
    gltf = json.loads(buf[20:20 + jlen])
    tb = gltf["meshes"][gltf["nodes"][0]["mesh"]]["extras"]["buildingTable"]
    v = gltf["bufferViews"][tb["bufferView"]]
    return np.frombuffer(buf, np.uint32, v["byteLength"] // 4, 20 + jlen + 8 + v.get("byteOffset", 0)).reshape(-1, 4)[:, 3]


def main():
    glb = DIST / "base.glb"
    _, blds = read_base_buildings(glb)
    tiers = source_tiers(glb)
    props = json.loads((DIST / "proposals.json").read_text())["proposals"]
    hidden = {k: p["id"] for p in props for k in p.get("hide_base", [])}
    sr = [p for p in fh.load_skyrise() if p["height"] or p["storeys"]]
    sr_xy = np.array([to_local(p["lon"], p["lat"]) for p in sr]) if sr else np.zeros((0, 2))
    problems = 0
    print(f"Base buildings over {MIN_M:g} m with an OSM height, vs SkyriseCities (within {NEAR_M:g} m):")
    for i, (_, xy, top, bot) in enumerate(blds):
        h = top - bot - 1.0   # the base sits 1 m below ground (fetch_base.py)
        if tiers[i] != OSM_TIER or h <= MIN_M:
            continue
        c = xy.mean(0)
        lon, lat = to_wgs84(*c)
        d = np.hypot(*(sr_xy - c).T) if len(sr) else np.array([])
        k = int(d.argmin()) if len(d) and d.min() <= NEAR_M else None
        if k is not None:
            p = sr[k]
            ph = p["height"] or p["storeys"] * 3.1
            match = f"'{p['title']}' {ph:.1f} m ({p['status'] or '?'}, {d[k]:.0f} m away)"
            off = abs(h - ph) / ph > TOLERANCE
        else:
            match, off = "no SkyriseCities project nearby", True
        if i in hidden:
            state = f"ok (hidden under {hidden[i]})"
        else:
            state = "CHECK" if off else "ok"
            problems += state == "CHECK"
        print(f"  #{i:<6} {h:6.1f} m  {lat:.5f}, {lon:.5f}  {match}  {state}")
    print(f"{problems} to check" if problems else "all consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
