#!/usr/bin/env python3
"""Headless touch test of the viewer's Trace mode (web/index.html), iPad-sized with touch input.

  make test-trace      # -> prints PASS/FAIL per step, writes docs/trace-mode.png

Drives the real page in Chromium (SwiftShader, no GPU) with touch events only: toggles Trace,
pans with one finger, pinch-zooms with two, taps four corners, closes the ring on the first
corner, undoes and re-closes, then Copy GeoJSON (answers the id prompt) and reads the clipboard.
The copied Feature is projected back to the local frame with pyproj and must land on the tapped
corners to within 5 cm. Exits 1 on any failure.
"""
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN, ROOT, to_local  # noqa: E402
from render_views import THREE_CDN, SiteHandler, chromium_path, local_three  # noqa: E402

W, H = 1180, 820   # iPad Air 11" landscape, CSS px


def main():
    from playwright.sync_api import sync_playwright
    srv = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")

    def route_three(route):
        try:
            ok = requests.head(THREE_CDN, timeout=15).ok
        except requests.RequestException:
            ok = False
        if ok:
            route.continue_()
        else:
            route.fulfill(status=200, body=local_three(), headers={
                "content-type": "application/javascript", "access-control-allow-origin": "*"})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path(), args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2, has_touch=True,
                                  is_mobile=True, user_agent=(
                                      "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                                      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"))
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=url.rstrip("/"))
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [page error] {e}"))
        page.route(THREE_CDN, route_three)
        page.on("dialog", lambda d: d.accept("P900"))
        t0 = time.time()
        page.goto(url)
        page.wait_for_function("window.__ready || window.__error", timeout=900_000, polling=500)
        if page.evaluate("window.__error"):
            sys.exit(f"viewer failed to load: {page.evaluate('window.__error')}")
        print(f"Loaded in {time.time() - t0:.0f}s")
        cdp = ctx.new_cdp_session(page)

        def touch(kind, pts):
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [
                {"x": x, "y": y, "id": i, "radiusX": 4, "radiusY": 4, "force": 1} for i, (x, y) in enumerate(pts)]})

        def drag(a, b, steps=12):
            touch("touchStart", [a])
            for k in range(1, steps + 1):
                touch("touchMove", [(a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)])
            touch("touchEnd", [])

        def state():
            return page.evaluate("(() => { const t = window.__skyline.trace; return {on: t.on, cx: t.cx, cn: t.cn, half: t.half, "
                                 "pts: t.pts, closed: t.closed, orbit: window.__skyline.orbit.enabled}; })()")

        page.tap("#tracetog")
        page.wait_for_timeout(500)
        s0 = state()
        check("Trace toggle enters top-down mode", s0["on"] and not s0["orbit"] and page.is_visible("#tracebar"))
        n_out = page.evaluate("document.querySelectorAll('#traceov polygon').length")
        n_prop = page.evaluate("window.__skyline.world.proposals.length")
        check("existing proposal footprints outlined", n_out >= n_prop, f"{n_out} outlines for {n_prop} proposals")

        drag((600, 400), (500, 300))
        s1 = state()
        k = 2 * s0["half"] / H
        check("one-finger drag pans", abs((s0["cx"] - s1["cx"]) - (-100 * k)) < 0.5 and abs((s1["cn"] - s0["cn"]) - (-100 * k)) < 0.5,
              f"moved {s1['cx'] - s0['cx']:+.1f} m E, {s1['cn'] - s0['cn']:+.1f} m N")
        # two-finger pinch out (fingers apart -> zoom in)
        touch("touchStart", [(540, 410), (640, 410)])
        for k2 in range(1, 11):
            touch("touchMove", [(540 - 10 * k2, 410), (640 + 10 * k2, 410)])
        touch("touchEnd", [])
        s2 = state()
        check("pinch zooms", s2["half"] < s1["half"] * 0.6, f"view height {2 * s1['half']:.0f} m -> {2 * s2['half']:.0f} m")
        check("pan/zoom add no corners", not s2["pts"])

        corners = [(480, 330), (700, 330), (700, 520), (480, 520)]
        for x, y in corners:
            page.touchscreen.tap(x, y)
            page.wait_for_timeout(120)
        s3 = state()
        check("taps add corners", len(s3["pts"]) == 4 and not s3["closed"], f"{len(s3['pts'])} corners")
        page.touchscreen.tap(corners[0][0] + 5, corners[0][1] - 4)
        page.wait_for_timeout(120)
        s4 = state()
        check("tap on first corner closes the ring", s4["closed"] and len(s4["pts"]) == 4)
        page.tap("#traceundo")
        s5 = state()
        page.tap("#traceundo")
        s6 = state()
        check("Undo reopens, then removes the last corner", not s5["closed"] and len(s5["pts"]) == 4 and len(s6["pts"]) == 3)
        page.touchscreen.tap(*corners[3])
        page.wait_for_timeout(120)
        page.touchscreen.tap(*corners[0])
        page.wait_for_timeout(120)
        s7 = state()
        check("re-closed after undo", s7["closed"] and len(s7["pts"]) == 4)
        page.screenshot(path=str(ROOT / "docs" / "trace-mode.png"))

        page.tap("#tracecopy")
        page.wait_for_timeout(500)
        clip = page.evaluate("navigator.clipboard.readText()")
        try:
            feat = json.loads(clip)
        except ValueError:
            feat = None
        check("Copy GeoJSON puts a Feature on the clipboard", bool(feat) and feat.get("type") == "Feature",
              (clip[:90] + "…") if clip else "clipboard empty")
        if feat:
            ring = feat["geometry"]["coordinates"][0]
            check("id prompt answered -> properties.id", feat["properties"].get("id") == "P900")
            g = shape(feat["geometry"])
            check("valid closed polygon, 4 corners, inside the bbox", g.is_valid and len(ring) == 5 and ring[0] == ring[-1]
                  and all(LON_MIN < lon < LON_MAX and LAT_MIN < lat < LAT_MAX for lon, lat in ring))
            back = [to_local(lon, lat) for lon, lat in ring[:-1]]
            err = max(min(((e - x) ** 2 + (n - y) ** 2) ** 0.5 for e, n in back) for x, y in s7["pts"])
            check("WGS84 corners round-trip to the tapped points (pyproj)", err < 0.05, f"max error {err * 100:.1f} cm")
            ccw = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(4)) > 0
            check("exterior ring counter-clockwise (RFC 7946)", ccw)
            (ROOT / "data" / "raw" / "trace-test.geojson").write_text(json.dumps(feat, indent=1) + "\n")
        page.tap("#tracedone")
        s8 = state()
        check("Done returns to the 3D view", not s8["on"] and s8["orbit"] and not page.is_visible("#tracebar"))
        browser.close()
    srv.shutdown()
    print(f"{sum(results)}/{len(results)} passed; screenshot docs/trace-mode.png")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
