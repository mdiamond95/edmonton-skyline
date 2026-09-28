#!/usr/bin/env python3
"""Promote data/candidates.csv rows into data/proposals.csv (Phase 3B).

  make proposals        # this script, then scripts/auto_footprints.py, then make build

Selection (docs/scope.md inclusion rule), on storeys_final / height_final_m:
  >= 8 storeys (or >= 25 m) anywhere in the bbox; >= 6 storeys (or >= 20 m) inside a named node
  (candidates.csv `node` other than 'Other') or on a scope exception site. Alterations, conversions,
  use changes, parking and projects under 20 dwellings are dropped whatever their height.

Once a candidate is in proposals.csv (matched on candidate_id) its row is the record and is kept
exactly as it stands, hand edits included; a rerun only adds candidates not promoted yet, with the next
free P-number. `--rebuild` regenerates every promoted row from candidates.csv (ids stay). Rows without
a candidate_id (P001 Stantec Tower, anything entered by hand) are always kept as they are. To keep a
candidate out for good, add it to SKIP below.
Every dropped candidate and every judgment call is written to docs/promotion-log.md.

Columns added in Phase 3B: height_confidence, height_source, footprint_source (filled by
scripts/auto_footprints.py), candidate_id (';'-joined when adjacent rezonings of one DC are merged).
"""
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, RAW, ROOT  # noqa: E402
from fetch_candidates import BP_ID, exception_site, metres  # noqa: E402
import fill_heights as fh  # noqa: E402

COLUMNS = ["id", "name", "address", "lat", "lon", "height_m", "storeys", "status", "developer", "source_url",
           "last_checked", "height_confidence", "height_source", "footprint_source", "candidate_id"]
CACHE = RAW / "footprints"
ZONING_SNAPSHOT = CACHE / "zoning.json"
LOG = ROOT / "docs" / "promotion-log.md"
RES_M = 3.1

# --- Hand decisions (each one is logged) ----------------------------------------------------------
# Completed after the LiDAR survey and meeting the rule: status existing with the as-built height
# from the SkyriseCities project page (fetched and cached; the fallback numbers are what the page said
# on 2026-09-28). The footprint is the building's own outline in base.glb (auto_footprints.py).
EXISTING = {
    "386491616-002": {"name": "Oliver Crossing", "sr": "https://skyrisecities.com/database/projects/oliver-crossing.46965",
                      "storeys": 7, "height_m": 21.0},
    "392456153-002": {"name": "The Trax", "sr": "https://skyrisecities.com/database/projects/trax.38015",
                      "storeys": 6, "height_m": 24.08},
    "408272142-002": {"name": "Douglas Manor Addition", "sr": "https://skyrisecities.com/database/projects/douglas-manor-addition.42245",
                      "storeys": 6, "height_m": None},
}
# Flagged as work on an existing building by fill_heights.py, but they are revisions of a new building
# that is approved or under construction, so they stay.
NEW_BUILDING_REVISION = {
    "326906292-013": "revision of Falcon Tower One (38 -> 29 storeys), a new tower under construction",
    "315143826-064": "adds 12 storeys to the east tower of Stationlands Residential Towers (under construction)",
    "392214637-036": "exterior-material revision of a mixed-use building under construction (-002 is the original DP)",
    "573198770-006": "revision of a new mixed-use building ('basement removed from project')",
}
# Revisions that cut the building below the rule: storeys after the revision.
REVISED_STOREYS = {
    "298214168-149": (4, "building 4 cut from 12 to 4 storeys (building 3 from 6 to 4)"),
    "298214168-098": (6, "building 2 cut from 12 to 6 storeys"),
}
# Projects whose buildings each hold fewer than 20 units, although the DP total is 20 or more.
SMALL_PER_BUILDING = {
    "520979226-002": "2 supportive-housing buildings of 12 sleeping units each",
    "381366998-002": "2 lodging houses of 12 sleeping units each",
    "617134145-002": "cluster housing: 6 buildings of 8-14 dwellings",
}
# Station Lands: SkyriseCities lists the towers at 25 storeys / 90.0 m; this DP adds 12 storeys (x 3.1 m).
HEIGHT_OVERRIDE = {
    "315143826-064": (37, 127.2, "medium", "skyrisecities 90.0 m + 12 storeys x 3.1 m (dp_description)",
                      "SkyriseCities 'Stationlands Residential Towers' 25 storeys / 90.0 m + 12 storeys added by this DP"),
}
# Candidates never to promote (candidate_id: reason); a row already in proposals.csv is removed too.
SKIP = {
    "411454341-002": "Stadium Yards: 6 storeys in node 'Other' is below the rule (removed by Mark, Phase 3C)",
    "520417913-002": "City of Edmonton Garneau supportive housing (8231 111 St): four storeys, 34 units "
                     "(https://www.edmonton.ca/sites/default/files/public-files/Supportive-Housing-Garneau-Notification.pdf; "
                     "storeys per ConstructConnect / GEC Architecture); node needs 6",
    "368879276-002": "99 Street Apartment (9860 83 Ave): the 11-dwelling '99 Street Townhomes' building permit at 8305 99 St "
                     "(2025-06-19) is on the same lot (both 'Plan I8 Blk 75 Lots 1-2'), so this 27-dwelling scheme is dead "
                     "(removed by Mark's rule, Phase 4)",
}
# Zoning-map polygons that carry a DC link but are not that DC's site.
ZONING_ARTEFACT = {
    "REZ-DC-20932-207783": "1 km strip along the valley edge that repeats the DC 20932 link; the DC text describes "
                           "one site (existing 40 m east building + new 108 m west tower), which is polygon 173298",
}
# Area-wide rezonings: a zone ceiling laid over many lots (a City-initiated or district rezoning), not one
# project. Dropped unless listed in KEEP_AREA_REZONING. See area_wide().
AREA_MIN_M2, AREA_PARCELS, AREA_PARCELS_ANY, AREA_BIG_M2 = 8000.0, 5, 10, 20000.0
KEEP_AREA_REZONING = set()

