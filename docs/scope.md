# Edmonton Future Skyline — Scope & Decisions

## Bounding box (WGS84)
lat 53.505–53.585, lon -113.560 to -113.430. Projection for geometry: UTM zone 12N (EPSG:26912), origin at the bbox centre.

## Status legend (exact hex)
- existing:      #E4E3DF  (matte, all base buildings)
- construction:  #4E8FD1  (blue)
- approved:      #A995C9  (lavender) — development permit issued, not yet started
- proposed:      #E59CA5  (pink) — rezoning or DP application filed, or publicly announced with a site
- stalled:       #E4E3DF at 25% opacity — approved > 3 years with no permit activity, or publicly paused
Ground #EDECE8, roads #C9C8C4, parks/landuse green #9CC08E, water #8FB8D8, trees #7FA872.

## Inclusion rule for proposals
≥ 8 storeys (or ≥ 25 m) anywhere in the bbox; ≥ 6 storeys (or ≥ 20 m) within the named nodes (Downtown Core, Ice District, Wîhkwêntôwin, Quarters/Boyle, Strathcona/Whyte, Garneau/University, Blatchford, Exhibition Lands) and on exception sites. Alterations, use changes, parking and projects under 20 dwellings are excluded regardless of height.
Exception sites: Ice District, The Quarters, Station Lands, Blatchford, Rossdale, Exhibition Lands.
Applications considered from 2021-01-01 onward. Height source priority: DP document > developer site > SkyriseCities > storeys × 3.1 m (residential) or × 4.0 m (office).

## Base-building height priority
City open data height field > LiDAR DSM−DEM > OSM building:levels × 3.2 > default (8 m if footprint < 300 m², else 12 m).

## Data sources (in trust order)
1. City of Edmonton Open Data: development permits, rezoning/land-use applications, building footprints, DEM/LiDAR
2. Council and agency agendas (rezonings, Ice District/Blatchford reports)
3. SkyriseCities Edmonton database and threads
4. Developer sites and press
Every proposals.csv row must carry a source_url and last_checked date.

## Render spec
- Perspective camera, 35° vertical FOV, portrait 3:4, PNG at 2400×3200 (tile if Safari canvas limit hit); optional 16:9 at 3840×2160
- Lighting: directional key from azimuth 200° (SSW), elevation 42°, soft PCF shadows; hemisphere fill; SSAO on; light exponential fog starting at 3 km
- Materials: MeshStandardMaterial, roughness 0.9, metalness 0
- Bottom-left name chip: dark rounded rect, white bold sans, 4% of frame height
- Low-poly instanced trees on park/landuse polygons, cap 20,000 instances
- height_confidence = low renders as an envelope: status colour at 50% opacity. Legend notes 'translucent = height ceiling, no confirmed design'.
- Base buildings under a proposal footprint are hidden
- Output filename: renders/NN-slug_YYYY-MM-DD.png (date = data date, see CLAUDE.md)

## The 20 views (cameras.json names)
01 City Centre (from SE over Ice District, mirrors reference)
02 Ice District from the south bank
03 Downtown from the west (Wîhkwêntôwin)
04 Downtown from the north (Blatchford)
05 Wîhkwêntôwin / Oliver
06 The Quarters & Boyle Street
07 Rossdale & the river bend
08 Station Lands & MacEwan
09 Warehouse District / 104 Street
10 Jasper Avenue corridor
11 Legislature grounds
12 Old Strathcona / Whyte Avenue
13 Garneau / 109 Street
14 Strathcona from the east (Bonnie Doon side)
15 Blatchford
16 From the High Level Bridge
17 From Ada Boulevard (east)
18 Exhibition Lands & Commonwealth
19 Whyte to Downtown (long axis, looking north)
20 Full extent aerial

## Tech constraints
Everything must run from GitHub Codespaces in Safari on iPadOS: no desktop apps, no GPU assumed server-side, page must render on iPad Safari with touch orbit. three.js pinned from cdnjs, single-file web/index.html, assets as one base.glb + terrain heightmap + proposals.json. dist/base.glb < 25 MB, dist/ total < 60 MB.

## Maintenance
Quarterly: rerun fetch_candidates.py, diff against proposals.csv, update statuses, re-render all 20 views into renders/ with the data date in the filename.
