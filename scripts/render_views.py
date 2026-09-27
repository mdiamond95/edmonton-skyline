#!/usr/bin/env python3
"""Render camera views headlessly through the real viewer (web/index.html).

Runs Chromium via Playwright with SwiftShader (CPU WebGL), so no GPU is needed. The page is
served from a throwaway local HTTP server with the same layout as `make site`
(web/* at the root, dist/ and renders/ beside it). Each view is rendered by the viewer's own
Render PNG code path (tiling, supersampling, SSAO, name chip), so previews match what the
iPad produces.

  make contact-sheet     # 600x800 previews of all 20 views -> docs/contact-sheet/ + docs/contact-sheet.png
  make renders           # 2400x3200 renders -> renders/NN-slug_YYYY-MM-DD.png (data date)

  scripts/render_views.py --views 01,03 --size 600x800 --out /tmp/x --dist data/raw/compare/city

three.js is loaded from cdnjs like on the iPad. If cdnjs is unreachable (some sandboxes), the
identical three@0.166.1 build is fetched from the npm registry into data/raw/npm/ and served
in its place.

Setup (once per Codespace): pip install playwright && python -m playwright install chromium
"""
import argparse
import base64
import functools
import glob
import io
import json
import os
import sys
import tarfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DIST, RAW, ROOT  # noqa: E402

THREE_VERSION = "0.166.1"
THREE_CDN = f"https://cdnjs.cloudflare.com/ajax/libs/three.js/{THREE_VERSION}/three.module.min.js"
THREE_NPM = f"https://registry.npmjs.org/three/-/three-{THREE_VERSION}.tgz"


# Runs in the page: is each proposal seated on the terrain, is its top at ground + height_m, and
# does any base building still poke through it (a vertex of a non-hidden building inside the
# footprint that is higher than the proposal's top)?
PROPOSAL_CHECK = """() => {
  const { world } = window.__skyline;
  const inPoly = (x, y, ring) => { let c = false; for (let i = 0, j = ring.length - 2; i < ring.length; j = i, i += 2) {
    const xi = ring[i], yi = ring[i + 1], xj = ring[j], yj = ring[j + 1];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; };
  const hidden = new Set(world.proposals.flatMap((p) => p.hide_base || []));
  return world.propMeshes.map((m) => {
    const p = m.userData.proposal, ring = p.footprint[0][0];
    m.geometry.computeBoundingBox();
    const bb = m.geometry.boundingBox;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < ring.length; i += 2) { x0 = Math.min(x0, ring[i]); x1 = Math.max(x1, ring[i]); y0 = Math.min(y0, ring[i + 1]); y1 = Math.max(y1, ring[i + 1]); }
    let gmin = Infinity, gmax = -Infinity;
    for (let x = x0; x <= x1; x += 1) for (let y = y0; y <= y1; y += 1) if (inPoly(x, y, ring)) {
      const g = world.heights ? window.__skyline.heightAt(x, -y) : 0; gmin = Math.min(gmin, g); gmax = Math.max(gmax, g); }
    const ground = []; for (let i = 0; i < ring.length; i += 2) ground.push(window.__skyline.heightAt(ring[i], -ring[i + 1]));
    ground.sort((a, b) => a - b);
    const T = world.table; let visible = 0;
    for (let b = 0; b < T.length / 4; b++) {
      if (hidden.has(b)) continue;
      const pos = world.buildings[T[b * 4]].geometry.attributes.position.array;
      for (let v = T[b * 4 + 1]; v < T[b * 4 + 1] + T[b * 4 + 2]; v++) {
        const x = pos[v * 3], y = -pos[v * 3 + 2];
        if (x < x0 + 0.5 || x > x1 - 0.5 || y < y0 + 0.5 || y > y1 - 0.5) continue;
        if (inPoly(x, y, ring) && pos[v * 3 + 1] > window.__skyline.heightAt(x, -y) + 1 && pos[v * 3 + 1] > bb.max.y) { visible++; break; }
      }
    }
    return { id: p.id, status: p.status, height_m: p.height_m, base: bb.min.y, top: bb.max.y,
             ground_min: gmin, ground_max: gmax, ground_ref: ground[Math.floor(ground.length / 2)],
             hidden: (p.hide_base || []).length, poking_through: visible };
  });
}"""