# --- Phase 3C (2026-09-28): height and status rules from Mark (CLAUDE.md "Height and status rules") --------
# Applied to every row, kept or new, after promotion (phase3c()):
#   1. CORRECTIONS below (researched by hand; each one logged).
#   2. status = construction: the storeys named in the building-permit descriptions (24uj-dj8v) at the
#      address replace a zone / DC / estimate height (confidence high); a row that then falls below the
#      inclusion rule is removed.
#   3. SkyriseCities scheme above the DC / zone ceiling (dc_text / zone_max rows): a project pin within 40 m
#      of the permit, or inside the rezoned polygon, with more height than the ceiling. Forum activity in
#      the last 24 months -> the scheme is the proposal (medium, "exceeds current DC; scheme per
#      SkyriseCities"); otherwise the ceiling stays and the stale scheme is noted in height_source.
#   4. status = construction must end at medium or high; anything left low is listed as unresolved
#      (build_proposals.py refuses it).
SR_ACTIVE_DAYS = 730
SR_NEAR_M = 40.0
CORRECTIONS = {
    "364536438-002": dict(tower_storeys=[16, 19, 21],
                          why="DP text: 3 towers of 16, 19 and 21 storeys; each tower gets its own height (order along "
                              "the lot's long axis as listed, not known)"),
    "392214637-036": dict(name="ONE12", storeys=14, height_m=46.0, height_confidence="high",
                          height_source="skyrisecities 46.00 m + building permit 2024-10-31 ('existing 14 storey high-rise - ONE12 Tower')",
                          source_url="https://skyrisecities.com/database/projects/one12.47031",
                          why="SkyriseCities 'ONE12' (26 m away, Under Construction) 14 storeys / 46.00 m; building permit "
                              "calls it an existing 14-storey high-rise; LiDAR already shows a 42 m roof here. Above MU h40, "
                              "but the DP predates Zoning Bylaw 20001, so this is the as-built height, not a scheme"),
    "562465177-002": dict(storeys=6, height_m=18.6, height_confidence="medium",
                          height_source="estimate: 83 dwellings on a 1,773 m2 lot under RM h23 (6 storeys x 3.1)",
                          why="no storeys in the DP or building permits (hoarding only, 2026-05-14), nothing on SkyriseCities; "
                              "83 dwellings x ~85 m2 gross on the consolidated 1,773 m2 lot needs ~6 storeys, the most RM h23 "
                              "allows with a 3.1 m floor; listed unit numbers run to 4xx"),
    "315143826-064": dict(storeys=37, height_m=127.2, height_confidence="medium",
                          height_source="skyrisecities 90.0 m + 12 storeys x 3.1 m (dp_description)",
                          why="SkyriseCities 'Stationlands Residential Towers' 25 storeys / 90.0 m (both towers; Under "
                              "Construction, forum active 2026-09) + the 12 storeys this DP adds = 37 storeys / 127.2 m "
                              "(was 37 x 3.1 = 114.7 m, which ignored the taller podium storeys in the 90 m)"),
    "640132008-002": dict(name="Massey Ferguson Building Redevelopment", storeys=6, height_m=18.6, height_confidence="high",
                          height_source="skyrisecities 6 storeys + press (four six-storey buildings)", developer="ESH Housing Ltd.",
                          source_url="https://skyrisecities.com/database/projects/massey-ferguson-building-redevelopment.17582",
                          why="4 buildings / 696 dwellings (student housing): four six-storey mid-rises per Connect CRE and "
                              "Daily Hive (2026, via search; the sites are blocked from here), SkyriseCities 'Massey Ferguson "
                              "Building Redevelopment' (21 m) 6 storeys, 4 buildings. Was 12 storeys (dwellings estimate)"),
    "655770701-002": dict(name="ICE District Block BG residential tower", storeys=43, height_m=141.1, height_confidence="medium",
                          height_source="skyrisecities 'Ice District Tower B' 43 storeys / 141.12 m (Block BG residential tower)",
                          source_url="https://skyrisecities.com/database/projects/connect-centre.17572",
                          why="matched by coordinate: the nearest project is 'Connect Centre' (31 m), ICE District Block BG, "
                              "whose page says the residential tower was replaced by a shorter commercial tower. This DP puts a "
                              "386-dwelling residential tower back on the existing podium. SkyriseCities' Block BG residential "
                              "tower ('Ice District Tower B', mis-pinned, its URL now redirects to Connect Centre) is 43 storeys "
                              "/ 141.12 m; 386 dwellings over ~38 tower floors fits. AED zone ceiling 195 m"),
    "REZ-DC1-19860-167448": dict(name="La Reina Tower (Horne and Pitfield Building)", storeys=45, height_m=160.0,
                                 developer="Limak Investments",
                                 height_source="dc_text 160 m; 40-45 storeys per press (CBC 2022)",
                                 why="SkyriseCities 'Horne and Pitfield Building Redevelopment' (16 m, 10301 104 St, Limak "
                                     "Investments, forum active 2026-01) has no height; the rezoning (LDA21-0129 'La Reina "
                                     "Tower', Bylaw 19860 April 2022) is for a 40-45-storey tower inside the warehouse. DC1 "
                                     "19860 max 160.0 m (the 115 m in the text is the 5-year sunset fallback, April 2027). Not "
                                     "ICE District Phase 2 (that pin is 450 m north). Kept 160 m, storeys 51 -> 45"),
    "REZ-DC-20932-173298": dict(name="Jasper House", height_confidence="high",
                                height_source="dc_text 108 m; skyrisecities agrees (108.00 m)",
                                source_url="https://skyrisecities.com/database/projects/jasper-house.48762",
                                why="SkyriseCities 'Jasper House' (12021 Jasper Ave, 58 m, forum active 2026-08) 108.00 m = DC "
                                    "20932 west building 108 m; storeys unknown, 34 kept (108 / 3.1)"),
    "REZ-DC-21522-359895": dict(name="Jasper and 115 Street", storeys=52, height_m=170.0, height_confidence="medium",
                                height_source="skyrisecities 52 storeys / 170.00 m; second tower at the DC Area A ceiling 100 m",
                                developer="Greenlong Construction", tower_heights=[170.0, 100.0],
                                tallest_near=[53.541372, -113.518958],
                                source_url="https://skyrisecities.com/database/projects/jasper-and-115-street.28514",
                                why="DC 21522 allows Area A 100 m / Area B 180 m (the 60 m / 36.6 m read in Phase 3A.5 are "
                                    "the June 2029 sunset fallbacks). SkyriseCities 'Jasper and 115 Street' (pin inside the "
                                    "parcel, 2 buildings, forum active 2026-06) 52 storeys / 170.00 m fits Area B; the second "
                                    "tower is shown at the Area A ceiling. Tallest tower placed nearest the SkyriseCities pin"),
    "362851822-002": dict(name="Edmonton Motors Phase 1 - Area A", storeys=45, height_m=140.0,
                          height_source="dc_text (DC2-1064 Area A 140.0 m)", developer="Pangman Development Corp.",
                          why="DC2-1064 allows Area A 140.0 m / Area B 170.0 m (the 58 m read in Phase 3A.5 is the fallback "
                              "if no permit by June 2029). This DP is Area A; SkyriseCities 'Edmonton Motors Lands "
                              "Redevelopment' (56 storeys / 170.07 m, forum active 2026-03) is the Area B tower, which has no "
                              "DP or rezoning since 2021 and is not in the candidates. 140 m / 3.1 = 45 storeys"),
    # Phase 4 (Mark): Artists Quarters stays at its 18-storey scheme, shown as stalled.
    "REZ-MU-189192": dict(name="Artists Quarters", status="stalled", storeys=18, height_m=77.11, height_confidence="medium",
                          height_source="skyrisecities 18 storeys / 77.11 m (exceeds current zone ceiling MU h40); "
                                        "on hold; site downzoned 2024",
                          source_url="https://skyrisecities.com/database/projects/artists-quarters.18434",
                          why="decided by Mark (Phase 4): status stalled, keep 18 storeys / 77.1 m, on hold; site downzoned "
                              "2024 (SkyriseCities status On-Hold; the site went from DC1 to MU h40 f6.5 on 2024-07-08)"),
}

