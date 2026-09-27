# edmonton-skyline

A 3D model of downtown Edmonton and its river valley, showing proposed, approved, under-construction
and stalled towers. `docs/scope.md` is the source of truth for scope and styling.

```sh
make fetch    # base city -> dist/base.glb, terrain.png/json, landuse.json (cached in data/raw/)
make build    # data/proposals.csv + .geojson -> dist/proposals.json; cameras -> dist/cameras.json; size check
make serve    # site/ on port 8000 (Codespaces forwards it as "skyline")
```

- `make fetch REFRESH=1` refetches everything.
- `make fetch SKIP=city,overpass` skips sources.
- The source chain, dataset IDs and height rules are in `docs/data-sources.md`.

Viewer (`web/index.html`, single file, three.js r166.1 from cdnjs):

- Orbit with one finger or the mouse. Pinch or scroll to zoom. Pan with two fingers, right-drag or shift-drag.
- Tap a proposal to see its details.
- Pick a view from `cameras.json`, then use **Render PNG** to download
  `NN-slug_YYYY-MM-DD.png` (2400×3200, or 3840×2160 at 16:9). Commit it to `renders/`.
- **Copy camera** copies the current view in `cameras.json` format.
- Needs iPadOS/Safari 16.4 or later (import maps, `DecompressionStream`).
