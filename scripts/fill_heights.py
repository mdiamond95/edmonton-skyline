#!/usr/bin/env python3
"""Fill in heights for data/candidates.csv (Phase 3A.5) and regenerate docs/candidates-summary.md.

  make candidate-heights     # after `make candidates`; rereads the caches in data/raw/heights/

Adds, per row: zone_current, zone_max_m, storeys_final, height_final_m, height_source,
height_confidence (high / medium / low), height_source_url, height_note. Never writes proposals.csv.

Sources, in the order a row takes them (docs/data-sources.md, "Candidate heights"):
  dp_description      storeys / height already stated in the DP text (fetch_candidates.py)
  dc_text             Direct Control provision on zoningbylaw.edmonton.ca (the zone link on the
                      City's zoning map): maximum Height in metres and/or storeys. A DC is drafted
                      for one site, so it is a height, but still an envelope: medium confidence
                      (high when SkyriseCities agrees within 2 storeys).
  skyrisecities       SkyriseCities Edmonton database: the city page lists every project with
                      coordinates, storeys and height in one fetch; project pages within 120 m of
                      a candidate are read once each (2 s apart, cached) for their street address.
                      Matched by street address (high), else same street and block within 80 m, or
                      a named DP ("(The Clancy)") matching the project title (medium).
  zone_max            standard or special-area zone maximum Height (hNN modifier on the zoning map,
                      else the zone's table value). A ceiling, not a height: low confidence, and
                      only used when it caps the building below 12 storeys (<= 40 m); a higher
                      ceiling says nothing about the band and is kept in zone_max_m only.
  dwellings_estimate  last resort from the DP dwelling count: 20-59 -> 4-6 storeys (5),
                      60-149 -> 6-12 (9), 150+ -> 12+ (12); capped by the zone ceiling. Low.
Storeys <-> metres use docs/scope.md rates: 3.1 m per residential storey (4.0 m for office-only).
SkyriseCities sits above zone_max, one step higher than the Phase 3A.5 brief listed it: a
project's own storey count beats a zone ceiling, and scope.md's height priority lists
SkyriseCities but not zone maxima. Every judgment call is in docs/candidates-summary.md.
"""
import csv
import html
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, ROOT  # noqa: E402
from fetch_candidates import (COLUMNS, EXCEPTION_SITES, NODES, OUT_CSV, OUT_MD, ZH_ID, LAT_MAX, LAT_MIN,  # noqa: E402
                              LON_MAX, LON_MIN, exception_site, get, metres, table)

CACHE = RAW / "heights"
UA = "edmonton-skyline/3A.5 (candidate heights; https://github.com/mdiamond95/edmonton-skyline; 1 request / 2 s)"
DELAY = 2.0
ZB = "https://zoningbylaw.edmonton.ca"
SR = "https://skyrisecities.com"
SR_CITY = f"{SR}/database/cities/edmonton.14475"
SR_RADIUS, SR_BLOCK_M, SR_NAME_M = 120, 80, 250
RES_M, OFFICE_M = 3.1, 4.0
ZONE_MAX_USE_M = 40.0          # zone ceilings above this don't pin the storey band
NEW_COLUMNS = ["zone_current", "zone_max_m", "storeys_final", "height_final_m", "height_source",
               "height_confidence", "height_source_url", "height_note"]
BANDS = ["< 6", "6–7", "8–11", "12–19", "20+", "still unknown"]
CONFIDENCES = ["high", "medium", "low", "—"]