def local_three():
    path = RAW / "npm" / f"three-{THREE_VERSION}.module.min.js"
    if not path.exists():
        r = requests.get(THREE_NPM, timeout=120)
        r.raise_for_status()
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tf:
            data = tf.extractfile("package/build/three.module.min.js").read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.read_bytes()


class SiteHandler(SimpleHTTPRequestHandler):
    """Serve web/ at /, and dist/ and renders/ from configurable directories."""
    dist_dir = DIST

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/dist/"):
            return str(Path(self.dist_dir) / path[len("/dist/"):])
        if path.startswith("/renders/"):
            return str(ROOT / "renders" / path[len("/renders/"):])
        if path in ("", "/"):
            path = "/index.html"
        return str(ROOT / "web" / path.lstrip("/"))

    def log_message(self, *a):
        pass


def view_size(cam, W, H):
    """cameras.json "aspect": "16:9" -> landscape at 1.2x the portrait height (2400x3200 -> 3840x2160)."""
    if cam.get("aspect") == "16:9":
        w = round(max(W, H) * 1.2)
        return w, round(w * 9 / 16)
    return W, H


def chromium_path():
    for p in [os.environ.get("SKYLINE_CHROMIUM"), "/opt/pw-browsers/chromium",
              *sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))]:
        if p and os.path.exists(p):
            return p
    return None  # let Playwright use its own download


def sky_fraction(png_bytes, bg=(0xE9, 0xEC, 0xEE)):
    """Share of the frame showing background (sky or beyond the model edge)."""
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB")).astype(int)
    return float((np.abs(a - np.array(bg)).max(-1) <= 3).mean())


