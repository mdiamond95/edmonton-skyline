# Quarterly refresh prompt

Opus, high

Paste everything below this line into a new Claude Code session (Codespace or cloud) on
`mdiamond95/edmonton-skyline`, on a fresh branch off `main`. It runs the quarterly refresh
end to end with no manual steps outside the session — read `CLAUDE.md` and `docs/scope.md`
first, then follow the CLAUDE.md height/status/placement rules (Phase 3C, Phase 4) exactly;
do not invent new judgment calls where those rules already decide the row.

---

Run the quarterly refresh for edmonton-skyline, in this order, fixing forward on any failure
rather than skipping a step:

1. `make osm-heights-check`. If it exits 1, each flagged building is a real mismatch (OSM
   height vs SkyriseCities) that CLAUDE.md's standing rule requires fixing with a
   `status: existing` or `construction` row in `data/proposals.csv` plus a footprint over the
   building (never by editing `fetch_base.py` or `base.glb`). Rerun until it's clean.
2. `make candidates REFRESH=1`. Skim `docs/candidates-summary.md` for anything obviously
   out of scope (wrong node, pre-2021, under the inclusion rule) before it reaches proposals.
3. `make proposals`. This promotes candidates into `data/proposals.csv` +
   `data/proposals.geojson` and runs `make build`; it also (re)writes
   `docs/promotion-log.md` and `docs/footprints-report.md`.
4. Review `docs/promotion-log.md` against the CLAUDE.md rules (Phase 3C "Height, status and
   placement rules" and "Phase 4 decisions"):
   - For every row the log dropped or flagged, check whether a CLAUDE.md rule mechanically
     decides it — the SkyriseCities-forum-activity test, the newest-dated-source test
     (`newest_source()`), the construction-confidence floor, the multi-tower `part_heights`
     rule, the park-placement / OSM-tag check, the dead-DP-superseded-by-a-smaller-building
     rule, etc. Where a rule decides it, apply the fix in `scripts/promote_candidates.py`
     (`CORRECTIONS`, `SKIP`, `MANUAL_NOTES`) exactly as CLAUDE.md states it, and rerun
     `make proposals` (add `REBUILD=1` if a row already in `proposals.csv` needs to be
     regenerated rather than kept as edited).
   - Anything left over — genuinely new judgment calls the existing rules don't cover, two
     sources that disagree with no newest-wins tiebreak, a row where the right call depends on
     information only Mark has — do **not** guess. Leave the row as the script's best
     mechanical default and add it verbatim to the REPORT FOR MARK below.
   - Update `last_checked` on every row you re-verified against its source this pass.
5. `make build`: confirms `dist/proposals.json` regenerates clean and the size budgets
   (`dist/base.glb` < 25 MB, `dist/` < 60 MB) still pass.
6. `make renders-archive`: snapshots the outgoing `renders/` set to a GitHub Release
   (`renders-<old-data-date>`) before the next step overwrites it. Confirm it printed a
   release URL (or "already exists" / "nothing to archive") — don't proceed past a hard
   failure here without finding out why `gh` couldn't authenticate or reach the repo.
7. `make renders`: re-renders all 20 views at full spec, replacing the old ones, then runs
   `make renders-check` itself.
8. `make renders-check`: run it again standalone and confirm every render matches the new
   data date.
9. `make contact-sheet`: regenerate `docs/contact-sheet.png` and actually look at it (Read
   the image). Check for the same kind of framing problems Phase 5 fixed — a proposal cut off
   at a frame edge, a construction tower looming in the immediate foreground, sky over the
   guideline for that view's type. Nudge `scripts/set_cameras.py` and re-render just the
   affected view(s) if something looks wrong; don't leave a bad frame for the next refresh.
10. Commit everything and push to your branch. Open a PR titled `Refresh <data-date>` (the
    new max `last_checked` in `data/proposals.csv`), following any PR template in the repo.

Then reply with a message headed **REPORT FOR MARK** containing exactly these five sections:

1. **New/changed/removed rows** — every proposal id added, materially changed (status,
   height, storeys), or removed this pass, one line each with the reason.
2. **needs_trace list** — every proposal currently at `footprint_source: needs_trace`
   (`docs/footprints-report.md` has the full table with why each one needs tracing); these
   need Mark in the viewer's Trace mode, not a guessed footprint.
3. **Rows needing a decision** — the leftover list from step 4: anything no CLAUDE.md rule
   resolved, with enough context (both sources, the conflict) for Mark to decide in one read.
4. **Ready to merge** — yes/no. If no, say exactly what's blocking (a failing check, an
   unresolved row that affects a render, a size-budget miss) rather than leaving it implicit.
5. **PR link.**
