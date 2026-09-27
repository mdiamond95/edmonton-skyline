# Data sources

What `scripts/fetch_base.py` uses, in the order it tries them. Every run records which source
actually served each layer, and every failed attempt, in `dist/base_meta.json`.

Probed on 2026-09-27 from a Claude Code cloud session. That session's network policy blocked the
first-choice hosts, so the committed `dist/` was built from the fallbacks marked **used** below.
Running `make fetch` in the Codespace (open internet) tries the City and Overpass first.

## Reachability from the cloud session (2026-09-27)

| Host | Result |
|---|---|
| overpass-api.de (and mirror overpass.kumi.systems) | blocked (proxy 403) |
| data.edmonton.ca (and api.us.socrata.com) | blocked (proxy 403) |
| download.geofabrik.de | blocked (proxy 403) |
| cdnjs.cloudflare.com | blocked (proxy 403); the viewer was tested with the identical three r166.1 build from npm |
| overturemaps-us-west-2.s3.us-west-2.amazonaws.com | reachable |
| canelevation-dem.s3.ca-central-1.amazonaws.com (NRCan) | reachable |
| s3.amazonaws.com/elevation-tiles-prod | reachable |

## City of Edmonton Open Data (first choice, not reachable from the cloud session)

| Layer | Dataset ID | Name | Notes |
|---|---|---|---|
| Building footprints | `6n9r-ddf8` | Building Footprint | https://data.edmonton.ca/dataset/Building-Footprint/6n9r-ddf8 |
| DEM | `nppw-6ykk` | Digital Elevation Model Points 3TM (DEM) | mass points in Alberta 3TM (EPSG:3776), https://data.edmonton.ca/Elevation-Model/Digital-Elevation-Model-Points-3TM-DEM-/nppw-6ykk |
| DEM (related, unused) | `4zih-x3jw` | Digital Elevation Model Break Lines 3TM (DEM) | breaklines to go with the points |
| Contours (unused) | `m9uq-2tf9` | Contour Lines 3TM | |

**Height field on `6n9r-ddf8`: not verified.** The portal and its API were blocked, and public
search results don't list the column names. The script therefore reads the schema at run time
(`/api/views/6n9r-ddf8.json`):

- A numeric column whose name matches `height|hgt|bldg_ht|z_max|roof_height` becomes the
  **City height** tier.
- A column matching `storey|stories|floors|levels` is used like OSM levels (× 3.2 m).
- The detected names are written to `dist/base_meta.json` under `sources.city_footprint_fields`.

After the first Codespace run, check that key and update this line.

**City LiDAR:** no LiDAR DSM/point-cloud dataset was found on the portal. The LiDAR tier uses NRCan HRDEM (below).

The DEM points are gridded with linear (Delaunay) interpolation. They are used only if their
density is at least 1 point per 25 m². Anything sparser would be coarser than the 1 m LiDAR DTM
next in the chain, so the script falls through and logs why.

## Fallbacks and alternates

| Layer | Order tried | Used on 2026-09-27 |
|---|---|---|
| Footprints | City `6n9r-ddf8` → Overpass (fill, or fallback if City fails) → Geofabrik `alberta-latest.osm.pbf` + pyosmium (+ `osmium extract` if osmium-tool is installed) → **Overture Maps** `buildings/building` + `building_part` | **Overture** release 2026-09-23.1: 70,626 buildings (70,256 OpenStreetMap-derived, 370 Microsoft ML) + 281 building parts |
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

`data/proposals.csv` currently holds 5 **placeholder** rows, one per status. P002–P005 are real open
lots (checked against LiDAR and footprints) with invented heights. Their `source_url` points at the
City portal until each row is researched. P001 (Stantec Tower, existing) uses its OSM `building:part`
footprint and the published 250.8 m height.