def compact_png(png_bytes):
    """256-colour palette PNG (about a third of the size) for the previews kept in docs/."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    q = im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    o = io.BytesIO()
    q.save(o, "PNG", optimize=True)
    return o.getvalue()


def contact_sheet(items, path, cols=5, thumb=(300, 400), compact=False):
    from PIL import Image, ImageDraw, ImageFont
    rows = (len(items) + cols - 1) // cols
    cap = 34
    sheet = Image.new("RGB", (cols * thumb[0], rows * (thumb[1] + cap)), "#ffffff")
    draw = ImageDraw.Draw(sheet)
    font = None
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            font = ImageFont.truetype(f, 15)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    for k, (cid, name, png) in enumerate(items):
        im = Image.open(png).convert("RGB")
        x, y = (k % cols) * thumb[0], (k // cols) * (thumb[1] + cap)
        if im.width * thumb[1] > im.height * thumb[0]:   # landscape view: fit the width, centre vertically
            tw, th = thumb[0], round(thumb[0] * im.height / im.width)
            sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y + (thumb[1] - th) // 2))
        else:
            sheet.paste(im.resize(thumb, Image.LANCZOS), (x, y))
        label = f"{cid}  {name}"
        while draw.textlength(label, font=font) > thumb[0] - 12 and len(label) > 8:
            label = label[:-2]
        if label != f"{cid}  {name}":
            label = label.rstrip() + "…"
        draw.text((x + 6, y + thumb[1] + 8), label, fill="#1d1d1f", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        o = io.BytesIO()
        sheet.save(o, "PNG")
        path.write_bytes(compact_png(o.getvalue()))
    else:
        sheet.save(path, optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--views", default="all", help="comma list of camera ids, or 'all'")
    ap.add_argument("--size", default="600x800", help="WxH of each PNG")
    ap.add_argument("--out", default=str(ROOT / "docs" / "contact-sheet"))
    ap.add_argument("--dist", default=str(DIST), help="directory served as ./dist/")
    ap.add_argument("--dated", action="store_true", help="name files NN-slug_YYYY-MM-DD.png (renders/ convention)")
    ap.add_argument("--sheet", default=None, help="also write a contact-sheet grid PNG here")
    ap.add_argument("--compact", action="store_true", help="save 256-colour PNGs (docs previews, not renders/)")
    ap.add_argument("--timeout", type=float, default=900, help="seconds to wait for the scene to load")
    args = ap.parse_args()
    W, H = (int(v) for v in args.size.lower().split("x"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    handler = functools.partial(SiteHandler)
    SiteHandler.dist_dir = Path(args.dist).resolve()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    cdn_ok = {}

    def route_three(route):
        if "ok" not in cdn_ok:
            try:
                cdn_ok["ok"] = requests.head(THREE_CDN, timeout=15).ok
            except requests.RequestException:
                cdn_ok["ok"] = False
            if not cdn_ok["ok"]:
                print("  cdnjs unreachable; serving three.js from the npm registry copy")
        if cdn_ok["ok"]:
            route.continue_()
        else:
            route.fulfill(status=200, body=local_three(), headers={
                "content-type": "application/javascript", "access-control-allow-origin": "*"})

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path(), args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        page.on("console", lambda m: m.type in ("error", "warning") and print(f"  [page {m.type}] {m.text}"))
        page.on("pageerror", lambda e: print(f"  [page error] {e}"))
        page.route(THREE_CDN, route_three)
        t0 = time.time()
        page.goto(url)
        page.wait_for_function("window.__ready || window.__error", timeout=args.timeout * 1000, polling=500)
        err = page.evaluate("window.__error")
        if err:
            sys.exit(f"viewer failed to load: {err}")
        info = page.evaluate("""() => ({ cams: window.__skyline.world.cameras, date: window.__skyline.world.dataDate,
                                         gpu: window.__skyline.gpuMemoryMB(), status: document.getElementById('status').textContent })""")
        print(f"Loaded in {time.time() - t0:.0f}s: {info['status']}; GPU estimate {info['gpu']:.0f} MB")
        checks = page.evaluate(PROPOSAL_CHECK)
        print("Proposals (base = mesh bottom, terrain = min/max ground under the footprint):")
        for k in checks:
            k["ok"] = (k["base"] <= k["ground_min"] + 0.01 and not k["poking_through"]
                       and abs(k["top"] - k["ground_ref"] - k["height_m"]) < 0.05)
            print(f"  {k['id']} {k['status']:<12} base {k['base']:7.1f}  terrain {k['ground_min']:7.1f}..{k['ground_max']:7.1f}"
                  f"  top-ref {k['top'] - k['ground_ref']:6.1f} m (height {k['height_m']})  hides {k['hidden']}"
                  f"  base poking through: {k['poking_through']}  {'OK' if k['ok'] else 'CHECK'}")
        cams = [c for c in info["cams"] if c.get("position")]
        if args.views != "all":
            want = {v.strip().zfill(2) for v in args.views.split(",")}
            cams = [c for c in cams if c["id"] in want]
        for c in cams:
            t1 = time.time()
            w, h = view_size(c, W, H)
            data_url = page.evaluate("([id, w, h]) => window.__skyline.renderView(id, w, h)", [c["id"], w, h])
            png = base64.b64decode(data_url.split(",", 1)[1])
            name = f"{c['id']}-{c['slug']}" + (f"_{info['date']}" if args.dated else "") + ".png"
            path = out / name
            path.write_bytes(compact_png(png) if args.compact else png)
            sky = sky_fraction(png)
            results.append({"id": c["id"], "name": c["name"], "file": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
                            "sky_pct": round(100 * sky, 1)})
            print(f"  {c['id']} {c['name']:<40} {w}x{h} {time.time() - t1:5.1f}s  background/sky {100 * sky:4.1f}%  -> {path}")
        browser.close()
    srv.shutdown()
    if args.sheet:
        contact_sheet([(r["id"], r["name"], ROOT / r["file"] if not Path(r["file"]).is_absolute() else r["file"])
                       for r in results], Path(args.sheet), compact=args.compact)
        print(f"Contact sheet -> {args.sheet}")
    (out / "render_log.json").write_text(json.dumps({"size": [W, H], "gpu_estimate_mb": round(info["gpu"]),
                                                    "proposals": checks, "views": results}, indent=1) + "\n")


if __name__ == "__main__":
    main()