# Phase 4 (Mark): when a SkyriseCities scheme and a later rezoning disagree, the most recent dated source wins.
# For a rezoning row that adopted a SkyriseCities scheme above its new zone ceiling, the rezoning date (first
# on the zoning map) is compared with the project's last forum post each run: a newer rezoning puts the row
# back to the zone ceiling as an envelope (low); otherwise the scheme stays. See newest_source().

# Rows entered by hand (no candidate_id): why each one is there (written to the promotion log).
MANUAL_NOTES = {
    "P104": "Connect Centre (ICE District Block BG office tower, complete 2023) entered as `existing` at SkyriseCities' "
            "56.30 m / 16 storeys: it was finished after the LiDAR survey, so base.glb fell back to an OpenStreetMap "
            "height tag of 142 m (the old 43-storey / 141 m residential plan). Footprint: that building's base.glb outline, inset 2 m so the 12 m podium it stands on is not hidden with it",
    "P105": "Maclab Garneau (11120 86 Ave, Maclab Development Group; decided by Mark, Phase 4) entered as `construction` "
            "at SkyriseCities' 30 storeys / 98.14 m (Under Construction, 2 buildings). Its DP predates 2021, so it was never a "
            "candidate, and base.glb showed OpenStreetMap's two planned towers (building:part 108.0 m / 30 floors and "
            "82.5 m / 20 floors). Footprint: those two building:part outlines, which hides both phantoms. part_heights: "
            "east tower 98.1 m, west tower 65.4 m (98.14 x 20 / 30, OSM's floor counts; SkyriseCities gives only the "
            "taller one)",
}

# Rows removed from proposals.csv in Phase 3C / Phase 4: their former ids, for the log.
FORMER_ID = {"411454341-002": "P009", "392111925-002": "P013", "476200645-002": "P021", "520417913-002": "P023",
             "613818800-002": "P025", "368879276-002": "P016"}


def bp_storeys(address):
    """(storeys, date, text) named in the building-permit descriptions (24uj-dj8v) at an address, or None.
    The most storeys mentioned wins ('4-storey structural frame only for the future 6-storey building' -> 6).
    Cached in data/raw/heights/bp/."""
    path = fh.CACHE / "bp" / (re.sub(r"\W+", "_", address).strip("_") + ".json")
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        num, _, rest = address.partition(" - ")
        rows = requests.get(f"https://data.edmonton.ca/resource/{BP_ID}.json",
                            params={"$where": f"address like '%{num} - {rest}%'", "$limit": 300}, timeout=60).json()
        if not isinstance(rows, list):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    best = None
    for r in rows:
        d = r.get("job_description") or ""
        for m in re.finditer(r"\b(\d{1,2}|" + "|".join(fh.WORDS) + r")[ -]?stor(?:e)?y", d, re.I):
            s = fh._num(m.group(1))
            if s and (best is None or s > best[0]):
                best = (s, (r.get("permit_date") or "")[:10], d.strip()[:120])
    return best


def sr_last_post(url):
    """Date of the last forum post shown on a SkyriseCities project page (cached), or None."""
    m = re.search(r"\.(\d+)$", url or "")
    s = fh.fetch(url, fh.CACHE / "skyrise" / f"project_{m.group(1)}.html") if m else None
    mm = re.search(r"Last Post: (\w{3} \d{1,2}, \d{4})", " ".join(fh.page_text(s or "", start="<body")))
    return datetime.strptime(mm.group(1), "%b %d, %Y").date() if mm else None


def sr_scheme(row, cand, rez_geom, sr_projects):
    """The SkyriseCities project that stands for this site's scheme, if one shows more height than the row."""
    lat, lon = float(row["lat"]), float(row["lon"])
    best = None
    for p in sr_projects:
        if p["status"] in ("Complete", "Cancelled") or not (p["storeys"] or p["height"]):
            continue
        d = metres(lat, lon, p["lat"], p["lon"])
        # a rezoning: the pin must be inside the rezoned polygon; a permit: within 40 m of its coordinate
        if not (rez_geom.buffer(0.00005).contains(Point(p["lon"], p["lat"])) if rez_geom is not None else d <= SR_NEAR_M):
            continue
        h = p["height"] or p["storeys"] * RES_M
        if h > float(row["height_m"]) + 1.0 and (best is None or d < best[1]):
            best = (p, d, h)
    return best