# Zone maxima without an hNN modifier on the map (Zoning Bylaw 20001, read 2026-09-27). Each is
# checked against the live page text at run time: (metres, clause, page path).
ZONE_MAX = {
    "RS":  (9.5, "4.1.6", "part-2-standard-zones-and-overlays/residential-zones/210-rs-small-scale-residential-zone"),
    "CN":  (12.0, "4.1", "part-2-standard-zones-and-overlays/commercial-zones/290-cn-neighbourhood-commercial-zone"),
    "CG":  (16.0, "4.1.1", "part-2-standard-zones-and-overlays/commercial-zones/2100-cg-general-commercial-zone"),
    "CB":  (16.0, "4.1.1", "part-2-standard-zones-and-overlays/commercial-zones/2110-cb-business-commercial-zone"),
    "BE":  (16.0, "4.1.1", "part-2-standard-zones-and-overlays/industrial-zones/2120-be-business-employment-zone"),
    "PSN": (14.0, "4.1", "part-2-standard-zones-and-overlays/open-space-and-urban-services-zones/2170-psn-neighbourhood-parks-and-services-zone"),
    "PS":  (16.0, "4.1", "part-2-standard-zones-and-overlays/open-space-and-urban-services-zones/2180-ps-parks-and-services-zone"),
    "PU":  (18.0, "4.1 (sites > 0.7 ha; 12.0 m otherwise)", "part-2-standard-zones-and-overlays/open-space-and-urban-services-zones/2190-pu-public-utility-zone"),
    "UF":  (16.0, "4.1.1", "part-2-standard-zones-and-overlays/open-space-and-urban-services-zones/2200-uf-urban-facilities-zone"),
    "UI":  (55.0, "4.1.5 (general; sub-area rules vary)", "part-2-standard-zones-and-overlays/open-space-and-urban-services-zones/2210-ui-urban-institution-zone"),
    "HDR": (50.0, "5.5.1", "part-3-special-area-zones/downtown-special-area/321-hdr-high-density-residential-zone"),
    "CMU": (70.0, "5.1.2 (north of 100 Ave; 50.0 m south)", "part-3-special-area-zones/downtown-special-area/322-cmu-commercial-mixed-use-zone"),
    "RMU": (50.0, "5.1.5", "part-3-special-area-zones/downtown-special-area/323-rmu-residential-mixed-use-zone"),
    "UW":  (50.0, "5.1.3", "part-3-special-area-zones/downtown-special-area/324-uw-urban-warehouse-zone"),
    "AED": (195.0, "5.4.2 (south of 104 Ave; 180 m north, 275 m for one tower)", "part-3-special-area-zones/downtown-special-area/325-aed-arena-entertainment-district-zone"),
    "CCA": (150.0, "5.1.4", "part-3-special-area-zones/downtown-special-area/327-cca-core-commercial-arts-zone"),
    "BP":  (10.0, "5.1.1", "part-3-special-area-zones/blatchford-special-area/341-bp-blatchford-parks-zone"),
    "BRH": (13.0, "5.1.3 (15.0 m abutting BP)", "part-3-special-area-zones/blatchford-special-area/342-brh-blatchford-row-housing-zone"),
}
# Zones whose maximum is the hNN modifier on the zoning map (the page says so).
H_MODIFIER_ZONES = {"RSM", "RM", "RL", "MU"}

_last = defaultdict(float)


def fetch(url, cache_path):
    """GET once, politely: cached forever, one request per DELAY seconds per host."""
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    host = urlparse(url).netloc
    wait = _last[host] + DELAY - time.time()
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            _last[host] = time.time()
            if r.status_code == 404:
                text = ""
                break
            r.raise_for_status()
            text = r.text
            break
        except requests.RequestException as e:
            _last[host] = time.time()
            if attempt == 2:
                print(f"    ! {url}: {e}")
                return None
            time.sleep(DELAY * (attempt + 2))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text


