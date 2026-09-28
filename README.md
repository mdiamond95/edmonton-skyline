# edmonton-skyline

A 3D model of central Edmonton and its river valley that shows how the skyline is changing. Every
building in the base city is a matte grey block measured from LiDAR, with its footprint from
OpenStreetMap. Towers under construction, approved, proposed or stalled since 2021 are drawn on top,
coloured by status, from City of Edmonton permits and rezonings checked against SkyriseCities. The
viewer runs in the browser (iPad Safari included), and 20 fixed views are rendered headlessly into
`renders/`. `docs/scope.md` is the source of truth for scope and styling.

**Live site: <https://mdiamond95.github.io/edmonton-skyline/>** (3D viewer) ·
[renders gallery](https://mdiamond95.github.io/edmonton-skyline/renders.html)

## Legend

| Colour | Status | Meaning |
|---|---|---|
| `#E4E3DF` (matte) | existing | all base buildings, plus towers finished after the LiDAR survey at their official height |
| `#4E8FD1` (blue) | construction | under construction |
| `#A995C9` (lavender) | approved | development permit issued, not yet started |
| `#E59CA5` (pink) | proposed | rezoning or DP application filed, or publicly announced with a site |
| `#E4E3DF` at 25% opacity | stalled | approved more than 3 years ago with no permit activity, or publicly paused |

Ground `#EDECE8`, roads `#C9C8C4`, parks `#9CC08E`, water `#8FB8D8`, trees `#7FA872`.

**Envelope rule:** a proposal whose height is only a zoning ceiling (`height_confidence = low`) is drawn as
an envelope, its status colour at 50% opacity: *translucent = height ceiling, no confirmed design*.
Rows under construction are never envelopes.

## Data

**Data date: 2026-09-28** (the latest `last_checked` in `data/proposals.csv`; it is the date in every
render's filename).

- **City of Edmonton Open Data:** development permits (`2ccn-pwtu`), zoning bylaw map history
  (`67p2-r285`), building permits (`24uj-dj8v`), assessment parcels (`dm3i-bp8w`), parcel addresses
  (`ut27-nrpn`), plus the Direct Control texts on zoningbylaw.edmonton.ca.
- **SkyriseCities** Edmonton database: project heights, status and forum activity dates.
- **Base city:** Overture Maps buildings (OpenStreetMap-derived, ODbL), NRCan HRDEM 1 m LiDAR
  (DSM − DTM heights, DTM terrain; Open Government Licence – Canada), Overture land use, water and roads.
- Developer sites and press, for a few rows (each one noted in `docs/promotion-log.md`).

The full source chain, dataset IDs and height rules are in `docs/data-sources.md`. Every row in
`data/proposals.csv` carries a `source_url` and a `last_checked` date.

**Inclusion rule:** at least 8 storeys (or 25 m) anywhere in the model, or at least 6 storeys (or 20 m)
in the named nodes (Downtown Core, Ice District, Wîhkwêntôwin, Quarters/Boyle, Strathcona/Whyte,
Garneau/University, Blatchford, Exhibition Lands) and the exception sites (Ice District, The Quarters,
Station Lands, Blatchford, Rossdale, Exhibition Lands). Only applications from 2021-01-01 onward.
Alterations, use changes, parking and projects under 20 dwellings are left out at any height.

## Quarterly refresh

Everything runs headless from the `Makefile` (a Codespace or a cloud session; no GPU needed).
[`docs/refresh-prompt.md`](docs/refresh-prompt.md) is a copy-paste prompt that runs the whole thing
below end to end in a fresh Claude Code session and ends with a report for Mark.

1. `make osm-heights-check`: tall base buildings with OpenStreetMap heights vs SkyriseCities. Fix any
   mismatch with a `status: existing` or `construction` row in `data/proposals.csv` and a footprint.
2. `make candidates REFRESH=1`: new permits and rezonings from City open data, with heights, into
   `data/candidates.csv` (unverified leads).
3. Review `docs/candidates-summary.md` and then `docs/promotion-log.md` (after step 4 has written it):
   every drop and judgment call is listed there. Hand decisions go in `scripts/promote_candidates.py`
   (`CORRECTIONS`, `SKIP`); the rules are in `CLAUDE.md`.
4. `make proposals`: promotes the candidates into `data/proposals.csv`, writes footprints to
   `data/proposals.geojson` (traced footprints are kept) and runs `make build`. Update `last_checked`
   on the rows you re-verified.
5. `make build`: `dist/proposals.json` and the size check (`base.glb` < 25 MB, `dist/` < 60 MB).
6. `make contact-sheet`: check `docs/contact-sheet.png`.
7. `make renders-archive`: snapshots the outgoing `renders/` set (PNGs, thumbs, `renders.json`) to a
   GitHub Release tagged `renders-<data-date>` before the next step overwrites it in place. No-ops if
   `renders/` is empty or that tag already exists.
8. `make renders`: all 20 views into `renders/NN-slug_YYYY-MM-DD.png` (the data date), replacing the
   older ones, then `make renders-check`, which fails on any render older than the data date.
9. Commit and merge to `main`; GitHub Pages redeploys.

Base city, only when it needs updating: `make fetch` (then `make build`, which the Makefile runs for you).

## Commands

```sh
make fetch    # base city -> dist/base.glb, terrain.png/json, landuse.json (cached in data/raw/)
make build    # data/proposals.csv + .geojson -> dist/proposals.json; cameras -> dist/cameras.json; size check
make serve    # site/ on port 8000 (Codespaces forwards it as "skyline")
make cameras  # recompute the 20 views in data/cameras.json from landmark coordinates
make contact-sheet   # headless 600×800 previews of all 20 views -> docs/contact-sheet.png
make renders  # headless 2400×3200 renders (3840×2160 for 16:9 views) -> renders/NN-slug_YYYY-MM-DD.png
make renders-check   # flags renders older than the data date
make renders-archive # snapshots renders/ to a GitHub Release (renders-<data-date>) before make renders replaces it
make candidates      # City open-data leads -> data/candidates.csv + docs/candidates-summary.md (unverified)
make proposals       # candidates -> data/proposals.csv + proposals.geojson, then make build
make osm-heights-check   # OSM-tagged base buildings over 80 m vs SkyriseCities
make test-trace      # headless iPad-touch test of Trace mode -> docs/trace-mode.png
```

- `make fetch REFRESH=1` refetches everything.
- `make fetch SKIP=city,overpass` skips sources.

## Viewer

`web/index.html` (single file, three.js r166.1 from cdnjs); `web/renders.html` is the renders gallery.

- Orbit with one finger or the mouse. Pinch or scroll to zoom. Pan with two fingers, right-drag or shift-drag.
- Tap a proposal to see its details.
- Pick a view from `cameras.json`, then use **Render PNG** to download
  `NN-slug_YYYY-MM-DD.png` (2400×3200, or 3840×2160 at 16:9). Commit it to `renders/`.
- **Renders** opens the gallery of the 20 committed renders.
- **Copy camera** copies the current view in `cameras.json` format.
- A view can set `"aspect": "16:9"` in `cameras.json` (view 20 does). Picking that view switches
  the render size to 3840×2160, and `make renders` / `make contact-sheet` honour it.
- **Trace** switches to a top-down map (land use plus base footprints). Existing proposal footprints
  are outlined with their ids. Tap corners to draw, tap the first corner to close, and use **Undo** or
  **Clear** to fix mistakes. One finger pans; pinch zooms. **Copy GeoJSON** asks for the proposal id
  and copies a WGS84 `Feature` to paste into `data/proposals.geojson`. If the clipboard is blocked, it
  shows the text selected instead.
- **FPS** shows frame rate, draw calls, triangles and an estimate of GPU memory.
- On iPad, **Render PNG** saves via the download prompt. If that doesn't happen, the status bar
  offers **Save again**, **Open in new tab** (long-press → Save to Photos) and **Share…**.
- While you orbit, frames skip SSAO; a full-quality frame is drawn once the view settles.
- Needs iPadOS/Safari 16.4 or later (import maps, `DecompressionStream`).