def newest_source(row, c, calls):
    """Most recent dated source wins (Mark, Phase 4): a rezoning row carrying a SkyriseCities scheme above its
    zone ceiling keeps the scheme only if the project's last forum post is on or after the rezoning date."""
    rez = date.fromisoformat(c["decision_date"][:10]) if c.get("decision_date") else None
    last = sr_last_post(row["source_url"])
    if rez is None or last is None:
        calls.append(f"`{row['id']}` newest source: rezoning {rez or '?'} vs SkyriseCities last post {last or '?'} "
                     "(a date is missing): scheme kept")
        return
    if rez > last:
        row.update(storeys=c["storeys_final"], height_m=c["height_final_m"], height_confidence=c["height_confidence"],
                   source_url=c["height_source_url"] or c["url"],
                   height_source=f"{c['height_source']} (rezoning {rez} is newer than the SkyriseCities scheme's last post {last})")
        calls.append(f"`{row['id']}` newest source: rezoning {rez} is newer than SkyriseCities' last post {last}: "
                     f"back to the zone ceiling {row['height_m']} m ({row['height_confidence']})")
    else:
        calls.append(f"`{row['id']}` newest source: SkyriseCities' last post {last} is newer than the rezoning {rez}: "
                     f"{row['storeys']}-storey scheme kept")


AUTO_NAME = re.compile(r"^(.*) \((\d+)-storey ([\w-]+)\)$")


def phase3c(rows, cands, zpolys, sr_projects, calls, dropped):
    """Apply the Phase 3C rules (see above) to the finished proposals.csv rows, in place."""
    today = date.today()
    out = []
    for row in rows:
        cid = row.get("candidate_id") or ""
        c = cands.get(cid.split(";")[0])
        if c is None:
            out.append(row)
            continue
        pid = row["id"]
        before = (row["status"], row["storeys"], row["height_m"], row["height_confidence"])
        fix = CORRECTIONS.get(cid)
        if fix:
            for k in ("name", "status", "storeys", "height_m", "height_confidence", "height_source", "source_url", "developer"):
                if k in fix:
                    row[k] = str(fix[k]) if not isinstance(fix[k], float) else f"{fix[k]:g}"
        lat, lon = float(row["lat"]), float(row["lon"])
        node, site = c["node"], exception_site(lat, lon)
        # construction: building-permit storeys
        src = row["height_source"]
        if row["status"] == "construction" and not fix and not re.match(r"dp_description|skyrisecities \(as built\)|official", src):
            bp = bp_storeys(c["address"])
            if bp and str(bp[0]) != row["storeys"]:
                s, when, txt = bp
                rate = 4.0 if fh.office_only(c["description"]) else RES_M
                row.update(storeys=str(s), height_m=f"{s * rate:.1f}", height_confidence="high",
                           height_source=f"building permit {when} ('{txt}')",
                           source_url=f"https://data.edmonton.ca/resource/{BP_ID}.json?address="
                                      + c["address"].replace(" ", "%20"))
                calls.append(f"`{pid}` building permit {when}: {s} storeys ('{txt}')")
            if not meets_rule(num(row["storeys"], int), float(row["height_m"]), node, site):
                dropped.append((c, f"building permit: {row['storeys']} storeys, below the rule ({node}); "
                                   f"removed from proposals.csv ({pid})"))
                calls.append(f"`{pid}` removed: building permit says {row['storeys']} storeys, below the rule in node '{node}'")
                continue
        # SkyriseCities scheme above the DC / zone ceiling
        if not fix and row["height_source"].split(";")[0].strip() in ("dc_text", "zone_max"):
            rez = None
            if cid.startswith("REZ-"):
                ps = [shape(zpolys[x.rsplit("-", 1)[1]]["the_geom"]) for x in cid.split(";") if x.rsplit("-", 1)[1] in zpolys]
                rez = unary_union(ps) if ps else None
            hit = sr_scheme(row, c, rez, sr_projects)
            if hit:
                p, d, h = hit
                last = sr_last_post(p["url"])
                kind = "DC" if row["height_source"].startswith("dc_text") else "zone ceiling"
                where = "pin inside the rezoned parcel" if rez is not None else f"{d:.0f} m"
                if last and (today - last).days <= SR_ACTIVE_DAYS:
                    s = p["storeys"] or round(h / RES_M)
                    row.update(storeys=str(s), height_m=f"{h:g}", height_confidence="medium", source_url=p["url"],
                               height_source=f"skyrisecities (exceeds current {kind}; scheme per SkyriseCities)")
                    if AUTO_NAME.match(row["name"]):
                        row["name"] = p["title"]
                    if not row["developer"]:
                        row["developer"] = re.sub(r"\s+,", ",", sr_info(p["url"]).get("developer", ""))
                    calls.append(f"`{pid}` SkyriseCities '{p['title']}' ({where}, {p['status']}, last forum post {last}) "
                                 f"{s} storeys / {h:g} m exceeds the {kind} {before[2]} m: scheme adopted (medium)")
                elif "stale SkyriseCities" not in row["height_source"]:
                    row["height_source"] += (f"; stale SkyriseCities scheme '{p['title']}' {p['storeys'] or '?'} storeys "
                                             f"(last activity {last or 'none shown'})")
                    calls.append(f"`{pid}` SkyriseCities '{p['title']}' ({where}) {h:g} m exceeds the {kind}, but its last "
                                 f"forum post is {last or 'not shown'} (> 24 months): {kind} kept, stale scheme noted")
        if not fix and cid.startswith("REZ-") and row["height_source"].startswith("skyrisecities (exceeds current"):
            newest_source(row, c, calls)
        m = AUTO_NAME.match(row["name"])
        if m and m.group(2) != row["storeys"] and row["storeys"]:
            row["name"] = f"{m.group(1)} ({row['storeys']}-storey {m.group(3)})"
        after = (row["status"], row["storeys"], row["height_m"], row["height_confidence"])
        if fix and after != before:
            calls.append(f"`{pid}` {before[0]} {before[1]} storeys / {before[2]} m ({before[3]}) -> {after[0]} {after[1]} "
                         f"storeys / {after[2]} m ({after[3]}): {fix['why']}")
        elif fix:
            calls.append(f"`{pid}` {fix['why']}")
        if row["status"] == "construction" and row["height_confidence"] == "low":
            calls.append(f"`{pid}` UNRESOLVED: status construction with low height confidence")
        out.append(row)
    return out


NAME_STOP = re.compile(r"\d|dwelling|unit|use\b|site|total|reference|corner|housing|storey|building", re.I)