def page_text(s, start="<article"):
    """HTML -> plain lines (table cells joined with ' | ')."""
    i = s.find(start)
    t = s[i if i >= 0 else 0:]
    t = re.sub(r"<(script|style|nav)[^>]*>.*?</\1>", "", t, flags=re.S)
    t = re.sub(r"</(p|li|h\d|div|tr)>", "\n", t)
    t = re.sub(r"<(td|th)[^>]*>", " | ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return [l.strip() for l in t.split("\n") if l.strip()]


def floor_storeys(h, rate=RES_M):
    return max(1, int(h / rate + 1e-6))


# ---------------------------------------------------------------------------------------------
# Zoning: current zone per site (latest 67p2-r285 snapshot, the one fetch_candidates.py caches)
def load_zoning():
    latest = get(ZH_ID, {"$select": "max(as_of_date)"}, "zh_latest_date.json")[0]["max_as_of_date"][:10]
    bbox = (f"POLYGON(({LON_MIN} {LAT_MIN},{LON_MAX} {LAT_MIN},{LON_MAX} {LAT_MAX},{LON_MIN} {LAT_MAX},"
            f"{LON_MIN} {LAT_MIN}))")
    polys = get(ZH_ID, {"$select": "polygon_id,zoning,url,the_geom",
                        "$where": f"as_of_date='{latest}' AND intersects(the_geom,'{bbox}')", "$limit": 50000},
                f"zh_{latest}.json")
    geoms = [shape(p["the_geom"]) for p in polys]
    return latest, polys, geoms, STRtree(geoms)


def zone_at(zoning, lat, lon, geom=False):
    _, polys, geoms, tree = zoning
    pt = Point(lon, lat)
    hits = [i for i in tree.query(pt) if geoms[i].contains(pt)]
    if not hits:  # a DP point on a road edge: nearest polygon within ~15 m
        near = [i for i in tree.query(pt.buffer(0.0002)) if geoms[i].distance(pt) < 0.0002]
        hits = sorted(near, key=lambda i: geoms[i].distance(pt))[:1]
    if not hits:
        return (None, None, None) if geom else (None, None)
    p = polys[hits[0]]
    out = p["zoning"], (p.get("url") or {}).get("url")
    return out + (geoms[hits[0]],) if geom else out


def zb_page(url):
    slug = urlparse(url).path.strip("/").replace("/", "__") or "home"
    return fetch(url, CACHE / "zoningbylaw" / f"{slug}.html")


def check_zone_table():
    """Fetch each ZONE_MAX page once and confirm the value is still in its height table."""
    bad = []
    for code, (h, clause, path) in ZONE_MAX.items():
        s = zb_page(f"{ZB}/{path}")
        lines = page_text(s or "")
        joined = " // ".join(lines)
        val = f"{h:.1f}".rstrip("0").rstrip(".")
        if not re.search(rf"Maximum (building )?Height[^/]{{0,160}}(//[^/]{{0,40}}){{0,3}}\b{re.escape(val)}(\.0)? m", joined):
            bad.append(code)
    if bad:
        print(f"  ! zone table check: value not found on the page for {', '.join(bad)} (bylaw amended? update ZONE_MAX)")
    return bad


DC_SKIP = re.compile(r"street wall|podium|ground floor|fence|screen|sign|stepback|setback|floor[- ]to|parapet|"
                     r"platform|canop|landscap|accessory|garage|separation|floor plate|amenity|retaining|light|"
                     r"antenna|mechanical|within \d|of any portion|storey height|first storey|basement|shed", re.I)
NUM_M = r"(\d{1,3}(?:\.\d+)?)\s*(?:m|metres|meters)\b(?!\s*2)"
DC_H = [  # (pattern, group holding the subject, group holding the value)
    re.compile(r"max(?:imum)?\.?\s+((?:total\s+|overall\s+|building\s+)?height[^.;:]{0,90}?)"
               r"(?:is|shall be|must be|shall not exceed|must not exceed|will be|of)\s+(?:approximately\s+)?" + NUM_M, re.I),
    re.compile(r"((?:building\s+)?height[^.;:]{0,60}?)(?:shall|must) not exceed\s+(?:the lesser of\s+|or\s+)?" + NUM_M, re.I),
]
DC_S = [
    re.compile(r"(?:max(?:imum)?|not exceed|up to(?: approximately)?|approximately)[^.;:]{0,40}?"
               r"(\d{1,2}|[a-z]+(?:[- ][a-z]+)?)\s+storeys?", re.I),
]
WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
WORDS.update({"thirty": 30, "forty": 40, "fifty": 50})


def _num(w):
    w = w.lower().replace("-", " ").split()
    if w and w[-1].isdigit():
        return int(w[-1])
    if len(w) == 2 and w[0] in WORDS and w[1] in WORDS:
        return WORDS[w[0]] + WORDS[w[1]]
    return WORDS.get(w[-1]) if w else None


def parse_dc(lines):
    """Maximum Height (m) and storeys stated in a Direct Control provision.

    Reads 'maximum Height ... is / shall be / shall not exceed N m' phrases and 'maximum ... N Storeys',
    plus height tables ('Maximum Height' header, then '24 meters' cells). Limits whose subject is a
    podium, street wall, stepback, setback, fence, sign etc. are skipped. Returns the largest metre
    value (the tallest tower or area), the largest storey count, and the matched snippets."""
    hs, ss, snips = [], [], []
    text = "\n".join(lines)
    for pat in DC_H:
        for m in pat.finditer(text):
            if DC_SKIP.search(m.group(1)):
                continue
            v = float(m.group(2))
            if 6 <= v <= 320:
                hs.append(v)
                snips.append(re.sub(r"\s*\|\s*|\s+", " ", text[max(0, m.start() - 10):m.end()]).strip()[:120])
    for pat in DC_S:
        for m in pat.finditer(text):
            ctx = text[max(0, m.start() - 40):m.end()]
            if DC_SKIP.search(ctx) or re.search(r"lower|first|above the|below the", ctx, re.I):
                continue
            v = _num(m.group(1))
            if v and 2 <= v <= 90:
                ss.append(v)
                snips.append(re.sub(r"\s+", " ", ctx).strip()[:120])
    # table form: a 'Maximum Height' header cell, values like '24 meters' in the following cells
    for i, l in enumerate(lines):
        if re.fullmatch(r"\|?\s*Maximum (?:building )?Height\s*\|?", l, re.I):
            for v in lines[i + 1:i + 16]:
                m = re.fullmatch(r"\|?\s*" + NUM_M + r"\s*\|?", v, re.I)
                if m and 6 <= float(m.group(1)) <= 320:
                    hs.append(float(m.group(1)))
                    snips.append(f"table: Maximum Height {m.group(1)} m")
    return (max(hs) if hs else None), (max(ss) if ss else None), list(dict.fromkeys(snips))


# ---------------------------------------------------------------------------------------------
# SkyriseCities
def load_skyrise():
    s = fetch(SR_CITY, CACHE / "skyrise" / "city_edmonton.14475.html")
    if not s:
        return []
    i = s.find("var projects = ")
    projs, _ = json.JSONDecoder().raw_decode(s[i + len("var projects = "):])
    out = []
    for p in projs:
        try:
            lat, lon = float(p["latitude"]), float(p["longitude"])
        except (TypeError, ValueError):
            continue
        m = re.match(r"(\d+)", p.get("storeys") or "")
        h = re.search(r"([\d.]+) m", p.get("height") or "")
        out.append({"id": p["id"], "title": p["title"], "url": f"{SR}/{p['path']}", "lat": lat, "lon": lon,
                    "storeys": int(m.group(1)) if m else None, "height": float(h.group(1)) if h else None,
                    "status": p.get("status") or "", "completion": p.get("completion") or "",
                    "category": p.get("category") or ""})
    return out


def sr_address(p):
    s = fetch(p["url"], CACHE / "skyrise" / f"project_{p['id']}.html")
    if not s:
        return None
    lines = page_text(s, start="<body")
    for i, l in enumerate(lines[:-1]):
        if l == "Address":
            return lines[i + 1]
    return None


STREET_ABBR = {"ST": "STREET", "AVE": "AVENUE", "AV": "AVENUE", "RD": "ROAD", "BLVD": "BOULEVARD", "DR": "DRIVE",
               "WAY": "WAY", "CRES": "CRESCENT"}


def addr_key(a):
    """'10019 104 Street NW, Edmonton' / '10019 - 104 STREET NW' -> ('10019', '104 STREET')."""
    a = (a or "").upper().split(",")[0]
    a = re.sub(r"\s+-\s+", " ", a)
    m = re.match(r"^(\d+[A-Z]?)\s+(\d+[A-Z]?)\s+([A-Z]+)", a.strip())
    if not m:
        return None
    return m.group(1), f"{m.group(2)} {STREET_ABBR.get(m.group(3), m.group(3))}"


def name_tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in
            {"the", "and", "tower", "towers", "building", "phase", "residences", "condos", "edmonton", "street", "avenue"}}


