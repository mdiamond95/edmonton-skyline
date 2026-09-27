# edmonton-skyline

A 3D model of downtown Edmonton and its river valley, showing proposed, approved, under-construction
and stalled towers. `docs/scope.md` is the source of truth for scope and styling.

```sh
make fetch    # base city -> dist/base.glb, terrain.png/json, landuse.json (cached in data/raw/)
make build    # data/proposals.csv + .geojson -> dist/proposals.json; cameras -> dist/cameras.json; size check
make serve    # site/ on port 8000 (Codespaces forwards it as "skyline")
make cameras  # recompute the 20 views in data/cameras.json from landmark coordinates
make contact-sheet   # headless 600×800 previews of all 20 views -> docs/contact-sheet.png
make renders  # headless 2400×3200 renders -> renders/NN-slug_YYYY-MM-DD.png
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
- **FPS** shows frame rate, draw calls, triangles and an estimate of GPU memory.
- On iPad, **Render PNG** saves via the download prompt. If that doesn't happen, the status bar
  offers **Save again**, **Open in new tab** (long-press → Save to Photos) and **Share…**.
- While you orbit, frames skip SSAO; a full-quality frame is drawn once the view settles.
- Needs iPadOS/Safari 16.4 or later (import maps, `DecompressionStream`).
