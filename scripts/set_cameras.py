#!/usr/bin/env python3
"""Compute all 20 camera views in data/cameras.json from landmark coordinates.

  make cameras        # rewrite data/cameras.json, then `make build` copies it to dist/

Each aerial view is defined by where it looks (a landmark, lat/lon), which compass bearing
the camera sits on as seen from that landmark, its height above the landmark's ground, and
its pitch below horizontal. The script turns that into position/target in the cameras.json
frame ([east_m, north_m, elevation_m_ASL], local UTM 12N metres from the bbox centre) using
the terrain in dist/terrain.json + terrain.png. Eye-level views give the camera's lat/lon
and eye height instead.

Render spec (docs/scope.md, Phase 2 brief): 35 deg vertical FOV, portrait 3:4. Aerials at
roughly 300-900 m and 35-55 deg pitch; 16 and 17 near-ground eye level; 20 a high overview.
Landmark coordinates come from Overture Maps places / OSM street geometry (checked 2026-09-27).
To tweak a view, edit its row here and rerun; to capture a hand-framed view instead, use the
viewer's Copy camera button and paste the result over that entry (and delete its row here).
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, DIST, to_local  # noqa: E402

FOV = 35

# Landmarks (lat, lon)
L = {
    "core":        (53.5440, -113.4955),   # Jasper Ave / 101 St, centre of the downtown towers
    "ice":         (53.5462, -113.4968),   # Ice District: Rogers Place / Stantec Tower
    "core_west":   (53.5432, -113.5000),   # core as seen from the west (104 St)
    "oliver":      (53.5425, -113.5190),   # Wîhkwêntôwin towers, Jasper Ave / 116 St
    "quarters":    (53.5452, -113.4845),   # The Quarters / Boyle Street, 96 St / 103 Ave
    "rossdale":    (53.5325, -113.4930),   # Rossdale flats, power plant and the river bend
    "104ave":      (53.5472, -113.4990),   # between Station Lands (99 St) and MacEwan (109 St) on 104 Ave
    "warehouse":   (53.5438, -113.5000),   # 104 St / 102 Ave
    "jasper":      (53.5410, -113.5000),   # Jasper Ave / 104 St
    "legislature": (53.5335, -113.5066),   # Alberta Legislature Building
    "whyte":       (53.5182, -113.4975),   # Whyte Ave (82 Ave) / 104 St
    "garneau":     (53.5230, -113.5110),   # Garneau, 109 St / 86 Ave
    "strathcona":  (53.5190, -113.4900),   # Strathcona east of Gateway Blvd
    "blatchford":  (53.5680, -113.5150),   # Blatchford (former City Centre Airport)
    "exhibition":  (53.5640, -113.4660),   # between Commonwealth Stadium and the Expo Centre
    "valley":      (53.5260, -113.4972),   # river valley on the 104 St axis
    "centre":      (53.5450, -113.4950),   # scope bbox centre
}

# Aerials: id -> (landmark, bearing camera<-landmark in degrees from north, camera height above
# the landmark's ground m, pitch below horizontal deg, aim height above ground m)
AERIAL = {
    "01": ("core",        105, 900, 22, 70),   # from ESE: Quarters/Boyle low-rise foreground, core mid-frame, sky < 15%
    "02": ("ice",         175, 900, 21, 80),   # camera over the south-bank rim, river in the foreground
    "03": ("core_west",   272, 800, 35, 60),   # camera over Wîhkwêntôwin
    "04": ("core",        352, 900, 35, 60),   # camera over Central McDougall, Blatchford behind it
    "05": ("oliver",      160, 600, 40, 30),   # over Victoria Park, looking NNW
    "06": ("quarters",    120, 550, 40, 30),   # over Riverdale, looking WNW
    "07": ("rossdale",    160, 800, 40, 10),   # over the Cloverdale hill: river, Rossdale flats, downtown rim
    "08": ("104ave",       75, 600, 38, 30),   # along 104 Ave from the east: Station Lands near, MacEwan far
    "09": ("warehouse",   185, 450, 38, 20),   # along 104 St from the south
    "10": ("jasper",      272, 600, 32, 40),   # along Jasper Ave from the west
    "11": ("legislature", 190, 450, 40, 20),   # from over the river, dome and downtown behind
    "12": ("whyte",        75, 450, 38, 10),   # along Whyte Ave from the east
    "13": ("garneau",     185, 500, 35, 15),   # along 109 St toward the High Level Bridge
    "14": ("strathcona",   95, 900, 35, 10),   # from over Bonnie Doon
    "15": ("blatchford",  350, 900, 45, 5),
    "18": ("exhibition",  235, 800, 38, 10),   # along the Commonwealth -> Expo Centre axis
    "19": ("valley",      180, 900, 30, 0),    # camera over Whyte Ave; frame runs Whyte -> downtown
    "20": ("centre",      180, 16300, 60, 0),  # whole bbox fits the 26.6 deg horizontal FOV
}

# Eye level: id -> (camera lat, lon, eye elevation ASL or None for ground + eye_m, eye_m, look-at landmark, aim m)
EYE = {
    # High Level Bridge top deck is ~662 m ASL over the river (HRDEM 1 m DSM, 53.5298-53.5314 N);
    # the terrain model has no bridge, so the eye is set from that deck height.
    "16": (53.5302, -113.51115, 662.2 + 1.7, None, "core", 60),
    "17": (53.5588, -113.4466, None, 1.7, "core", 80),   # Ada Blvd NW at ~67 St, on the valley rim
}


class Terrain:
    def __init__(self):
        m = json.loads((DIST / "terrain.json").read_text())
        a = np.asarray(Image.open(DIST / m["heightmap"])).astype(np.float64)
        self.h = m["elevation"]["offset"] + a * m["elevation"]["scale"]
        self.e = m["extent"]
        self.w, self.ht = m["width"], m["height"]

    def at(self, east, north):
        e = self.e
        fx = np.clip((east - e["xmin"]) / (e["xmax"] - e["xmin"]) * (self.w - 1), 0, self.w - 1.001)
        fy = np.clip((e["ymax"] - north) / (e["ymax"] - e["ymin"]) * (self.ht - 1), 0, self.ht - 1.001)
        x0, y0 = int(fx), int(fy)
        tx, ty = fx - x0, fy - y0
        h = self.h
        return float((h[y0, x0] * (1 - tx) + h[y0, x0 + 1] * tx) * (1 - ty) +
                     (h[y0 + 1, x0] * (1 - tx) + h[y0 + 1, x0 + 1] * tx) * ty)


def main():
    t = Terrain()
    path = DATA / "cameras.json"
    cams = json.loads(path.read_text(encoding="utf-8"))
    for c in cams["cameras"]:
        cid = c["id"]
        c["fov"] = FOV
        if cid in AERIAL:
            lm, bearing, alt, pitch, aim = AERIAL[cid]
            e, n = to_local(L[lm][1], L[lm][0])
            g = t.at(e, n)
            d = (alt - aim) / math.tan(math.radians(pitch))
            b = math.radians(bearing)
            pos = [e + d * math.sin(b), n + d * math.cos(b), g + alt]
            tgt = [e, n, g + aim]
            c["aim"] = {"landmark": lm, "bearing": bearing, "height_m": alt, "pitch": pitch}
        elif cid in EYE:
            lat, lon, elev, eye, lm, aim = EYE[cid]
            e, n = to_local(lon, lat)
            pos = [e, n, elev if elev is not None else t.at(e, n) + eye]
            te, tn = to_local(L[lm][1], L[lm][0])
            tgt = [te, tn, t.at(te, tn) + aim]
            c["aim"] = {"landmark": lm, "eye_level": True}
        else:
            continue
        c["position"] = [round(v) for v in pos]
        c["target"] = [round(v) for v in tgt]
        dh = math.hypot(pos[0] - tgt[0], pos[1] - tgt[1])
        pitch = math.degrees(math.atan2(pos[2] - tgt[2], dh))
        print(f"{cid} {c['name']:<36} height {pos[2] - t.at(pos[0], pos[1]):6.0f} m AGL  pitch {pitch:5.1f}  "
              f"range {math.hypot(dh, pos[2] - tgt[2]):6.0f} m")
    cams["fov_note"] = "vertical FOV in degrees (render spec: 35); portrait 3:4 renders"
    cams["generator"] = "scripts/set_cameras.py (make cameras); aim = how each view was derived"
    path.write_text(json.dumps(cams, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