def match_skyrise(row, projects, parcel=None):
    lat, lon = float(row["lat"]), float(row["lon"])
    near = sorted(((metres(lat, lon, p["lat"], p["lon"]), p) for p in projects), key=lambda t: t[0])
    ak = addr_key(row["address"])
    named = re.findall(r"\(([^()]{3,60})\)\.?\s*(?:\[|$)", row["description"])
    ntok = name_tokens(named[-1]) if named else set()
    best = None
    for d, p in near:
        if d > SR_NAME_M:
            break
        name_hit = bool(ntok) and len(ntok & name_tokens(p["title"])) >= max(1, len(ntok) - 1)
        if d > SR_RADIUS and not name_hit:
            continue
        pk = addr_key(sr_address(p)) if d <= SR_RADIUS else None
        if ak and pk and pk == ak:
            how, conf = f"address {pk[0]} {pk[1]}", "high"
        elif name_hit:
            how, conf = f"project name '{named[-1]}', {d:.0f} m", "medium"
        elif ak and pk and pk[1] == ak[1] and abs(int(re.sub(r'\D', '', pk[0])) - int(re.sub(r'\D', '', ak[0]))) < 100 \
                and d <= SR_BLOCK_M:
            how, conf = f"same block of {pk[1]} ({pk[0]} vs {ak[0]}), {d:.0f} m", "medium"
        else:
            continue
        if best is None or (conf == "high" and best[2] != "high"):
            best = (p, how, conf, d)
        if conf == "high":
            break
    if best is None and parcel is not None:   # rezoning rows: no street address, match the parcel itself
        inside = [(d, p) for d, p in near if d <= 600 and parcel.contains(Point(p["lon"], p["lat"]))]
        if inside:
            d, p = inside[0]
            more = f", 1 of {len(inside)} projects in it" if len(inside) > 1 else ""
            best = (p, f"pin inside the rezoned parcel{more}", "medium", d)
    return best


