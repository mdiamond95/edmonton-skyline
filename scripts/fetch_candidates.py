#!/usr/bin/env python3
"""Find candidate towers / mid-rises from City of Edmonton Open Data (unverified leads).

  make candidates      # -> data/candidates.csv + docs/candidates-summary.md (prints the summary)

Nothing here is added to data/proposals.csv: a candidate becomes a proposal only after it is
researched (height, footprint, source_url). Mark sets the storey floor from the summary tables.

Sources (data.edmonton.ca, SODA API; see docs/data-sources.md):
  2ccn-pwtu  Development Permits: Major/Minor DPs inside the scope bbox, decided 2021-01-01 or
             later, plus every application still open (In Progress / Appealed: the dataset gives
             those no date, so they are kept as current filings).
  67p2-r285  Zoning Bylaw Map - History (weekly/biweekly snapshots since 2019-05). There is no
             rezoning-application dataset on the portal, so rezonings are detected as zoning
             changes: for each current polygon in the bbox with a zone that allows >= 20 m (hNN
             >= 20, Direct Control, downtown special-area zones), its zoning history at a point
             inside it is walked back to the last change. A change on or after 2021-01-01 is a
             rezoning; the date is the first snapshot showing the new zone (adoption, +/- 2 weeks).
             The citywide switch to Zoning Bylaw 20001 (snapshot 2024-01-08) renamed every zone,
             so a change on that snapshot alone is not counted.
  24uj-dj8v  General Building Permits since 2021: new-building / foundation / excavation /
             structure permits within 40 m of a site mark it under construction (or complete
             once occupancy is granted).

Keep rule (cast wide): >= 6 storeys, or height >= 20 m, or a description naming a tower /
high-rise / mid-rise / mixed-use / apartment / residential building (or larger Multi-unit
Housing), or located on a scope exception site. Geocoded from the dataset's own coordinates
(DP point; representative point of the rezoned polygon). Deduped by address, most recent
filing kept; site status uses all filings and permits at the address.

status_mapped (docs/scope.md definitions):
  construction  a new-building permit issued at the site since 2021, no occupancy yet
  approved      DP approved, no building permit yet, decision < 3 years before today
  proposed      DP application in progress or under appeal, or a rezoning adopted with no DP
  stalled       DP approved > 3 years ago with no building-permit activity since
  unknown       refused, 'Other' (withdrawn / cancelled / expired: the portal does not say), or
                complete (occupancy granted: a candidate `existing` row, not a proposal)
status_reason says which rule fired.
"""
import argparse
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import requests
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN, RAW, ROOT  # noqa: E402

API = "https://data.edmonton.ca/resource/{}.json"
DP_ID, BP_ID, ZH_ID = "2ccn-pwtu", "24uj-dj8v", "67p2-r285"
SINCE = "2021-01-01"
CONVERSION_SNAPSHOT = "2024-01-08"   # first snapshot under Zoning Bylaw 20001
STALL_YEARS = 3
CACHE = RAW / "candidates"
OUT_CSV = DATA / "candidates.csv"
OUT_MD = ROOT / "docs" / "candidates-summary.md"
COLUMNS = ["permit_id", "source_dataset", "address", "lat", "lon", "storeys_guess", "height_guess_m",
           "status_raw", "status_mapped", "decision_date", "description", "url", "node", "status_reason", "dwellings"]
NODES = ["Downtown Core", "Ice District", "Wîhkwêntôwin", "Quarters/Boyle", "Strathcona/Whyte",
         "Garneau/University", "Blatchford", "Exhibition Lands", "Other"]
BANDS = ["6–7", "8–11", "12–19", "20+", "< 6", "unknown"]

# Node centres (lat, lon) and reach in metres; nearest by distance / reach, 'Other' beyond 1.0.
NODE_CENTRES = {
    "Downtown Core":      (53.5420, -113.4985, 800),
    "Ice District":       (53.5465, -113.4965, 350),
    "Wîhkwêntôwin":       (53.5405, -113.5180, 1000),
    "Quarters/Boyle":     (53.5455, -113.4840, 750),
    "Strathcona/Whyte":   (53.5185, -113.4950, 1000),
    "Garneau/University": (53.5235, -113.5185, 1000),
    "Blatchford":         (53.5690, -113.5160, 1300),
    "Exhibition Lands":   (53.5680, -113.4620, 1000),
}
# Scope exception sites (everything tracked regardless of height): (lat0, lat1, lon0, lon1)
EXCEPTION_SITES = {
    "Ice District":     (53.5443, 53.5490, -113.4995, -113.4925),
    "The Quarters":     (53.5410, 53.5480, -113.4915, -113.4790),
    "Station Lands":    (53.5468, 53.5500, -113.4945, -113.4880),
    "Blatchford":       (53.5600, 53.5790, -113.5320, -113.5000),
    "Rossdale":         (53.5290, 53.5385, -113.5045, -113.4870),
    "Exhibition Lands": (53.5600, 53.5760, -113.4720, -113.4480),
}

NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60})
KEYWORDS = re.compile(r"tower|high[\s-]?rise|mid[\s-]?rise|mixed[\s-]?use|apartment|residential (use )?building", re.I)
BUILD = re.compile(r"\bto (construct|develop|build|revise|amend)\b|\brevision\b", re.I)
EXCLUDE = re.compile(r"garden suite|garage|deck|sign\b|fence|shipping container|temporary|secondary suite|"
                     r"backyard hous|single detached|semi-detached|duplex|patio|hoarding|solar|telecommunication|row ?hous|"
                     r"modular|trailer|rooftop|kiosk|canopy|pedway", re.I)
ADDITION = re.compile(r"^to construct (an? )?(interior |rear |side |front )?addition", re.I)
ALTER_ONLY = re.compile(r"^to (construct )?(interior|exterior|interior and exterior) alterations|^to change the use|"
                        r"^to demolish|^to operate|^to permit|^to comply|^to install", re.I)


def get(dataset, params, cache_name, refresh=False):
    path = CACHE / cache_name
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    for attempt in range(4):
        try:
            r = requests.get(API.format(dataset), params=params, timeout=180)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def within_box(col):
    return f"within_box({col},{LAT_MAX},{LON_MIN},{LAT_MIN},{LON_MAX})"


def norm_addr(a):
    """'101, 10119 - 85 AVENUE NW' -> '10119 - 85 AVENUE NW' (drop unit / suite prefixes)."""
    a = re.sub(r"\s+", " ", (a or "").upper()).strip()
    parts = [p.strip() for p in a.split(",")]
    for p in reversed(parts):
        if re.match(r"^\d+[A-Z]? - ", p):
            return p
    return parts[-1] if parts else a


def metres(lat1, lon1, lat2, lon2):
    k = 111_320
    return math.hypot((lat1 - lat2) * k, (lon1 - lon2) * k * math.cos(math.radians(lat1)))


def node_of(lat, lon):
    lat0, lat1, lon0, lon1 = EXCEPTION_SITES["Ice District"]
    if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
        return "Ice District"
    best, score = "Other", 1.0
    for name, (clat, clon, reach) in NODE_CENTRES.items():
        s = metres(lat, lon, clat, clon) / reach
        if s < score:
            best, score = name, s
    return best


def exception_site(lat, lon):
    for name, (lat0, lat1, lon0, lon1) in EXCEPTION_SITES.items():
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return name
    return None


def parse_storeys(text):
    t = text.lower().replace("‑", "-")
    vals = []
    for m in re.finditer(r"(\d{1,3}|[a-z]+)[\s-]*(?:and a half[\s-]*)?(?:storey|story|stories|storeys)\b", t):
        w = m.group(1)
        if w.isdigit():
            vals.append(int(w))
        elif w in NUM_WORDS:
            vals.append(NUM_WORDS[w])
        else:  # 'twenty-five', 'thirty two'
            m2 = re.search(r"(twenty|thirty|forty|fifty|sixty)[\s-]+(" + "|".join(list(NUM_WORDS)[:10]) + r")[\s-]*(?:storey|story|stor)",
                           t[max(0, m.start() - 20):m.end()])
            if m2:
                vals.append(NUM_WORDS[m2.group(1)] + NUM_WORDS[m2.group(2)])
    for m in re.finditer(r"floors?\s+\d{1,2}\s*(?:-|to|through)\s*(\d{1,2})\b|\b(\d{1,2})/f\b", t):
        vals.append(int(m.group(1) or m.group(2)))
    vals = [v for v in vals if 1 <= v <= 90]
    return max(vals) if vals else None


def parse_height(text):
    t = text.lower()
    vals = []
    for m in re.finditer(r"(\d{2,3}(?:\.\d+)?)\s?m(?:etres?|eters?)?\s*(?:in height|high|tall)\b", t):
        vals.append(float(m.group(1)))
    for m in re.finditer(r"height of (?:approximately )?(\d{2,3}(?:\.\d+)?)\s?m\b", t):
        vals.append(float(m.group(1)))
    vals = [v for v in vals if 10 <= v <= 400]
    return max(vals) if vals else None


