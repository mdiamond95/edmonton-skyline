# Data sources

What `scripts/fetch_base.py` uses, in the order it tries them. Every run records which source
actually served each layer, and every failed attempt, in `dist/base_meta.json`.

The committed `dist/` uses **Overture footprints** + **HRDEM 1 m LiDAR** heights. The City footprint
layer is reachable now and was compared head-to-head in Phase 2; it lost (see
[City vs Overture](#city-vs-overture-footprints-phase-2-2026-09-27)). `make fetch` therefore defaults to
`FOOTPRINTS=osm`; `make fetch FOOTPRINTS=auto` (or `city`) puts the City layer first again.

## Reachability from the cloud sessions

| Host | Phase 1 (2026-09-27) | Phase 2 (2026-09-27) |
|---|---|---|
| data.edmonton.ca | blocked (proxy 403) | **reachable** (SODA API works) |
| overpass-api.de / overpass.kumi.systems | blocked | blocked (connection reset / 403) |
| download.geofabrik.de | blocked | blocked (403) |
| cdnjs.cloudflare.com | blocked | blocked (403); headless renders use the identical three 0.166.1 build from registry.npmjs.org |
| overturemaps-us-west-2 S3 | reachable | reachable |
| canelevation-dem S3 (NRCan) | reachable | reachable |
| s3.amazonaws.com/elevation-tiles-prod | reachable | not re-tested |

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

`data/proposals.csv` currently holds 5 **placeholder** rows, one per status. P002–P005 are real open
lots (checked against LiDAR and footprints) with invented heights. Their `source_url` points at the
City portal until each row is researched. P001 (Stantec Tower, existing) uses its OSM `building:part`
footprint and the published 250.8 m height.