# ---------------------------------------------------------------------------------------------
def dwellings_estimate(du):
    if du >= 150:
        return 12, "12+"
    if du >= 60:
        return 9, "6–12"
    if du >= 20:
        return 5, "4–6"
    return None, None


def office_only(desc):
    d = desc.lower()
    return bool(re.search(r"\boffice\b", d)) and not re.search(r"dwelling|residential|multi-unit|apartment|hotel", d)


def is_conversion(desc):
    return bool(re.search(r"^to convert|conversion of (an )?existing|convert (a portion of )?(an )?existing", desc, re.I))


def band_of(s):
    if s in ("", None):
        return "still unknown"
    s = int(s)
    return "20+" if s >= 20 else "12–19" if s >= 12 else "8–11" if s >= 8 else "6–7" if s >= 6 else "< 6"


def main():
    today = date.today()
    rows = list(csv.DictReader(OUT_CSV.open(encoding="utf-8")))
    print(f"{len(rows)} candidates in {OUT_CSV.relative_to(ROOT)}")
    zoning = load_zoning()
    print(f"  zoning snapshot {zoning[0]} ({len(zoning[1])} polygons in bbox)")
    bad_zone = check_zone_table()
    projects = load_skyrise()
    print(f"  SkyriseCities Edmonton database: {len(projects)} projects "
          f"({sum(1 for p in projects if p['storeys'])} with storeys)")

    dc_cache = {}
    stats = Counter()
    for r in rows:
        desc = r["description"]
        rate = OFFICE_M if office_only(desc) else RES_M
        notes = []
        zone, zurl, parcel = zone_at(zoning, float(r["lat"]), float(r["lon"]), geom=True)
        rezoning = r["source_dataset"].startswith("67p2")
        if rezoning and r["url"].startswith(ZB):
            zurl = r["url"]    # the rezoned polygon's own link
        r["zone_current"] = zone or ""
        code = (zone or "").split(" ")[0]
        zmax, zmax_url = None, ""
        m = re.search(r"\bh(\d+(?:\.\d+)?)", zone or "")
        if m and code in H_MODIFIER_ZONES | set(ZONE_MAX):
            zmax, zmax_url = float(m.group(1)), zurl or ""
        elif code in ZONE_MAX:
            zmax, zmax_url = ZONE_MAX[code][0], f"{ZB}/{ZONE_MAX[code][2]}"
        r["zone_max_m"] = f"{zmax:g}" if zmax else ""

        # evidence ---------------------------------------------------------------------------
        dc = None
        if code.startswith("DC") and zurl and re.search(r"/dc[12]?-\d+", zurl):
            if zurl not in dc_cache:
                s = zb_page(zurl)
                dc_cache[zurl] = parse_dc(page_text(s)) if s else (None, None, [])
            dc = dc_cache[zurl]
        sr = match_skyrise(r, projects, parcel if rezoning else None)

        # choose -----------------------------------------------------------------------------
        st = ht = None
        src = conf = url = ""
        if r["storeys_guess"] or (r["height_guess_m"] and r["source_dataset"].startswith("2ccn")):
            st = int(r["storeys_guess"]) if r["storeys_guess"] else floor_storeys(float(r["height_guess_m"]), rate)
            ht = float(r["height_guess_m"]) if r["height_guess_m"] else round(st * rate, 1)
            src, conf, url = "dp_description", "high", r["url"]
        elif dc and (dc[0] or dc[1]):
            h, s, snips = dc
            # stated storeys only when they fit the tallest height (a multi-building DC may give storeys
            # for a low building and metres for the tower)
            st = s if s and (not h or s * rate >= 0.75 * h) else floor_storeys(h, rate)
            ht = h or round(s * rate, 1)
            src, conf, url = "dc_text", "medium", zurl
            notes.append("DC: " + " / ".join(snips[:2]))
            if sr and sr[0]["storeys"] and abs(sr[0]["storeys"] - st) <= 2:
                conf = "high"
                notes.append(f"SkyriseCities agrees: {sr[0]['storeys']} storeys ({sr[0]['url']})")
        elif sr and (sr[0]["storeys"] or sr[0]["height"]):
            p = sr[0]
            st = p["storeys"] or floor_storeys(p["height"], rate)
            ht = p["height"] or round(st * rate, 1)
            src, conf, url = "skyrisecities", sr[2], p["url"]
            notes.append(f"SkyriseCities '{p['title']}' matched by {sr[1]}; {p['status']}"
                         + (f" {p['completion']}" if p["completion"] not in ("", "TBD") else ""))
            if zmax and ht > zmax + 0.5:
                notes.append(f"above the {zone} ceiling {zmax:g} m (variance or rezoning)")
        elif zmax and (zmax <= ZONE_MAX_USE_M or rezoning):
            st, ht = floor_storeys(zmax, rate), zmax
            src, conf, url = "zone_max", "low", zmax_url
            notes.append(f"ceiling of {zone} ({zmax:g} m), not a design height"
                         + ("; the site was rezoned to it" if rezoning else ""))
        else:
            est, rng = dwellings_estimate(int(r["dwellings"] or 0))
            if est:
                cap = floor_storeys(zmax, rate) if zmax else None
                st = min(est, cap) if cap else est
                ht = round(st * rate, 1)
                src, conf, url = "dwellings_estimate", "low", r["url"]
                notes.append(f"{r['dwellings']} dwellings -> est {rng} storeys"
                             + (f", capped by {zone} ceiling {zmax:g} m" if cap and cap < est else ""))
        if not src:
            why = []
            if code.startswith("DC"):
                why.append("DC text states no height" if dc else "DC text not found")
            if zmax:
                why.append(f"only a high zone ceiling ({zone}: {zmax:g} m)")
            if not r["dwellings"]:
                why.append("no dwelling count")
            elif int(r["dwellings"]) < 20:
                why.append(f"{r['dwellings']} dwellings (< 20, no estimate)")
            if not zone:
                why.append("no zone at the point")
            notes.append("unknown: " + "; ".join(why))
        if dc and src != "dc_text" and (dc[0] or dc[1]):
            notes.append(f"DC allows {dc[0] or '?'} m / {dc[1] or '?'} storeys")
        if sr and src not in ("skyrisecities",) and not any("SkyriseCities" in n for n in notes):
            p = sr[0]
            notes.append(f"SkyriseCities '{p['title']}' ({p['storeys'] or '?'} storeys, {p['status']}"
                         + (f" {p['completion']}" if p["completion"] not in ("", "TBD") else "") + f", {sr[1]}): {p['url']}")
        if is_conversion(desc):
            notes.append("conversion of an existing building: already in base.glb at its measured height")
        if rate == OFFICE_M:
            notes.append("office: 4.0 m per storey")
        r["storeys_final"] = st or ""
        r["height_final_m"] = f"{ht:g}" if ht else ""
        r["height_source"] = src
        r["height_confidence"] = conf
        r["height_source_url"] = url
        r["height_note"] = " | ".join(notes)
        r["_sr"] = sr
        r["_site"] = exception_site(float(r["lat"]), float(r["lon"]))
        r["_unknown_before"] = not r["storeys_guess"] and not r["height_guess_m"]
        stats[src or "still unknown"] += 1

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS + NEW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    md = summary(rows, today, zoning[0], len(projects), bad_zone)
    OUT_MD.write_text(md, encoding="utf-8")
    print()
    print(md)
    print("height_source: " + ", ".join(f"{k} {v}" for k, v in stats.most_common()))
    print(f"wrote {OUT_CSV.relative_to(ROOT)} ({len(rows)} rows, +{len(NEW_COLUMNS)} columns) and {OUT_MD.relative_to(ROOT)}")