def dwellings(text):
    m = re.findall(r"(\d{2,4})\s*(?:residential\s+)?(?:dwelling|unit|suite)", text.lower())
    return max(int(v) for v in m) if m else 0


# ---------------------------------------------------------------------------------------------
def fetch_dps(refresh):
    where = (f"{within_box('geometry_point')} AND permit_type in('Major Development Permit','Minor Development Permit') "
             f"AND (permit_date >= '{SINCE}' OR (permit_date IS NULL AND status in('In Progress','Appealed')))")
    rows = get(DP_ID, {"$where": where, "$limit": 50000, "$order": "city_file_number"}, "dp.json", refresh)
    print(f"  {DP_ID} Development Permits: {len(rows)} Major/Minor DPs in bbox (decided >= {SINCE} or open)")
    return rows


def fetch_bps(refresh):
    where = (f"{within_box('geometry_point')} AND issue_date >= '{SINCE}' AND job_category != 'Home Improvement' "
             f"AND job_category != 'Single, Semi-detached & Rowhousing'")
    sel = "issue_date,job_category,job_description,building_type,work_type,units_added,address,latitude,longitude,occupancy_granted_date"
    rows = get(BP_ID, {"$select": sel, "$where": where, "$limit": 100000}, "bp.json", refresh)
    new = [r for r in rows if re.search(r"new|footing|foundation|excavation|structural frame", r.get("work_type") or "", re.I)
           and r.get("latitude")]
    print(f"  {BP_ID} Building Permits: {len(rows)} non-house permits since {SINCE}, {len(new)} new-building/foundation")
    return new


def zone_key(z, url):
    """(zone code, height modifier, DC number from the zone text, DC number from the link). The
    history mixes formats — 'DC2 (1031) (1)' with a generic bylaw_12800 link until 2025-06, 'DC2'
    with a dc2-1031 link after; 'RM' until 2025, 'RM h23' after — so keys are compared with
    `same_zone`, where a missing part matches anything, and the two DC numberings are kept apart."""
    code = (z or "").strip()
    base = code.split(" ")[0] if code else ""
    tnum = unum = None
    if base.startswith("DC"):
        m = re.search(r"\((\d{2,5})\)", code)
        tnum = m.group(1) if m and m.group(1) != "12800" else None   # 12800 = the old Zoning Bylaw itself
        m = re.search(r"/dc[12]?-(\d+)", (url or "").lower())
        unum = m.group(1) if m else None
    return base, zone_height(code), tnum, unum


def same_zone(a, b):
    return a[0] == b[0] and all(x is None or y is None or x == y for x, y in zip(a[1:], b[1:]))


def zone_height(z):
    m = re.search(r"\bh(\d+(?:\.\d+)?)", z or "")
    return float(m.group(1)) if m else None


def tall_zone(z):
    h = zone_height(z)
    if h is not None:
        return h >= 20
    return (z or "").split(" ")[0] in {"DC", "DC1", "DC2", "CCA", "CMU", "HDR", "JAMSC", "AED", "BRH", "UI", "UW", "CB"}


