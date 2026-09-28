# Data sources

What `scripts/fetch_base.py` uses, in the order it tries them. Every run records which source
actually served each layer, and every failed attempt, in `dist/base_meta.json`.

The committed `dist/` uses **Overture footprints** + **HRDEM 1 m LiDAR** heights. The City footprint
layer is reachable now and was compared head-to-head in Phase 2; it lost (see
[City vs Overture](#city-vs-overture-footprints-phase-2-2026-09-27)). `make fetch` therefore defaults to
`FOOTPRINTS=osm`; `make fetch FOOTPRINTS=auto` (or `city`) puts the City layer first again.

## Reachability from the cloud sessions

| Host | Phase 1 (2026-09-27) | Phase 2 (2026-09-27) | Phase 3A.5 (2026-09-27) |
|---|---|---|---|
| data.edmonton.ca | blocked (proxy 403) | **reachable** (SODA API works) | reachable |
| overpass-api.de / overpass.kumi.systems | blocked | blocked (connection reset / 403) | not re-tested |
| download.geofabrik.de | blocked | blocked (403) | not re-tested |
| cdnjs.cloudflare.com | blocked | blocked (403); headless renders use the identical three 0.166.1 build from registry.npmjs.org | not re-tested |
| overturemaps-us-west-2 S3 | reachable | reachable | not re-tested |
| canelevation-dem S3 (NRCan) | reachable | reachable | not re-tested |
| s3.amazonaws.com/elevation-tiles-prod | reachable | not re-tested | not re-tested |
| zoningbylaw.edmonton.ca (DC texts, zone pages) | | blocked (proxy 403) | **reachable** |
| www.edmonton.ca | | | reachable |
| edmonton.ca (bare domain) | | | blocked (proxy 403); not needed, `www.` works |
| maps.edmonton.ca | | | reachable |
| coewebapps.edmonton.ca | | | proxy passes it; the site's own Cloudflare answers 403 (not a proxy setting) |
| pub-edmonton.escribemeetings.com (council agendas) | | | blocked (proxy 403); not needed yet |
| skyrisecities.com | | | **reachable** |
| www.skyrisecities.com | | | blocked (proxy 403); not needed, the bare domain serves everything |

## City of Edmonton Open Data

| Layer | Dataset ID | Name | Notes |
|---|---|---|---|
| Building footprints (tried 1st) | `jpxi-a9a5` | City of Edmonton - Rooflines (as of 2019) | 384,228 rows; `building_height`, `elevation_rooftop`, `elevation_ground`, `area`, `layer` (Building / Building_2019). Historical, not updated. |
| Building footprints (tried 2nd) | `6n9r-ddf8` | Building Footprint | Rooflines as of May 2017, historical. Columns: `_170033_id`, `the_geom`, `area` only |
| DEM | `nppw-6ykk` | Digital Elevation Model Points 3TM (DEM) | mass points in Alberta 3TM (EPSG:3776), https://data.edmonton.ca/Elevation-Model/Digital-Elevation-Model-Points-3TM-DEM-/nppw-6ykk |
| DEM (related, unused) | `4zih-x3jw` | Digital Elevation Model Break Lines 3TM (DEM) | breaklines to go with the points |
| Contours (unused) | `m9uq-2tf9` | Contour Lines 3TM | |

**Height field on `6n9r-ddf8`: none** (verified 2026-09-27 from `/api/views/6n9r-ddf8.json`: only the
id, geometry and `area`). The newer `jpxi-a9a5` does have one, `building_height` (roof − ground from the
City's 2019 LiDAR). `fetch_base.py` reads each layer's schema at run time:

- A numeric column whose name matches `height|hgt|bldg_ht|z_max|roof_height` becomes the
  **City height** tier (`building_height` on `jpxi-a9a5`).
- A column matching `storey|stories|floors|levels` is used like OSM levels (× 3.2 m).
- The detected names are written to `base_meta.json` under `sources.city_footprint_fields`.

**DEM `nppw-6ykk`** is a non-tabular (file) dataset: the SODA API answers "no row or column access to
non-tabular tables", so the terrain chain falls through to HRDEM 1 m (logged in `base_meta.json`).
If a City DEM table ever appears, points sparser than 1 per 25 m² are still skipped in favour of the 1 m DTM.

**Also on the portal, not used yet:**

- `sz3i-y5ze` / `i85g-8mg6`: 2025 LiDAR Full Feature / Bare Earth (flown 29–30 June 2025). Single
  zips of 42.7 GB and 26.7 GB, too big to pull into a Codespace or cloud session. If per-tile downloads
  (grid `v59u-75mc`, 512 m tiles) become available, these would fix post-2019 towers at the source.
- `78sz-qcfr`: 3D Buildings (225 MB file geodatabase), built from the 2019 rooflines and 2019 LiDAR.
  It has the same vintage as `jpxi-a9a5`.

## City vs Overture footprints (Phase 2, 2026-09-27)

`make compare-footprints` (`scripts/compare_footprints.py`) builds both variants in full (same HRDEM
terrain, land use and pipeline, no blending) under `data/raw/compare/` and measures the downtown core
(109 St to 97 St, river-valley rim to 105 Ave, 2.08 km²):

| | City `jpxi-a9a5` | Overture (OSM) |
|---|---|---|
| Footprints in core | 370 | 465 |
| Footprint area / union coverage of core | 694,692 m² / 33.2% | 735,893 m² / 33.4% |
| Share of its area the other set also covers | 91.9% | 91.4% |
| Median footprint | 855 m² | 746 m² |
| Buildings in core after height step | 442 | 677 |
| ≥ 50 m / ≥ 100 m | 92 / 41 | 119 / 47 |
| Tallest | 147.5 m | 198.0 m (Stantec, LiDAR; 250.8 m via proposals.csv) |
| Height tier | 100% City `building_height` | 96% LiDAR |

Area coverage is the same. The difference is shape. City rooflines are whole-lot outlines with one
height (the highest roof), so every tower-on-podium becomes a single slab at tower height. The worst case
is a 19,372 m² block at 146.8 m (Manulife Place and its podium). Scope priority puts the City height first,
which also switches off the LiDAR podium/tower split. The 2019 layer also predates Stantec Tower: its site
is one 15 m podium. Views 01 and 03 from both builds are in `docs/footprint-compare.png` (City left of each
pair). **Decision: keep Overture; `dist/` unchanged** (a fresh Overture build is byte-identical to the
committed `base.glb`).

## Fallbacks and alternates

| Layer | Order tried | Used on 2026-09-27 |
|---|---|---|
| Footprints | `--footprints auto`: City `jpxi-a9a5` → City `6n9r-ddf8`, and only if both fail the OSM chain. `--footprints osm` (the Makefile default): Overpass → Geofabrik `alberta-latest.osm.pbf` + pyosmium (+ `osmium extract` if osmium-tool is installed) → **Overture Maps** `buildings/building` + `building_part`. One set is used; City and OSM geometry are never blended. | **Overture** release 2026-09-23.1: 70,626 buildings (70,256 OpenStreetMap-derived, 370 Microsoft ML) + 281 building parts |
| OSM tags (levels/height) | Same OSM chain | Overture `num_floors` (from OSM `building:levels`) and `height` only where it came from OSM. ML-estimated heights are ignored. |
| Land use / water / roads | Overpass → Geofabrik PBF → **Overture** `base/land_use`, `base/land`, `base/water`, `transportation/segment` | **Overture** (classified from each feature's OSM `source_tags`) |
| Terrain (DEM) | City `nppw-6ykk` → **NRCan HRDEM mosaic 1 m DTM** → NRCan MRDEM 30 m → AWS Terrain Tiles (terrarium z13) | **HRDEM 1 m DTM**, 100% valid over the bbox, 611–686 m |
| LiDAR DSM (building heights) | **NRCan HRDEM mosaic 1 m DSM** | **HRDEM 1 m DSM** |

NRCan HRDEM ("CanElevation" series, Open Government Licence – Canada):

- DTM: `https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-mosaic-1m/3_4-mosaic-1m-dtm.tif`
- DSM: `https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-mosaic-1m/3_4-mosaic-1m-dsm.tif`
- Both are Cloud-Optimized GeoTIFFs in EPSG:3979, 1 m, vertical datum CGVD2013.
- Tile `3_4` was picked by testing every `hrdem-mosaic-1m/*-extent.geojson` against the scope bbox.
- The LiDAR acquisition year for Edmonton isn't in the mosaic metadata. Stantec Tower's DSM peak is
  about 194 m above ground, against 250.8 m official (which includes the spire). Towers finished
  after the survey show up as ground; those buildings fall through to OSM levels or the default height.

Overture Maps (ODbL for OSM-derived data) is read straight from `s3://overturemaps-us-west-2/release/<latest>/`
with a pyarrow bbox filter, taking the newest release listed in the bucket.

## Height assignment (docs/scope.md priority)

For each footprint, the first tier that applies wins:

1. **City height field.** Only when City footprints were fetched and a height column was detected.
2. **LiDAR DSM − DTM.** Taken over the footprint's 1 m cells:
   - Uses the median for footprints under 300 m² and the 75th percentile otherwise. Needs at least
     50% valid cells and a result of at least 2 m.
   - Small buildings are clamped to limit tree-canopy bleed: 7 m under 60 m²; 13 m for houses,
     garages and footprints under 150 m².
   - Large footprints (over 800 m²) with a clear tower-on-podium profile are split into podium and
     tower pieces, each with its own height.
3. **OSM.** An OSM-sourced `height` tag if present, else `building:levels` × 3.2 m.
4. **Default.** 8 m if the footprint is under 300 m², else 12 m.

Building parts replace their parent outline, plus any leftover part of the outline. `make fetch`
prints the coverage per tier. The viewer's **Height sources** toggle colours buildings by tier.

## Proposals

`make build` lists, for each proposal, the base buildings under its footprint (`hide_base` in
`proposals.json`: centre inside, or at least half the outline inside). The viewer hides them. For
**`status: existing`** rows this is the official-height override: the official height replaces the
measured LiDAR height for whatever base buildings sit under that footprint. Towers finished after the
LiDAR survey (they show as ground or as a stump) are entered this way. The build prints the measured
→ official change, e.g. Stantec Tower 189.1 m → 250.8 m. The lists depend on the exact `base.glb`, so
`make fetch` reruns the build; the viewer falls back to its own test if they don't match.

`data/proposals.csv` holds P001 (Stantec Tower, existing, its OSM `building:part` footprint and the
published 250.8 m) plus the candidates promoted in Phase 3B (below). The Phase 1 placeholder rows
P002–P005 are gone.

## Candidates (Phase 3A, 2026-09-27)

`make candidates` (`scripts/fetch_candidates.py`) writes `data/candidates.csv` and
`docs/candidates-summary.md`. These are unverified leads; nothing in them goes into `proposals.csv`
automatically. The raw responses are cached in `data/raw/candidates/`.

| Dataset | ID | Used for |
|---|---|---|
| Development Permits | `2ccn-pwtu` | Major/Minor DPs in the bbox, decided 2021-01-01 or later, plus all open ones (In Progress / Appealed carry no date) |
| Zoning Bylaw Map – History | `67p2-r285` | Rezonings. The portal has no rezoning-application dataset, so each current polygon zoned for ≥ 20 m (hNN ≥ 20, Direct Control, downtown special-area zones) has its history walked back to its last zone change |
| General Building Permits | `24uj-dj8v` | construction / complete status: new-building, foundation, excavation or structure permits within 40 m |
| Public Notices | `vfpx-jrew` | not used: the rezoning notices are PDFs without coordinates |

The zoning history mixes formats. Before 2025-06, DC numbers sit in the zone text with a generic
bylaw link; after that they are in the link. Height modifiers (`h23`) appear from 2025. Change
detection therefore treats a missing part as a wildcard. It also ignores the bulk-change snapshots
where many polygons "change" at once: 2024-01-08 (the Zoning Bylaw 20001 switch-over) and 2025-06-17
(a re-extract). A real rezoning dated exactly on one of those snapshots is missed. Rezoned polygons
with a DP inside them are dropped; the DP row stands for the site. `zoningbylaw.edmonton.ca` (the DC
texts with their heights) was blocked from the cloud sessions in Phase 3A (proxy 403), so DC rezonings had no
height; it is reachable from Phase 3A.5 on.

## Candidate heights (Phase 3A.5, 2026-09-27)

`scripts/fill_heights.py` (`make candidate-heights`; `make candidates` runs it after `fetch_candidates.py`)
adds `zone_current`, `zone_max_m`, `storeys_final`, `height_final_m`, `height_source`, `height_confidence`,
`height_source_url` and `height_note` to `data/candidates.csv`, and rewrites `docs/candidates-summary.md`.
Pages are fetched once, 2 s apart per host, with a User-Agent naming the project, and cached in
`data/raw/heights/` (gitignored).

| Source | What is read | height_source | Confidence |
|---|---|---|---|
| DP description (Phase 3A) | storeys / metres already in the DP text | `dp_description` | high |
| Zoning Bylaw Map `67p2-r285`, latest snapshot | current zone at the site (point in polygon) and its link | (feeds the next two rows) | |
| `zoningbylaw.edmonton.ca/dc-NNNNN` (`dc1-`, `dc2-`) | Direct Control provision: every "maximum Height … N m" / "maximum … N Storeys" phrase and height table; podium, street-wall, stepback and setback limits skipped; tallest tower wins | `dc_text` | medium (high if SkyriseCities agrees within 2 storeys) |
| `skyrisecities.com/database/cities/edmonton.14475` | one page with all Edmonton projects (coordinates, storeys, height, status, completion) | `skyrisecities` | high by street address; medium by DP project name, or the one building project inside a rezoned parcel (≤ 15,000 m²) |
| `skyrisecities.com/database/projects/<slug>.<id>` | the project's street address, for projects within 120 m of a candidate (one fetch each) | (matching only) | |
| Zoning Bylaw 20001 zone pages | zone maximum Height: the `hNN` modifier on the map, else the zone table (`ZONE_MAX` in the script, re-checked against the live page every run) | `zone_max` (≤ 40 m, or the site's own rezoning) | low |
| DP dwelling count | 20–59 → 4–6 storeys, 60–149 → 6–12, 150+ → 12+ (stored as 5 / 9 / 12, capped by the zone ceiling) | `dwellings_estimate` | low |

Metres ↔ storeys use scope.md's 3.1 m (residential) and 4.0 m (office-only DPs). The DC page for
**DC 21437** (Central McDougall / Queen Mary Park, Bylaw 20989 schedules) carries no regulation text, only
schedule maps, so its sites fall through to the later sources. SkyriseCities forum threads are never read
(robots.txt disallows the Edmonton forum).

## Proposals from candidates (Phase 3B, 2026-09-28)

`make proposals` runs `scripts/promote_candidates.py`, then `scripts/auto_footprints.py`, then `make build`.

**Promotion** applies the scope.md inclusion rule to `storeys_final` / `height_final_m` and writes
`data/proposals.csv` with four new columns: `height_confidence`, `height_source`, `footprint_source`,
`candidate_id`. Names come from the DP text ("(Falcon Tower Two)") or the SkyriseCities match, else
"<address> (<storeys>-storey <use>)"; rezonings, which have no address, take the nearest civic address from
Parcel Addresses `ut27-nrpn` (text coordinates: queried with `latitude::number between …`). Developers are
read from the matched SkyriseCities project page. Once a row is in the CSV it is kept as edited; a rerun only
adds new candidates (`make proposals REBUILD=1` regenerates them). Every drop and judgment call:
`docs/promotion-log.md`.

**Lots.** The City stopped publishing parcel polygons in November 2021 (legal and title parcel mapping moved
to AltaLIS; the note is on every parcel dataset). What is left on data.edmonton.ca:

| Dataset | ID | Used for |
|---|---|---|
| Land Parcels_Assessment Parcels (Point) | `dm3i-bp8w` | centroid + recorded area of every assessment parcel (43,927 in the bbox) |
| Zoning Bylaw Map – History, latest snapshot | `67p2-r285` | zoning polygons: follow lot lines, leave out roads and lanes; rezoned sites |
| Parcel Addresses | `ut27-nrpn` | nearest civic address for rezoning rows |

A permit's lot is the Voronoi cell of its nearest parcel centroid, clipped to the zoning polygon holding it,
scaled down to the recorded parcel area when it spills into the road, with slivers under 8 m removed. When
the permit's dwellings need more land (≈ 90 m² per dwelling over its storeys at 60% coverage), adjacent cells
in the same zoning polygon are merged in (never another row's own parcel). A rezoning takes its rezoned
polygon; a permit inside a site-specific Direct Control polygon (≤ 15,000 m², no other row in it) takes the
DC polygon. Fallbacks: hull of base buildings within 15 m, then a 30 × 30 m square. The reconstructed lots
are written to `data/raw/footprints/lots.geojson` for checking.

**Footprints** follow the brief (existing: the building's own base outline in the lot; ≤ 11 storeys: lot inset
3 m, ≤ 8 vertices; ≥ 12: 750 m² residential / 1,500 m² office, hotel or mixed-use floorplates, N on the long
axis, ≤ 70% of the lot, or the DC's own maximum tower floor plate when its text states one). `needs_trace`:
no lot found, one building on > 8,000 m², > 10% on park / water land use, or narrower than about 8 m. Per-row
detail: `docs/footprints-report.md`. Traced features (the viewer's Copy GeoJSON adds
`footprint_source: traced in viewer <date>`) are pasted into `data/proposals.geojson`; `make proposals`
keeps them and drops the auto footprint for that id.

**Viewer.** `height_confidence = low` rows are envelopes: status colour at 50% opacity with drawn edges;
they receive shadows but cast none (a ceiling is not a building) and stay out of the SSAO depth pass. Each
tower of a multi-tower footprint is seated on its own ground, and the bottom goes 1.5 m below the lowest
terrain anywhere under it. Trace mode lists the needs_trace rows with Jump to / Next.

## Placement and height corrections (Phase 3C, 2026-09-28)

The rules are in CLAUDE.md ("Height, status and placement rules"). `make proposals` applies them and
logs every change in `docs/promotion-log.md`.

| Source | Used for | Cache |
|---|---|---|
| General Building Permits `24uj-dj8v`, by address | storeys named in the permit descriptions of `construction` rows ("6 storey (41 dwelling units) apartment building"); the most storeys mentioned wins | `data/raw/heights/bp/` |
| SkyriseCities project pages | "Last Post: <date>" of the project's forum thread = the activity date for the 24-month rule (the page shows the date; the forum itself is never read) | `data/raw/heights/skyrise/` |
| Overture `base/land_use`, `land`, `water` (OSM `source_tags`) | park check: `landuse=grass` / `greenfield` / `meadow` and `natural=scrub` are vacant land, not parks | `data/raw/footprints/landuse_tags/` |
| Press, via web search only (Connect CRE, Daily Hive, CBC; these sites are blocked from the cloud sessions) | corroboration logged by hand: P035 (four 6-storey buildings), P050 (La Reina Tower, 40-45 storeys), P023 (four storeys; the City's own notice PDF on www.edmonton.ca is readable) | |

Found along the way:

- **Area height tables in DC texts.** `fill_heights.py` misses tables like "maximum building Height: i. Area A:
  140.0 m / ii. Area B: 170.0 m", and it can pick up the sunset fallback instead ("in the event that the owner
  does not obtain a Building Permit … within 10 years … the maximum Height shall be 58.0 m"). An audit of every
  `dc_text` row found this in DC 21522 (P064) and DC2-1064 (P065), both fixed by hand, and in DC 21179 (P014:
  29.9 m fallback, 34.0 m real; now replaced by the SkyriseCities 12-storey scheme). The parser itself is
  unchanged: `make candidates` needs a full refetch to test a fix.
- **False construction status.** "A building permit within 40 m" can be the neighbour's permit (P016 took the
  11-dwelling permit at 8305 99 St). The Phase 3C building-permit check matches by address.
- **OSM height tags on post-LiDAR buildings.** Where LiDAR shows ground, `fetch_base.py` falls back to OSM tags,
  which can hold a planned height: Connect Centre (ICE District Block BG) came through at 142 m, the old
  residential plan, against 56.3 m as built. It is now P104 (`existing`, official height). The other six
  base buildings over 80 m with OSM heights all check out against SkyriseCities (The Augustana 95.7 vs 96.0 m,
  Encore Tower 134.4 vs 138.0 m, Glenora Park 85.1 vs 82.0 m, Falcon Tower One, which is hidden under P010) except
  **Maclab Garneau** (87 Ave / 112 St): Under Construction, 30 storeys / 98.14 m, but not in `proposals.csv`
  (its DP predates 2021), so it shows as two grey towers at OSM's 108.4 m and 82.8 m. Fixed in Phase 4 (P105).

## Final corrections (Phase 4, 2026-09-28)

Mark's decisions are in CLAUDE.md ("Phase 4 decisions") and each change is in `docs/promotion-log.md`.

| Row | Evidence | Result |
|---|---|---|
| P016 99 Street Apartment | DP `368879276-002` (9860 83 Ave) and building permit 2025-06-19 at 8305 99 St ('99 Street Townhomes', 11 dwellings) both carry the legal description "Plan I8 Blk 75 Lots 1-2" (`2ccn-pwtu`, `24uj-dj8v`) | removed: same lot, the 27-dwelling scheme is dead |
| P072 Artists Quarters | SkyriseCities: On-Hold, 18 storeys / 77.11 m, last post 2026-06-30; site rezoned DC1 -> MU h40 f6.5 on 2024-07-08 | stalled, 18 storeys / 77.1 m (medium), traced footprint |
| P080 The Heights | rezoning DC2 (1089) -> RM h28 first on the zoning map 2026-03-30; SkyriseCities 'The Heights' last post 2026-08-07 (Pre-Construction, 43 storeys / 130.14 m) | the newer source is SkyriseCities: 43-storey scheme kept (medium) |
| P105 Maclab Garneau | SkyriseCities 'Maclab Garneau' (`maclab-garneau.35623`, 11120 86 Ave, Under Construction, 2 buildings, 30 storeys / 98.14 m, last post 2026-06-13); OSM building:part ways 1409368149 (30 floors, 108 m) and 1409368151 (20 floors, 82.5 m) via Overture 2026-09-23.1 | new `construction` row; footprint = the two OSM part outlines (hides both phantoms); part_heights 98.1 m east / 65.4 m west (98.14 x 20 / 30) |
| P057, P060, P071, P072 | traced by Mark in the viewer (Trace mode) | `footprint_source: traced in viewer 2026-09-28`; no needs_trace rows left |

SkyriseCities also has a second, empty 'Maclab Garneau' entry (`maclab-garneau.35637`, no coordinates, last post
2020); it is a duplicate and is ignored.

**Standing rule: OSM heights over 80 m.** `make osm-heights-check` (`scripts/check_osm_heights.py`) lists every base
building over 80 m whose height came from an OSM tag, whether a proposal footprint hides it, and the nearest
SkyriseCities project within 80 m. It exits 1 when a visible one has no project nearby or differs by more than 10%.
Run it before every quarterly refresh.