# ---------------------------------------------------------------------------------------------
def summary(rows, today, zsnap, n_sr, bad_zone):
    before = [r for r in rows if r["_unknown_before"]]
    src_before = Counter(r["height_source"] or "still unknown" for r in before)
    src_all = Counter(r["height_source"] or "still unknown" for r in rows)
    band = lambda r: band_of(r["storeys_final"])  # noqa: E731
    conf = lambda r: r["height_confidence"] or "—"  # noqa: E731
    unk_why = Counter()
    for r in rows:
        if not r["height_source"]:
            n = r["height_note"].split("unknown: ", 1)[-1].split(" | ")[0]
            unk_why[re.sub(r"\([^)]*\)", "(…)", n)] += 1

    def fmt(r):
        h = f"{r['height_final_m']} m" if r["height_final_m"] else "?"
        s = r["storeys_final"] or "?"
        return f"{s} st / {h} ({r['height_source'] or 'unknown'}, {r['height_confidence'] or '—'})"

    # (a) finished, likely after 2018: occupancy granted, or a SkyriseCities match that is complete
    done = []
    for r in rows:
        sr = r["_sr"]
        occ = re.search(r"occupancy granted (\d{4}-\d{2}-\d{2})", r["status_reason"])
        sr_done = sr and sr[0]["status"] in ("Complete", "Occupied")
        yr = sr[0]["completion"] if sr_done else ""
        if occ or (sr_done and (not yr.isdigit() or int(yr) >= 2019)):
            when = f"occupancy {occ.group(1)}" if occ else f"SkyriseCities {sr[0]['status']} {yr or '(year n/a)'}"
            done.append((r, when))
    # (b) exception sites
    exc = [r for r in rows if r["_site"]]
    exc.sort(key=lambda r: (list(EXCEPTION_SITES).index(r["_site"]), -int(r["storeys_final"] or 0)))

    out = [
        "# Candidate summary (unverified)",
        "",
        f"Generated {today.isoformat()} by `make candidates` (`scripts/fetch_candidates.py`, then "
        f"`scripts/fill_heights.py`). {len(rows)} sites from City of Edmonton Open Data (scope bbox, filings from "
        "2021-01-01). **Nothing here is in `proposals.csv`**; every row still needs research before it becomes a "
        "proposal. Heights: Phase 3A.5, columns `storeys_final`, `height_final_m`, `height_source`, "
        "`height_confidence`, `height_source_url`, `height_note`, plus `zone_current` / `zone_max_m`.",
        "",
        f"Of the {len(before)} rows that had no storeys or height after Phase 3A: "
        + ", ".join(f"**{v}** {k}" for k, v in src_before.most_common()) + ".",
        "",
        "## Storey band × node (storeys_final)",
        "",
        table(rows, band, BANDS, lambda r: r["node"], NODES, "Node"),
        "",
        "## Storey band × height_confidence",
        "",
        table(rows, band, BANDS, conf, CONFIDENCES, "Confidence"),
        "",
        "## height_source × height_confidence",
        "",
        table(rows, conf, CONFIDENCES, lambda r: r["height_source"] or "still unknown",
              ["dp_description", "dc_text", "skyrisecities", "zone_max", "dwellings_estimate", "still unknown"],
              "Source"),
        "",
        "- **high**: storeys stated in the DP; a DC height that SkyriseCities confirms within 2 storeys; a "
        "SkyriseCities project matched by exact street address.",
        "- **medium**: a Direct Control maximum (drafted for the site, but an envelope); a SkyriseCities project "
        "matched by name or same block.",
        "- **low**: a zone ceiling (`zone_max`, ≤ 40 m only) or a dwelling-count estimate.",
        "",
        "## Storey band × status_mapped",
        "",
        table(rows, band, BANDS, lambda r: r["status_mapped"],
              ["construction", "approved", "proposed", "stalled", "unknown"], "Status"),
        "",
        "## Still unknown: why",
        "",
        *[f"- {v} × {k}" for k, v in unk_why.most_common()],
        "",
        "## Shortlist (a): complete / occupied, likely finished after 2018 → candidate `existing` rows",
        "",
        "Occupancy granted on a building permit since 2021, or a SkyriseCities match marked Complete/Occupied "
        "with completion 2019 or later (or no year). Check each against LiDAR in the viewer: only towers that "
        "show as ground or a stump need an `existing` row.",
        "",
        "| permit_id | address | node | height | evidence |",
        "|---|---|---|---|---|",
        *[f"| {r['permit_id']} | {r['address']} | {r['node']} | {fmt(r)} | {when} |" for r, when in done],
        *(["| — | none | | | |"] if not done else []),
        "",
        "## Shortlist (b): exception-site rows with their best height",
        "",
        "| site | permit_id | address | status | height | note |",
        "|---|---|---|---|---|---|",
        *[f"| {r['_site']} | {r['permit_id']} | {r['address']} | {r['status_mapped']} | {fmt(r)} | "
          f"{(r['height_note'].split(' | ')[0])[:110].replace('|', '/')} |" for r in exc],
        "",
        "## Method and judgment calls (Phase 3A.5)",
        "",
        f"- **Zone at each site**: the latest Zoning Bylaw Map snapshot (`{ZH_ID}`, {zsnap}), point in polygon. "
        "Rezoning rows use their own polygon link.",
        "- **Direct Control** (`dc_text`): the zone link on the map (`zoningbylaw.edmonton.ca/dc-NNNNN`, `dc1-`, `dc2-`) "
        "is fetched once and every line stating a *maximum* Height or storey count is read; podium, street-wall, "
        "stepback and setback limits are skipped. Multi-tower DCs take the tallest tower. Storeys = stated storeys, "
        "else floor(height ÷ 3.1 m).",
        "- **Order changed from the brief**: SkyriseCities sits above `zone_max`. A zone maximum is a ceiling, not a "
        "height; a project's own storey count is better evidence, and scope.md's height priority (the source of "
        "truth) lists SkyriseCities but not zone maxima. `zone_max_m` is still recorded on every row.",
        f"- **High zone ceilings are not used as heights**: `zone_max` only fills a row when the ceiling is "
        f"≤ {ZONE_MAX_USE_M:g} m (it then caps the band below 13 storeys, which is what the storey floor needs). "
        "Downtown ceilings (CCA 150 m, AED 180–275 m, CMU/HDR/UW/RMU 50–70 m, UI 55 m, RL h65) fall through to the "
        "dwellings estimate or stay unknown.",
        "- **Dwellings estimate**: the brief's ranges, stored as a single number for banding (4–6 → 5, 6–12 → 9, "
        "12+ → 12), capped by the zone ceiling when that is lower. Fewer than 20 dwellings: no estimate.",
        f"- **SkyriseCities** ({n_sr} Edmonton projects, one fetch of the city page, then one fetch per project page "
        f"within {SR_RADIUS} m of a candidate, 2 s apart, cached; no forum threads). Matched by street address "
        f"(high); by a DP name in brackets matching the project title within {SR_NAME_M} m, or the same block of "
        f"the same street within {SR_BLOCK_M} m (medium). Coordinates alone never match.",
        "- **Metres ↔ storeys**: 3.1 m per residential storey, 4.0 m for office-only DPs (scope.md).",
        "- **Conversions** of existing buildings are flagged in `height_note`: they are already in `base.glb` "
        "at their measured height and are not new towers.",
        "- Rows that already had storeys in the DP text keep them (`dp_description`, high).",
        *( [f"- ! Zone-table check failed for {', '.join(bad_zone)}: the value in `ZONE_MAX` was not found on the "
            "live bylaw page; recheck before trusting those rows."] if bad_zone else []),
        "",
        "## Status rules",
        "",
        "- **construction**: a new-building / foundation / excavation / structure building permit (`24uj-dj8v`) issued "
        "within 40 m of the site (or at the same address) since the DP, no occupancy yet.",
        "- **approved**: DP approved, no building permit yet, decision less than 3 years ago.",
        "- **stalled**: DP approved more than 3 years ago and no building permit since.",
        "- **proposed**: DP application In Progress or Appealed, or a rezoning adopted with no DP on the site yet.",
        "- **unknown**: DP refused; DP status 'Other' (the portal lumps withdrawn, cancelled and expired together); or the "
        "building is complete (occupancy granted: a lead for an `existing` row if it post-dates the LiDAR).",
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    main()
