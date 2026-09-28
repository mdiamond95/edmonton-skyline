#!/usr/bin/env python3
"""Snapshot the current renders/ set to a GitHub Release before a full re-render replaces it.

  make renders-archive    # tag renders-<data-date>, upload renders/*.png + thumbs/*.jpg + renders.json

Run before `make renders` in the quarterly refresh (see README) so the outgoing PNGs stay reachable
after `make renders` overwrites them in place. Needs `gh` authenticated (GITHUB_TOKEN / GH_TOKEN,
e.g. Codespaces' default) against this repo's GitHub remote. No-ops if renders/ has no PNGs yet, or
if the release tag already exists (safe to rerun).

The existence check uses `gh api` (REST) rather than `gh release view`/`list`: those use GitHub's
GraphQL API, which Claude Code cloud sessions cannot reach (a sandbox restriction, not an auth
problem — `gh api` and `gh release create` are plain REST and work fine there).
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from renders_check import current_data_date  # noqa: E402

RENDERS = ROOT / "renders"


def repo_slug():
    url = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()
    m = re.search(r"[:/]([^/]+/[^/]+?)(\.git)?$", url)
    if not m:
        sys.exit(f"renders-archive: can't parse owner/repo from remote.origin.url {url!r}")
    return m.group(1)


def main():
    pngs = sorted(RENDERS.glob("*.png"))
    if not pngs:
        print("renders-archive: renders/*.png is empty, nothing to archive")
        return

    data_date = current_data_date()
    if not data_date:
        sys.exit("renders-archive: no last_checked dates in data/proposals.csv; cannot name the release")
    tag = f"renders-{data_date}"
    repo = repo_slug()

    if subprocess.run(["gh", "api", f"repos/{repo}/releases/tags/{tag}"], capture_output=True).returncode == 0:
        print(f"renders-archive: release {tag} already exists, skipping (nothing to supersede yet)")
        return

    thumbs = sorted((RENDERS / "thumbs").glob("*.jpg"))
    renders_json = RENDERS / "renders.json"
    assets = pngs + thumbs + ([renders_json] if renders_json.exists() else [])
    print(f"renders-archive: creating release {tag} with {len(assets)} assets ({len(pngs)} renders, "
          f"{len(thumbs)} thumbs{', renders.json' if renders_json.exists() else ''})")
    subprocess.run(["gh", "release", "create", tag, *(str(p) for p in assets),
                     "--repo", repo, "--title", f"Renders {data_date}",
                     "--notes", f"Snapshot of renders/ (data date {data_date}) before the next "
                                "`make renders` overwrites it in place."],
                    check=True)


if __name__ == "__main__":
    main()
