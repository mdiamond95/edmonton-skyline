# Candidate summary (unverified)

Generated 2026-09-27 by `make candidates` (`scripts/fetch_candidates.py`). Source: City of Edmonton Open Data, scope bbox, filings/decisions from 2021-01-01. 326 sites after dedupe by address (235 from development permits `2ccn-pwtu`, 91 rezonings from `67p2-r285`). **Nothing here is in `proposals.csv`**; every row needs research before it becomes a proposal.

Storey band uses `storeys_guess`; rows with only a zoning height use height ÷ 3.1 m. `unknown` = no storey or height stated: Direct Control rezonings (height is in the DC text, not the map) and DPs that give only a dwelling count (see the `dwellings` column; most City DP descriptions omit storeys).

## Storey band × node

| Node | 6–7 | 8–11 | 12–19 | 20+ | < 6 | unknown | Total |
|---|---|---|---|---|---|---|---|
| Downtown Core | 0 | 2 | 0 | 2 | 2 | 15 | 21 |
| Ice District | 0 | 0 | 0 | 0 | 0 | 7 | 7 |
| Wîhkwêntôwin | 2 | 2 | 0 | 3 | 0 | 20 | 27 |
| Quarters/Boyle | 4 | 0 | 3 | 4 | 0 | 20 | 31 |
| Strathcona/Whyte | 2 | 1 | 0 | 1 | 2 | 21 | 27 |
| Garneau/University | 4 | 1 | 0 | 0 | 0 | 17 | 22 |
| Blatchford | 4 | 1 | 0 | 1 | 1 | 28 | 35 |
| Exhibition Lands | 0 | 0 | 0 | 0 | 5 | 17 | 22 |
| Other | 14 | 5 | 3 | 8 | 5 | 99 | 134 |
| **Total** | **30** | **12** | **6** | **19** | **15** | **244** | **326** |

## Storey band × status_mapped

| Status | 6–7 | 8–11 | 12–19 | 20+ | < 6 | unknown | Total |
|---|---|---|---|---|---|---|---|
| construction | 3 | 0 | 2 | 1 | 4 | 50 | 60 |
| approved | 2 | 1 | 1 | 1 | 4 | 85 | 94 |
| proposed | 24 | 11 | 3 | 17 | 6 | 70 | 131 |
| stalled | 0 | 0 | 0 | 0 | 1 | 31 | 32 |
| unknown | 1 | 0 | 0 | 0 | 0 | 8 | 9 |
| **Total** | **30** | **12** | **6** | **19** | **15** | **244** | **326** |

## Exception sites (tracked regardless of height)

| Site | 6–7 | 8–11 | 12–19 | 20+ | < 6 | unknown | Total |
|---|---|---|---|---|---|---|---|
| Ice District | 0 | 0 | 0 | 0 | 0 | 7 | 7 |
| The Quarters | 1 | 0 | 2 | 3 | 0 | 13 | 19 |
| Station Lands | 0 | 0 | 1 | 0 | 0 | 1 | 2 |
| Blatchford | 4 | 0 | 0 | 1 | 1 | 24 | 30 |
| Rossdale | 0 | 0 | 0 | 0 | 1 | 7 | 8 |
| Exhibition Lands | 0 | 0 | 0 | 0 | 5 | 19 | 24 |
| **Total** | **5** | **0** | **3** | **4** | **7** | **71** | **90** |

## Why status_mapped = unknown

- 7 × DP status 'Other' (withdrawn, cancelled or expired; portal does not say which)
- 1 × DP refused
- 1 × complete: occupancy granted … (candidate existing row)

## Status rules

- **construction**: a new-building / foundation / excavation / structure building permit (`24uj-dj8v`) issued within 40 m of the site (or at the same address) since the DP, no occupancy yet.
- **approved**: DP approved, no building permit yet, decision less than 3 years ago.
- **stalled**: DP approved more than 3 years ago and no building permit since.
- **proposed**: DP application In Progress or Appealed, or a rezoning adopted with no DP on the site yet.
- **unknown**: DP refused; DP status 'Other' (the portal lumps withdrawn, cancelled and expired together); or the building is complete (occupancy granted: a lead for an `existing` row if it post-dates the LiDAR).