def fetch_rezonings(refresh):
    latest = get(ZH_ID, {"$select": "max(as_of_date)"}, "zh_latest_date.json", refresh)[0]["max_as_of_date"][:10]
    bbox_wkt = (f"POLYGON(({LON_MIN} {LAT_MIN},{LON_MAX} {LAT_MIN},{LON_MAX} {LAT_MAX},{LON_MIN} {LAT_MAX},"
                f"{LON_MIN} {LAT_MIN}))")
    polys = get(ZH_ID, {"$select": "polygon_id,zoning,url,the_geom",
                        "$where": f"as_of_date='{latest}' AND intersects(the_geom,'{bbox_wkt}')", "$limit": 50000},
                f"zh_{latest}.json", refresh)
    targets = []
    for p in polys:
        if not tall_zone(p["zoning"]):
            continue
        g = shape(p["the_geom"])
        pt = g.representative_point()
        if not (LAT_MIN <= pt.y <= LAT_MAX and LON_MIN <= pt.x <= LON_MAX):
            continue
        targets.append((p, pt, g.area * 111_320 ** 2 * math.cos(math.radians(pt.y))))
    print(f"  {ZH_ID} Zoning history: snapshot {latest}, {len(polys)} polygons in bbox, "
          f"{len(targets)} in zones allowing >= 20 m; walking each one's history…")

    def history(t):
        p, pt, _ = t
        name = f"zh_pt/{p['polygon_id']}_{pt.x:.5f}_{pt.y:.5f}.json"
        return get(ZH_ID, {"$select": "as_of_date,zoning,url",
                           "$where": f"intersects(the_geom,'POINT({pt.x:.6f} {pt.y:.6f})')",
                           "$order": "as_of_date", "$limit": 5000}, name, refresh)

    with ThreadPoolExecutor(8) as ex:
        hists = list(ex.map(history, targets))
    found = []
    for (p, pt, area), hist in zip(targets, hists):
        snaps = defaultdict(set)
        for r in hist:
            snaps[r["as_of_date"][:10]].add(zone_key(r.get("zoning"), (r.get("url") or {}).get("url")))
        cur = zone_key(p["zoning"], (p.get("url") or {}).get("url"))
        dates = sorted(snaps)
        changed = None
        for d in reversed(dates):
            if not any(same_zone(cur, k) for k in snaps[d]):
                break
            changed = d
        if not changed or changed == dates[0]:
            continue
        before = max(d for d in dates if d < changed)
        found.append((p, pt, area, changed, snaps[before], cur))
    # Bulk data events: snapshots where a large share of all polygons 'change' at once are format
    # migrations or citywide conversions (2024-01-08 Zoning Bylaw 20001; 2025-06 re-extract), not
    # rezonings. A real rezoning that happens to land on one of those snapshots is missed.
    per_date = Counter(f[3] for f in found)
    bulk = {d for d, n in per_date.items() if n >= max(50, 0.06 * len(targets))} | {CONVERSION_SNAPSHOT}
    print(f"    ignoring bulk-change snapshots: " + ", ".join(f"{d} ({per_date[d]})" for d in sorted(bulk)))
    out = []
    for p, pt, area, changed, prev, cur in found:
        if changed < SINCE or changed in bulk or area < 200:
            continue
        url = (p.get("url") or {}).get("url") or "https://data.edmonton.ca/d/67p2-r285"
        out.append({"polygon_id": p["polygon_id"], "zoning": p["zoning"],
                    "prev": sorted({k[0] + (f" ({k[2] or k[3]})" if k[2] or k[3] else "") for k in prev}),
                    "date": changed, "lat": pt.y, "lon": pt.x, "area": area, "url": url, "dc": cur[3] or cur[2]})
    print(f"    {len(out)} polygons rezoned on or after {SINCE}")
    return out


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore the data/raw/candidates/ cache")
    args = ap.parse_args()
    today = date.today()
    stall_cut = (today - timedelta(days=round(365.25 * STALL_YEARS))).isoformat()
    print("Fetching from data.edmonton.ca …")
    dps, bps, rez = fetch_dps(args.refresh), fetch_bps(args.refresh), fetch_rezonings(args.refresh)

    # --- development permits -> candidate rows --------------------------------------------------
    cands, dropped = [], Counter()
    for r in dps:
        desc = re.sub(r"\s+", " ", r.get("description_of_development") or "").strip()
        if not r.get("latitude"):
            dropped["no coordinates"] += 1
            continue
        lat, lon = float(r["latitude"]), float(r["longitude"])
        if not BUILD.search(desc) or ALTER_ONLY.search(desc) or EXCLUDE.search(desc):
            dropped["not a new building / revision"] += 1
            continue
        st, ht, du = parse_storeys(desc), parse_height(desc), dwellings(desc)
        site = exception_site(lat, lon)
        multi = re.search(r"multi-?unit hous", desc, re.I) and ((st or 0) >= 4 or du >= 20)
        tall = (st or 0) >= 6 or (ht or 0) >= 20
        small = (st is not None and st < 4) or (0 < du < 20) or (ADDITION.search(desc) and st is None)
        if not (tall or site or ((KEYWORDS.search(desc) or multi) and not small)):
            dropped["below the cast-wide filter"] += 1
            continue
        cands.append({
            "permit_id": r["city_file_number"], "source_dataset": f"{DP_ID} Development Permits",
            "address": norm_addr(r.get("address")), "lat": round(lat, 6), "lon": round(lon, 6),
            "storeys_guess": st or "", "height_guess_m": ht or "",
            "status_raw": f"{r.get('status')} ({r.get('permit_type')}{', ' + r['permit_class'] if r.get('permit_class') else ''})",
            "_status": r.get("status"), "decision_date": (r.get("permit_date") or "")[:10],
            "description": desc, "node": node_of(lat, lon), "_site": site, "dwellings": du or "",
            "url": f"https://data.edmonton.ca/resource/{DP_ID}.json?city_file_number={r['city_file_number']}",
        })
    print(f"DP candidates: {len(cands)} kept; dropped " + ", ".join(f"{v} {k}" for k, v in dropped.items()))

    # --- dedupe by address: most recent filing wins (open applications count as newest) -----------
    by_addr = defaultdict(list)
    for c in cands:
        by_addr[c["address"]].append(c)
    rows = []
    for addr, group in by_addr.items():
        group.sort(key=lambda c: (c["decision_date"] or "9999", c["permit_id"]))
        keep = dict(group[-1])
        keep["_group"] = group
        # carry the largest storey / height guess across filings at the site (revisions often omit it)
        keep["storeys_guess"] = max((c["storeys_guess"] for c in group if c["storeys_guess"]), default="")
        keep["height_guess_m"] = max((c["height_guess_m"] for c in group if c["height_guess_m"]), default="")
        keep["dwellings"] = max((c["dwellings"] for c in group if c["dwellings"]), default="")
        if len(group) > 1:
            keep["description"] += f"  [{len(group)} filings at this address: {', '.join(c['permit_id'] for c in group)}]"
        rows.append(keep)

    # --- status mapping ------------------------------------------------------------------------
    for c in rows:
        group = c.pop("_group")
        approved = sorted(g["decision_date"] for g in group if g["_status"] == "Approved" and g["decision_date"])
        near = [b for b in bps if metres(c["lat"], c["lon"], float(b["latitude"]), float(b["longitude"])) <= 40
                or norm_addr(b.get("address")) == c["address"]]
        if approved:
            near = [b for b in near if b["issue_date"][:10] >= (date.fromisoformat(approved[0]) - timedelta(days=180)).isoformat()]
        occ = sorted(b["occupancy_granted_date"][:10] for b in near if b.get("occupancy_granted_date"))
        open_bp = [b for b in near if not b.get("occupancy_granted_date")]
        s = c["_status"]
        if open_bp:
            c["status_mapped"], c["status_reason"] = "construction", f"building permit issued {min(b['issue_date'][:10] for b in open_bp)}"
        elif occ:
            c["status_mapped"], c["status_reason"] = "unknown", f"complete: occupancy granted {occ[-1]} (candidate existing row)"
        elif s in ("In Progress", "Appealed"):
            c["status_mapped"], c["status_reason"] = "proposed", f"DP application {s.lower()}"
        elif approved:
            if approved[-1] < stall_cut:
                c["status_mapped"], c["status_reason"] = "stalled", f"DP approved {approved[-1]}, no building permit since (> {STALL_YEARS} y)"
            else:
                c["status_mapped"], c["status_reason"] = "approved", f"DP approved {approved[-1]}, no building permit yet"
        elif s == "Refused":
            c["status_mapped"], c["status_reason"] = "unknown", "DP refused"
        else:
            c["status_mapped"], c["status_reason"] = "unknown", "DP status 'Other' (withdrawn, cancelled or expired; portal does not say which)"

    # --- rezonings -------------------------------------------------------------------------------
    n_rez_dup = n_rez_merge = 0
    merged = []
    for z in sorted(rez, key=lambda z: -z["area"]):
        twin = next((m for m in merged if m["zoning"] == z["zoning"] and m["date"] == z["date"]
                     and metres(m["lat"], m["lon"], z["lat"], z["lon"]) <= 250), None)
        if twin:
            twin["area"] += z["area"]
            n_rez_merge += 1
        else:
            merged.append(dict(z))
    for z in merged:
        if any(metres(z["lat"], z["lon"], c["lat"], c["lon"]) <= max(40, math.sqrt(z["area"]) / 2) for c in rows):
            n_rez_dup += 1  # a DP on the rezoned site is the later, better-described filing
            continue
        h = zone_height(z["zoning"])
        site = exception_site(z["lat"], z["lon"])
        pid = f"REZ-{z['zoning'].split(' ')[0]}{'-' + z['dc'] if z['dc'] else ''}-{z['polygon_id']}"
        rows.append({
            "permit_id": pid, "source_dataset": f"{ZH_ID} Zoning Bylaw Map - History",
            "address": f"(rezoned parcel, {z['lat']:.5f}, {z['lon']:.5f})", "lat": round(z["lat"], 6), "lon": round(z["lon"], 6),
            "storeys_guess": "", "height_guess_m": h or "", "dwellings": "",
            "status_raw": f"rezoning adopted: {'/'.join(z['prev']) or '?'} -> {z['zoning']}",
            "status_mapped": "proposed", "status_reason": "rezoning adopted, no DP found on the site",
            "decision_date": z["date"],
            "description": f"Rezoned to {z['zoning']} (parcel ≈ {z['area']:.0f} m²); first on the zoning map {z['date']}."
                           + (f" Exception site: {site}." if site else ""),
            "url": z["url"], "node": node_of(z["lat"], z["lon"]), "_site": site,
        })
    print(f"Rezonings: {len(merged) - n_rez_dup} kept as candidates ({n_rez_merge} adjoining polygons of the same "
          f"rezoning merged), {n_rez_dup} already covered by a DP on the site")

    rows.sort(key=lambda c: (NODES.index(c["node"]), -(int(c["storeys_guess"] or 0)), c["address"]))
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    md = summary(rows, today, len(dps), len(rez))
    OUT_MD.write_text(md, encoding="utf-8")
    print()
    print(md)
    print(f"wrote {OUT_CSV.relative_to(ROOT)} ({len(rows)} rows) and {OUT_MD.relative_to(ROOT)}")


