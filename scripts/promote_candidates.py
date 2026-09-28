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
from datetime import date
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, RAW, ROOT  # noqa: E402
from fetch_candidates import exception_site, metres  # noqa: E402
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
    "411454341-002": {"name": "Stadium Yards", "sr": "https://skyrisecities.com/database/projects/stadium-yards.39328",
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
# Station Lands: SkyriseCities lists the east tower at 25 storeys; this DP adds 12.
HEIGHT_OVERRIDE = {
    "315143826-064": (37, None, "low", "skyrisecities + dp_description",
                      "SkyriseCities 'Stationlands Residential Towers' 25 storeys + 12 storeys added by this DP"),
}
# Candidates never to promote (candidate_id: reason); e.g. a promoted row deleted by hand.
SKIP = {}
# Zoning-map polygons that carry a DC link but are not that DC's site.
ZONING_ARTEFACT = {
    "REZ-DC-20932-207783": "1 km strip along the valley edge that repeats the DC 20932 link; the DC text describes "
                           "one site (existing 40 m east building + new 108 m west tower), which is polygon 173298",
}
# Area-wide rezonings: a zone ceiling laid over many lots (a City-initiated or district rezoning), not one
# project. Dropped unless listed in KEEP_AREA_REZONING. See area_wide().
AREA_MIN_M2, AREA_PARCELS, AREA_PARCELS_ANY, AREA_BIG_M2 = 8000.0, 5, 10, 20000.0
KEEP_AREA_REZONING = set()
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


_parcels = None


def area_wide(geom_wgs, conf):
    """(is_area_wide, lot m2, parcels): > 8,000 m2 and either >= 10 assessment parcels, or a zone ceiling
    only (low confidence) over >= 5 parcels or > 20,000 m2."""
    global _parcels
    if _parcels is None:
        rows = json.loads((CACHE / "parcels_assessment.json").read_text())
        _parcels = [Point(float(r["longitude"]), float(r["latitude"])) for r in rows]
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
            calls.append(f"`{cid}` height: {note} = {storeys} storeys / {height} m (low)")
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

    # Stable ids
    old = load_existing_proposals()
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
    for o in gone:   # promoted earlier, no longer selected: keep the row, say so
        rows.append({k: o.get(k, "") for k in COLUMNS})
        calls.append(f"`{o['id']}` ({o['candidate_id']}) no longer meets the selection but was promoted before: kept")
    rows.sort(key=lambda o: int(o["id"][1:]))
    with (DATA / "proposals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # sidecar for auto_footprints.py: use, building count, node
    for p in promoted:   # a DC that names residential towers sets the floorplate use
        if p["cid"].startswith("REZ-DC") and re.search(r"residential towers?\b", dc_text(p, zpolys), re.I):
            p["use"] = "residential"
    meta = {p["id"]: {"use": p["use"], "buildings": dc_towers(p, zpolys) or building_count(p), "node": p["node"], "site": p["site"],
                      "cid": p["cid"], "dwellings": units(p["cand"]), "floorplate_max": dc_floorplate(p, zpolys)}
            for p in promoted}
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
    L = [f"# Promotion log (Phase 3B)", "",
         f"Generated {today} by `scripts/promote_candidates.py` (`make proposals`). "
         f"{len(promoted)} candidates promoted into `data/proposals.csv`; {len(dropped)} not promoted "
         f"({rule_n} below the inclusion rule, listed only as a count).", "",
         "## Dropped (other than the height rule)", "", "| candidate | node | address | storeys / m | why |", "|---|---|---|---|---|"]
    for r, why in dropped:
        if why == "rule":
            continue
        L.append(f"| {r['permit_id']} | {r['node']} | {r['address']} | {r['storeys_final'] or '?'} / "
                 f"{r['height_final_m'] or '?'} | {why} |")
    L += ["", "## Judgment calls", ""] + [f"- {c}" for c in calls]
    LOG.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
