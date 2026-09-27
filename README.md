# edmonton-skyline

A 3D model of downtown Edmonton and its river valley, showing proposed, approved, under-construction
and stalled towers. `docs/scope.md` is the source of truth for scope and styling.

```sh
make fetch    # base city -> dist/base.glb, terrain.png/json, landuse.json (cached in data/raw/)
make build    # data/proposals.csv + .geojson -> dist/proposals.json; cameras -> dist/cameras.json; size check
make serve    # site/ on port 8000 (Codespaces forwards it as "skyline")
make cameras  # recompute the 20 views in data/cameras.json from landmark coordinates
make contact-sheet   # headless 600×800 previews of all 20 views -> docs/contact-sheet.png
make renders  # headless 2400×3200 renders (3840×2160 for 16:9 views) -> renders/NN-slug_YYYY-MM-DD.png
make candidates      # City open-data leads -> data/candidates.csv + docs/candidates-summary.md (unverified)
make test-trace      # headless iPad-touch test of Trace mode -> docs/trace-mode.png
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