def load_candidates():
    with (DATA / "candidates.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_existing_proposals():
    p = DATA / "proposals.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def zoning_snapshot():
    """Latest Zoning Bylaw Map snapshot (67p2-r285) in the bbox, cached (also used by auto_footprints.py)."""
    if not ZONING_SNAPSHOT.exists():
        latest = requests.get(f"https://data.edmonton.ca/resource/{fh.ZH_ID}.json",
                              params={"$select": "max(as_of_date)"}, timeout=60).json()[0]["max_as_of_date"][:10]
        wkt = (f"POLYGON(({fh.LON_MIN} {fh.LAT_MIN},{fh.LON_MAX} {fh.LAT_MIN},{fh.LON_MAX} {fh.LAT_MAX},"
               f"{fh.LON_MIN} {fh.LAT_MAX},{fh.LON_MIN} {fh.LAT_MIN}))")
        rows = requests.get(f"https://data.edmonton.ca/resource/{fh.ZH_ID}.json", timeout=180, params={
            "$select": "polygon_id,zoning,url,the_geom", "$limit": 50000,
            "$where": f"as_of_date='{latest}' AND intersects(the_geom,'{wkt}')"}).json()
        CACHE.mkdir(parents=True, exist_ok=True)
        ZONING_SNAPSHOT.write_text(json.dumps({"as_of": latest, "polygons": rows}))
    d = json.loads(ZONING_SNAPSHOT.read_text())
    if isinstance(d, list):   # raw SODA list (older cache)
        d = {"as_of": None, "polygons": d}
    return d


PARCELS = CACHE / "parcels_assessment.json"


def parcel_rows():
    """Assessment parcel centroids + areas in the bbox (dm3i-bp8w), cached (also used by auto_footprints.py)."""
    if not PARCELS.exists():
        rows = requests.get("https://data.edmonton.ca/resource/dm3i-bp8w.json", timeout=180, params={
            "$select": "id,area,latitude,longitude", "$limit": 200000,
            "$where": "within_box(geometry_point,53.585,-113.560,53.505,-113.430)"}).json()
        CACHE.mkdir(parents=True, exist_ok=True)
        PARCELS.write_text(json.dumps(rows))
    return json.loads(PARCELS.read_text())


_parcels = None


def area_wide(geom_wgs, conf):
    """(is_area_wide, lot m2, parcels): > 8,000 m2 and either >= 10 assessment parcels, or a zone ceiling
    only (low confidence) over >= 5 parcels or > 20,000 m2."""
    global _parcels
    if _parcels is None:
        _parcels = [Point(float(r["longitude"]), float(r["latitude"])) for r in parcel_rows()]
    from shapely.prepared import prep
    lat = geom_wgs.centroid.y
    m2 = geom_wgs.area * 111_320 ** 2 * math.cos(math.radians(lat))
    pg = prep(geom_wgs)
    minx, miny, maxx, maxy = geom_wgs.bounds
    n = sum(1 for q in _parcels if minx <= q.x <= maxx and miny <= q.y <= maxy and pg.contains(q))
    wide = m2 > AREA_MIN_M2 and (n >= AREA_PARCELS_ANY or (conf == "low" and (n >= AREA_PARCELS or m2 > AREA_BIG_M2)))
    return wide, m2, n


def num(v, cast=float):
    try:
        return cast(v) if str(v).strip() else None
    except ValueError:
        return None


def meets_rule(storeys, height, node, site):
    big = (storeys or 0) >= 8 or (height or 0) >= 25
    mid = (storeys or 0) >= 6 or (height or 0) >= 20
    return big or ((node != "Other" or bool(site)) and mid)


def units(r):
    du = num(r["dwellings"], int)
    if du is None:
        m = re.search(r"(\d+)\s+(?:sleeping units?|dwellings?)", r["description"], re.I)
        du = int(m.group(1)) if m else None
    return du


def is_not_new(r):
    d = r["description"]
    return fh.is_conversion(d) or fh.is_existing_work(d) or bool(re.search(r"^to change the use|parking lot", d, re.I))


def tidy_address(a):
    """'10330 - 100 AVENUE NW' -> '10330 100 Avenue NW'."""
    a = re.sub(r"\s+-\s+", " ", a.strip())
    words = []
    for w in a.split():
        words.append(w if re.fullmatch(r"\d+[A-Z]?|NW|NE|SW|SE", w) else w.capitalize())
    return " ".join(words)


def sr_info(url):
    """Name, developer, buildings, storeys, height (m) from a SkyriseCities project page (cached)."""
    m = re.search(r"\.(\d+)$", url or "")
    if not m:
        return {}
    s = fh.fetch(url, fh.CACHE / "skyrise" / f"project_{m.group(1)}.html")
    if not s:
        return {}
    lines = fh.page_text(s, start="<body")
    out = {}
    for i, l in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if l == "Maximum Height":
            h = re.search(r"([\d.]+) m", nxt)
            out["height_m"] = float(h.group(1)) if h else None
        elif l == "Maximum Storeys" and nxt.isdigit():
            out["storeys"] = int(nxt)
        elif l == "Number of Buildings" and nxt.isdigit():
            out["buildings"] = int(nxt)
        elif l == "Developer" and "XXX" not in nxt:
            out["developer"] = nxt
    t = " ".join(lines)
    for k, pat in {"height_m": r"Maximum Height [\d,]+ ft / ([\d.]+) m", "storeys": r"Maximum Storeys (\d+)",
                   "buildings": r"Number of Buildings (\d+)", "developer": r"Project Companies Developer (.+?) (?:Architect|Units|Planner|Builder|Engineer|Landscape|Contractor|Marketing|Interior)"}.items():
        if k not in out:
            mm = re.search(pat, t)
            if mm:
                out[k] = mm.group(1) if k == "developer" else (float(mm.group(1)) if k == "height_m" else int(mm.group(1)))
    return out


def sr_match(note):
    """The SkyriseCities project a candidate was matched to in fill_heights.py: (title, url, trusted)."""
    for pat, trusted in [(r"SkyriseCities '([^']+)' matched by [^;|]*", True),
                         (r"SkyriseCities '([^']+)' \(\d+ storeys, [^)]*\): (https://\S+)", True),
                         (r"SkyriseCities agrees: \d+ storeys \((https://\S+)\)", True),
                         (r"SkyriseCities DISAGREES: '([^']+)' \([^)]*\): (https://\S+)", False)]:
        m = re.search(pat, note)
        if m:
            g = m.groups()
            if len(g) == 1 and g[0].startswith("http"):
                return None, g[0].rstrip(")"), trusted
            return g[0], (g[1] if len(g) > 1 else None), trusted
    return None


def dp_name(desc):
    for b in reversed(re.findall(r"\(([^()]{3,60})\)", desc)):
        b = b.strip()
        if not NAME_STOP.search(b) and re.match(r"^(The )?[A-Z]", b) and len(b.split()) <= 6:
            return b
    return None


def use_of(r):
    d = r["description"].lower()
    if r["source_dataset"].startswith("67p2"):
        z = (r["zone_current"] or r["description"].split("Rezoned to ")[-1]).split()[0].upper()
        return "residential" if z in {"RM", "RL", "RSM"} else "mixed-use"
    if "hotel" in d:
        return "hotel"
    if fh.office_only(r["description"]):
        return "office"
    if re.search(r"dwelling|residential|multi-unit|apartment|housing|group home|sleeping unit", d):
        return "residential"   # retail at grade does not change a residential floorplate
    return "mixed-use"


REBUILD = "--rebuild" in sys.argv


def main():
    today = date.today().isoformat()
    cands = load_candidates()
    zsnap = zoning_snapshot()
    zpolys = {p["polygon_id"]: p for p in zsnap["polygons"]}
    sr_projects = fh.load_skyrise()
    sr_by_url = {p["url"]: p for p in sr_projects}
    sr_by_title = {p["title"]: p for p in sr_projects}

    rez_geoms = []
    for r in cands:
        if r["permit_id"].startswith("REZ-"):
            pid = r["permit_id"].rsplit("-", 1)[1]
            if pid in zpolys:
                rez_geoms.append((r["permit_id"], shape(zpolys[pid]["the_geom"])))

    promoted, dropped, calls = [], [], []
    for r in cands:
        cid = r["permit_id"]
        lat, lon = float(r["lat"]), float(r["lon"])
        site = exception_site(lat, lon)
        storeys, height = num(r["storeys_final"], int), num(r["height_final_m"])
        conf, hsrc = r["height_confidence"], r["height_source"]
        hurl = r["height_source_url"] or r["url"]
        status = r["status_mapped"]
        du = units(r)
        why = None
        ex = EXISTING.get(cid)
        if cid in HEIGHT_OVERRIDE:
            storeys, height, conf, hsrc, note = HEIGHT_OVERRIDE[cid]
            height = height or round(storeys * RES_M, 1)
            calls.append(f"`{cid}` height: {note} = {storeys} storeys / {height} m ({conf})")
        known = storeys is not None or height is not None
        if cid == "451843020-002":
            why = "already P001 Stantec Tower (this DP is exterior work on it)"
        elif cid in SKIP:
            why = f"skipped by hand: {SKIP[cid]}"
        elif cid in ZONING_ARTEFACT:
            why = f"zoning-map artefact: {ZONING_ARTEFACT[cid]}"
        elif ex:
            info = sr_info(ex["sr"])
            storeys = info.get("storeys") or ex["storeys"]
            height = info.get("height_m") or ex["height_m"] or round(storeys * RES_M, 1)
            hsrc = "skyrisecities (as built)" if info.get("height_m") or ex["height_m"] else "skyrisecities storeys x 3.1"
            conf, hurl, status = "high", ex["sr"], "existing"
            if not meets_rule(storeys, height, r["node"], site):
                calls.append(f"`{cid}` {ex['name']}: {storeys} storeys in node '{r['node']}' is below the rule; "
                             "promoted as `existing` because the brief names it")
        elif known and not meets_rule(storeys, height, r["node"], site):
            why = "rule"   # below the height threshold: counted, not listed
        elif cid in REVISED_STOREYS:
            s2, txt = REVISED_STOREYS[cid]
            if not meets_rule(s2, s2 * RES_M, r["node"], site):
                why = f"revised below the rule: {txt} ({r['node']})"
        elif cid in SMALL_PER_BUILDING:
            why = f"fewer than 20 units per building: {SMALL_PER_BUILDING[cid]}"
        elif is_not_new(r) and cid not in NEW_BUILDING_REVISION:
            why = "not a new building (alteration, conversion, addition, use change or parking)"
        elif du is not None and du < 20:
            why = f"fewer than 20 dwellings ({du})"
        elif not known:
            why = "no height (still unknown after Phase 3A.5)"
        elif status == "unknown":
            zc = (r["zone_current"] or "").split(" ")[0]
            inside = [k for k, g in rez_geoms if g.contains(Point(lon, lat))]
            if zc.startswith("DC") or inside:
                status = "proposed"
                calls.append(f"`{cid}` status unknown -> proposed: site is under a rezoning "
                             f"({'Direct Control ' + zc if zc.startswith('DC') else inside[0]}); status_reason was "
                             f"'{r['status_reason']}'")
            else:
                why = f"status unknown and no rezoning on the site ({r['status_reason']})"
        if why is None and cid.startswith("REZ-") and cid not in KEEP_AREA_REZONING:
            pid = cid.rsplit("-", 1)[1]
            if pid in zpolys:
                wide, m2, npar = area_wide(shape(zpolys[pid]["the_geom"]), conf)
                if wide:
                    why = (f"area-wide rezoning, not one project: {m2:,.0f} m2 over {npar} assessment parcels "
                           f"({r['zone_current'] or cid.split('-')[1]}, {conf} confidence)")
        if why:
            dropped.append((r, why))
            continue
        if cid in NEW_BUILDING_REVISION:
            calls.append(f"`{cid}` kept although flagged as work on an existing building: {NEW_BUILDING_REVISION[cid]}")

        # name, developer, building count
        m = sr_match(r["height_note"])
        sr_title, sr_url, trusted = (m or (None, None, False))
        if sr_url is None and sr_title in sr_by_title:
            sr_url = sr_by_title[sr_title]["url"]
        if sr_title is None and sr_url in sr_by_url:
            sr_title = sr_by_url[sr_url]["title"]
        info = sr_info(sr_url) if (sr_url and trusted) else {}
        use = use_of(r)
        if cid == "315143826-064":   # the DP is for one of the project's two towers
            info = {}
        is_rez = cid.startswith("REZ-")
        if ex:
            name = ex["name"]
        elif cid == "315143826-064":
            name = "Stationlands Residential Towers (east tower)"
        else:
            name = dp_name(r["description"]) or (sr_title if trusted else None)
        address = tidy_address(r["address"]) if not is_rez else ""
        promoted.append({
            "cand": r, "cid": cid, "name": name, "address": address, "lat": lat, "lon": lon,
            "storeys": storeys, "height_m": round(height, 2) if height else round(storeys * RES_M, 1),
            "status": status, "developer": info.get("developer", "") if not ex else sr_info(ex["sr"]).get("developer", ""),
            "source_url": hurl, "height_confidence": conf, "height_source": hsrc, "use": use,
            "buildings": info.get("buildings"), "node": r["node"], "site": site,
        })

    # Adjacent rezoning polygons of the same Direct Control bylaw are one site: merge them.
    merged, used = [], set()
    rez = [p for p in promoted if p["cid"].startswith("REZ-DC")]
    for p in rez:
        if p["cid"] in used:
            continue
        dc = p["cid"].split("-")[2]
        group = [p]
        for q in rez:
            if q is not p and q["cid"] not in used and q["cid"].split("-")[2] == dc:
                group.append(q)
        if len(group) > 1:
            geoms = [shape(zpolys[g["cid"].rsplit("-", 1)[1]]["the_geom"]) for g in group]
            if unary_union([g.buffer(0.0002) for g in geoms]).geom_type == "Polygon":   # ~15 m: touching
                u = unary_union(geoms)
                pt = u.representative_point()
                keep = max(group, key=lambda g: (g["height_m"], g["cid"]))
                keep.update(cid=";".join(sorted(g["cid"] for g in group)), lat=pt.y, lon=pt.x)
                for g in group:
                    used.add(g["cid"])
                merged.append(keep)
                calls.append(f"merged adjacent rezonings of DC {dc}: {keep['cid']}")
    drop_ids = {g for m in merged for g in m["cid"].split(";")}
    promoted = [p for p in promoted if p["cid"] not in drop_ids and all(p is not m for m in merged)] + merged

    # Rezoning rows have no street address: take the nearest civic address (Parcel Addresses ut27-nrpn).
    for p in promoted:
        if not p["address"]:
            p["address"] = nearest_address(p["lat"], p["lon"])
        if not p["name"]:
            s = f"{p['storeys']}-storey" if p["storeys"] else f"{p['height_m']:.0f} m"
            where = (f"{p['node']} rezoned parcel" if p["address"].startswith("rezoned parcel at")
                     else p["address"].replace("near ", ""))
            p["name"] = f"{where} ({s} {p['use']})"

    # Phase 3C: a new construction row whose building permit names fewer storeys than the rule needs is
    # dropped before it takes an id (rows already in the CSV are checked in phase3c()).
    old = load_existing_proposals()
    in_csv = {o["candidate_id"] for o in old if o.get("candidate_id")}
    for p in list(promoted):
        if p["status"] == "construction" and p["cid"] not in in_csv and p["cid"] not in CORRECTIONS \
                and not re.match(r"dp_description|skyrisecities \(as built\)", p["height_source"]):
            bp = bp_storeys(p["cand"]["address"])
            if bp and not meets_rule(bp[0], bp[0] * RES_M, p["node"], p["site"]):
                promoted.remove(p)
                dropped.append((p["cand"], f"building permit {bp[1]}: {bp[0]} storeys ('{bp[2]}'), below the rule"))

    # Stable ids
    manual = [o for o in old if not o.get("candidate_id") and not o["name"].startswith("PLACEHOLDER")]
    by_cid = {o["candidate_id"]: o["id"] for o in old if o.get("candidate_id")}
    taken = {o["id"] for o in manual} | set(by_cid.values())
    nxt = max([int(re.sub(r"\D", "", i) or 0) for i in taken | {o["id"] for o in old}] + [0]) + 1
    status_rank = {"existing": 0, "construction": 1, "approved": 2, "proposed": 3, "stalled": 4}
    promoted.sort(key=lambda p: (status_rank[p["status"]], -p["height_m"], p["cid"]))
    for p in promoted:
        if p["cid"] in by_cid:
            p["id"] = by_cid[p["cid"]]
        else:
            p["id"] = f"P{nxt:03d}"
            nxt += 1
    old_fp = {o["id"]: o.get("footprint_source", "") for o in old}
    old_by_id = {o["id"]: o for o in old}
    rows, kept = [], 0
    for o in manual:
        o = {k: o.get(k, "") for k in COLUMNS}
        if o["id"] in MANUAL_NOTES:
            calls.append(f"`{o['id']}` (entered by hand) {MANUAL_NOTES[o['id']]}")
        if o["id"] == "P001":
            o.update(height_confidence=o["height_confidence"] or "high",
                     height_source=o["height_source"] or "official (Wikipedia, incl. spire)")
        rows.append(o)
    for p in sorted(promoted, key=lambda p: int(p["id"][1:])):
        if p["cid"] in by_cid and not REBUILD:   # once promoted, the CSV row is the record: keep it as edited
            rows.append({k: old_by_id[p["id"]].get(k, "") for k in COLUMNS})
            kept += 1
            continue
        rows.append({
            "id": p["id"], "name": p["name"], "address": p["address"], "lat": f"{p['lat']:.6f}", "lon": f"{p['lon']:.6f}",
            "height_m": f"{p['height_m']:g}", "storeys": p["storeys"] or "", "status": p["status"],
            "developer": p["developer"], "source_url": p["source_url"], "last_checked": today,
            "height_confidence": p["height_confidence"], "height_source": p["height_source"],
            "footprint_source": old_fp.get(p["id"], ""), "candidate_id": p["cid"]})
    gone = [o for o in old if o.get("candidate_id") and o["candidate_id"] not in {p["cid"] for p in promoted}]
    for o in gone:   # promoted earlier, no longer selected: keep the row, say so (unless skipped by hand)
        if o["candidate_id"] in SKIP:
            calls.append(f"`{o['id']}` {o['name']} removed from proposals.csv: {SKIP[o['candidate_id']]}")
            continue
        rows.append({k: o.get(k, "") for k in COLUMNS})
        calls.append(f"`{o['id']}` ({o['candidate_id']}) no longer meets the selection but was promoted before: kept")
    rows.sort(key=lambda o: int(o["id"][1:]))
    rows = phase3c(rows, {r["permit_id"]: r for r in cands}, zpolys, sr_projects, calls, dropped)
    with (DATA / "proposals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # sidecar for auto_footprints.py: use, building count, node
    for p in promoted:   # a DC that names residential towers sets the floorplate use
        if p["cid"].startswith("REZ-DC") and re.search(r"residential towers?\b", dc_text(p, zpolys), re.I):
            p["use"] = "residential"
    meta = {p["id"]: {"use": p["use"], "buildings": dc_towers(p, zpolys) or building_count(p), "node": p["node"], "site": p["site"],
                      "cid": p["cid"], "dwellings": units(p["cand"]), "floorplate_max": dc_floorplate(p, zpolys),
                      "from_permit": not p["cid"].startswith("REZ-")}
            for p in promoted}
    for p in promoted:   # per-tower heights: CORRECTIONS, else a DP that lists them ('16, 19, and 21 Storeys')
        fix, m = CORRECTIONS.get(p["cid"], {}), meta[p["id"]]
        ts = fix.get("tower_storeys") or tower_storeys(p["cand"]["description"])
        if ts and len(ts) == m["buildings"]:
            m["tower_storeys"] = ts
        if fix.get("tower_heights"):
            m["tower_heights"] = fix["tower_heights"]
            m["buildings"] = max(m["buildings"], len(fix["tower_heights"]))
        if fix.get("tallest_near"):
            m["tallest_near"] = fix["tallest_near"]
    (CACHE / "promote_meta.json").write_text(json.dumps(meta, indent=1))
    write_log(rows, promoted, dropped, calls, today)
    by = Counter(p["status"] for p in promoted)
    print(f"data/proposals.csv: {len(rows)} rows ({len(manual)} manual + {len(promoted)} promoted {dict(by)}, "
          f"{kept} of them kept as already in the CSV); "
          f"{len(dropped)} candidates dropped ({sum(1 for _, w in dropped if w == 'rule')} below the rule)")
    return 0


def building_count(p):
    """Towers on the site: DP text ('3 Residential buildings', '2 Multi-unit Housing buildings') or SkyriseCities."""
    d = p["cand"]["description"]
    m = re.search(r"\bconstruct (\d|two|three|four|five)\s+(?:[A-Z][\w-]*\s+){0,3}(?:buildings|towers)\b", d, re.I)
    if m:
        v = m.group(1).lower()
        return {"two": 2, "three": 3, "four": 4, "five": 5}.get(v) or int(v)
    # SkyriseCities counts buildings per project; a DP is usually one building of it, so only a
    # rezoning (the whole site) takes the project's count.
    return (p["buildings"] or 1) if p["cid"].startswith("REZ-") else 1


def tower_storeys(desc):
    """Per-building storeys a DP lists ('3 Apartment House buildings (16, 19, and 21 Storeys ...') -> [16, 19, 21]."""
    m = re.search(r"\b(\d{1,2}(?:\s*,\s*\d{1,2})*,?\s+and\s+\d{1,2})\s+Storeys", desc, re.I)
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else None


def dc_text(p, zpolys):
    pid = p["cid"].split(";")[0].rsplit("-", 1)[1]
    url = ((zpolys.get(pid) or {}).get("url") or {}).get("url")
    if not url:
        return ""
    s = fh.fetch(url, fh.CACHE / "dc" / (url.rstrip("/").split("/")[-1] + ".html"))
    return " ".join(fh.page_text(s or ""))


def dc_towers(p, zpolys):
    """Towers a Direct Control text names ('To accommodate two residential towers'), else None."""
    if not p["cid"].startswith("REZ-DC"):
        return None
    m = re.search(r"\b(two|three|four|five|[2-5])\s+(?:new\s+)?(?:residential\s+|mixed[- ]use\s+|high[- ]rise\s+)?towers\b",
                  dc_text(p, zpolys), re.I)
    return ({"two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1).lower()) or int(m.group(1))) if m else None


def dc_floorplate(p, zpolys):
    """A Direct Control rezoning's own maximum tower floor plate (m2), if its text states one."""
    if not p["cid"].startswith("REZ-DC"):
        return None
    pid = p["cid"].split(";")[0].rsplit("-", 1)[1]
    url = ((zpolys.get(pid) or {}).get("url") or {}).get("url")
    if not url:
        return None
    s = fh.fetch(url, fh.CACHE / "dc" / (url.rstrip("/").split("/")[-1] + ".html"))
    t = " ".join(fh.page_text(s or ""))
    m = re.search(r"Tower Floor Plates? (?:shall|must) (?:be|not exceed)[^0-9]{0,20}([\d,]+(?:\.\d+)?)\s*m\s*2", t, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def nearest_address(lat, lon):
    path = CACHE / "addresses" / f"{lat:.5f}_{lon:.5f}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        rows = []
        for rad in (60, 150, 400):   # the dataset's coordinates are text columns: cast, then box
            dlat, dlon = rad / 111_320, rad / (111_320 * math.cos(math.radians(lat)))
            rows = requests.get("https://data.edmonton.ca/resource/ut27-nrpn.json", timeout=60, params={
                "$where": f"latitude::number between {lat - dlat} and {lat + dlat} AND "
                          f"longitude::number between {lon - dlon} and {lon + dlon}", "$limit": 2000}).json()
            if isinstance(rows, list) and rows:
                break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    best = None
    for a in rows if isinstance(rows, list) else []:
        try:
            d = metres(lat, lon, float(a["latitude"]), float(a["longitude"]))
        except (KeyError, ValueError):
            continue
        if re.search(r"STATION|LRT", a.get("street_name", "")):
            continue
        if a.get("house_number") and a.get("street_name") and (best is None or d < best[0]):
            best = (d, f"{a['house_number']} {a['street_name']}")
    return f"near {tidy_address(best[1])}" if best else f"rezoned parcel at {lat:.5f}, {lon:.5f}"


def write_log(rows, promoted, dropped, calls, today):
    rule_n = sum(1 for _, w in dropped if w == "rule")
    L = [f"# Promotion log (Phase 3B, Phase 3C rules)", "",
         f"Generated {today} by `scripts/promote_candidates.py` (`make proposals`). "
         f"{len(promoted)} candidates promoted into `data/proposals.csv`; {len(dropped)} not promoted "
         f"({rule_n} below the inclusion rule, listed only as a count).", "",
         "## Dropped (other than the height rule)", "", "| candidate | node | address | storeys / m | why |", "|---|---|---|---|---|"]
    for r, why in dropped:
        if why == "rule":
            continue
        was = f" (was {FORMER_ID[r['permit_id']]})" if r["permit_id"] in FORMER_ID else ""
        L.append(f"| {r['permit_id']} | {r['node']} | {r['address']} | {r['storeys_final'] or '?'} / "
                 f"{r['height_final_m'] or '?'} | {why}{was} |")
    L += ["", "## Judgment calls", ""] + [f"- {c}" for c in calls]
    LOG.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