def band(c):
    s = c["storeys_guess"]
    if not s:
        h = c["height_guess_m"]
        if not h:
            return "unknown"
        s = float(h) / 3.1   # scope.md residential rate, for zoning-height-only rows
    s = int(round(float(s)))
    return "20+" if s >= 20 else "12–19" if s >= 12 else "8–11" if s >= 8 else "6–7" if s >= 6 else "< 6"


def table(rows, colkey, cols, rowkey, rowvals, title_col):
    t = defaultdict(Counter)
    for r in rows:
        t[rowkey(r)][colkey(r)] += 1
    lines = [f"| {title_col} | " + " | ".join(cols) + " | Total |", "|---" * (len(cols) + 2) + "|"]
    for rv in rowvals:
        vals = [t[rv][c] for c in cols]
        lines.append(f"| {rv} | " + " | ".join(str(v) for v in vals) + f" | {sum(vals)} |")
    tot = [sum(t[rv][c] for rv in rowvals) for c in cols]
    lines.append("| **Total** | " + " | ".join(f"**{v}**" for v in tot) + f" | **{sum(tot)}** |")
    return "\n".join(lines)


def summary(rows, today, n_dp, n_rez):
    statuses = ["construction", "approved", "proposed", "stalled", "unknown"]
    reasons = Counter(re.sub(r"\d{4}-\d{2}-\d{2}", "…", r["status_reason"]) for r in rows if r["status_mapped"] == "unknown")
    src = Counter(r["source_dataset"].split(" ")[0] for r in rows)
    out = [
        "# Candidate summary (unverified)",
        "",
        f"Generated {today.isoformat()} by `make candidates` (`scripts/fetch_candidates.py`). Source: City of Edmonton "
        f"Open Data, scope bbox, filings/decisions from {SINCE}. {len(rows)} sites after dedupe by address "
        f"({src.get(DP_ID, 0)} from development permits `{DP_ID}`, {src.get(ZH_ID, 0)} rezonings from `{ZH_ID}`). "
        "**Nothing here is in `proposals.csv`**; every row needs research before it becomes a proposal.",
        "",
        "Storey band uses `storeys_guess`; rows with only a zoning height use height ÷ 3.1 m. `unknown` = no storey or "
        "height stated: Direct Control rezonings (height is in the DC text, not the map) and DPs that give only a "
        "dwelling count (see the `dwellings` column; most City DP descriptions omit storeys).",
        "",
        "## Storey band × node",
        "",
        table(rows, band, BANDS, lambda r: r["node"], NODES, "Node"),
        "",
        "## Storey band × status_mapped",
        "",
        table(rows, band, BANDS, lambda r: r["status_mapped"], statuses, "Status"),
        "",
        "## Exception sites (tracked regardless of height)",
        "",
        table(rows, band, BANDS, lambda r: r.get("_site") or "—", list(EXCEPTION_SITES), "Site"),
        "",
        "## Why status_mapped = unknown",
        "",
        *[f"- {v} × {k}" for k, v in reasons.most_common()],
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
