# Candidate summary (unverified)

Generated 2026-09-27 by `make candidates` (`scripts/fetch_candidates.py`, then `scripts/fill_heights.py`). 326 sites from City of Edmonton Open Data (scope bbox, filings from 2021-01-01). **Nothing here is in `proposals.csv`**; every row still needs research before it becomes a proposal. Heights: Phase 3A.5, columns `storeys_final`, `height_final_m`, `height_source`, `height_confidence`, `height_source_url`, `height_note`, plus `zone_current` / `zone_max_m`.

Of the 244 rows that had no storeys or height after Phase 3A: **141** zone_max, **62** dc_text, **17** still unknown, **13** dwellings_estimate, **10** skyrisecities, **1** dp_description.

## Storey band × node (storeys_final)

| Node | < 6 | 6–7 | 8–11 | 12–19 | 20+ | still unknown | Total |
|---|---|---|---|---|---|---|---|
| Downtown Core | 4 | 1 | 8 | 4 | 3 | 1 | 21 |
| Ice District | 1 | 0 | 0 | 1 | 2 | 3 | 7 |
| Wîhkwêntôwin | 4 | 7 | 5 | 5 | 6 | 0 | 27 |
| Quarters/Boyle | 5 | 11 | 2 | 5 | 4 | 4 | 31 |
| Strathcona/Whyte | 10 | 11 | 4 | 0 | 1 | 1 | 27 |
| Garneau/University | 5 | 14 | 2 | 1 | 0 | 0 | 22 |
| Blatchford | 23 | 5 | 1 | 4 | 1 | 1 | 35 |
| Exhibition Lands | 19 | 2 | 0 | 0 | 0 | 1 | 22 |
| Other | 75 | 30 | 9 | 7 | 7 | 6 | 134 |
| **Total** | **146** | **81** | **31** | **27** | **24** | **17** | **326** |

## Storey band × height_confidence

| Confidence | < 6 | 6–7 | 8–11 | 12–19 | 20+ | still unknown | Total |
|---|---|---|---|---|---|---|---|
| high | 20 | 15 | 4 | 5 | 6 | 0 | 50 |
| medium | 28 | 13 | 7 | 8 | 5 | 0 | 61 |
| low | 98 | 53 | 20 | 14 | 13 | 0 | 198 |
| — | 0 | 0 | 0 | 0 | 0 | 17 | 17 |
| **Total** | **146** | **81** | **31** | **27** | **24** | **17** | **326** |

## height_source × height_confidence

| Source | high | medium | low | — | Total |
|---|---|---|---|---|---|
| dp_description | 36 | 0 | 0 | 0 | 36 |
| dc_text | 5 | 57 | 0 | 0 | 62 |
| skyrisecities | 9 | 4 | 0 | 0 | 13 |
| zone_max | 0 | 0 | 185 | 0 | 185 |
| dwellings_estimate | 0 | 0 | 13 | 0 | 13 |
| still unknown | 0 | 0 | 0 | 17 | 17 |
| **Total** | **50** | **61** | **198** | **17** | **326** |

- **high**: storeys stated in the DP; a DC height that SkyriseCities confirms within 2 storeys; a SkyriseCities project matched by exact street address.
- **medium**: a Direct Control maximum (drafted for the site, but an envelope); a SkyriseCities project matched by name, or the one project inside a rezoned parcel.
- **low**: a zone ceiling (`zone_max`, ≤ 40 m only) or a dwelling-count estimate.

## Storey band × status_mapped

| Status | < 6 | 6–7 | 8–11 | 12–19 | 20+ | still unknown | Total |
|---|---|---|---|---|---|---|---|
| construction | 26 | 19 | 4 | 6 | 3 | 2 | 60 |
| approved | 52 | 20 | 10 | 6 | 1 | 5 | 94 |
| proposed | 44 | 37 | 15 | 12 | 19 | 4 | 131 |
| stalled | 21 | 2 | 1 | 1 | 1 | 6 | 32 |
| unknown | 3 | 3 | 1 | 2 | 0 | 0 | 9 |
| **Total** | **146** | **81** | **31** | **27** | **24** | **17** | **326** |

## Still unknown: why

- 12 × not a new building (alteration, addition, child-care or use change, parking lot): no height needed
- 2 × fewer than 20 dwellings (no estimate; a small building)
- 2 × only a high zone ceiling (downtown / UI / RL h65) and no dwelling count
- 1 × Direct Control text states no height (heritage / use-only DC) and no dwelling count

## Shortlist (a): complete / occupied, likely finished after 2018 → candidate `existing` rows

Occupancy granted on a building permit since 2021, or a SkyriseCities match marked Complete/Occupied with completion 2019 or later (or no year). Check each against LiDAR in the viewer: only towers that show as ground or a stump need an `existing` row.

| permit_id | address | node | height | evidence |
|---|---|---|---|---|
| 451843020-002 | 10220 - 103 AVENUE NW | Ice District | 66 st / 250.84 m (skyrisecities, high) | SkyriseCities Complete 2019, built 66 st / 250.84 m; **already in proposals.csv as P001 Stantec Tower**; DP is work on the existing building |
| 386491616-002 | 10312 - 111 STREET NW | Wîhkwêntôwin | 7 st / 21 m (skyrisecities, high) | SkyriseCities Complete 2023, built 7 st / 21 m |
| 392456153-002 | 10054 - 79 AVENUE NW | Strathcona/Whyte | 7 st / 24 m (dc_text, high) | SkyriseCities Complete 2019, built 6 st / 24.08 m; DP is work on the existing building |
| 408272142-002 | 10757 - 83 AVENUE NW | Garneau/University | 6 st / 18.6 m (dp_description, high) | occupancy 2024-04-04; DP is work on the existing building |
| 387617207-002 | 11209 - 124 STREET NW | Other | 5 st / 15.5 m (skyrisecities, medium) | SkyriseCities Complete 2022, built 5 st |
| 387611865-002 | 8120 - 93 STREET NW | Other | 4 st / 12.4 m (skyrisecities, high) | SkyriseCities Complete 2022, built 4 st |
| 411454341-002 | 8450 - 106A AVENUE NW | Other | 38 st / 120 m (dc_text, medium) | SkyriseCities Complete 2020, built 6 st |

## Shortlist (b): exception-site rows with their best height

| site | permit_id | address | status | height | note |
|---|---|---|---|---|---|
| Ice District | 451843020-002 | 10220 - 103 AVENUE NW | stalled | 66 st / 250.84 m (skyrisecities, high) | SkyriseCities 'Stantec Tower' matched by address 10220 103 AVENUE; Complete 2019 |
| Ice District | REZ-DC1-19860-167448 | (rezoned parcel, 53.54486, -113.49878) | proposed | 51 st / 160 m (dc_text, medium) | DC: tion. The maximum Height shall be 160.0 m / a. the maximum Height shall be 115.0 m |
| Ice District | 655770701-002 | 10305 - 104 AVENUE NW | approved | 12 st / 37.2 m (dwellings_estimate, low) | 386 dwellings -> est 12+ storeys |
| Ice District | 385285059-002 | 10116 - 105 AVENUE NW | stalled | 4 st / 12.4 m (skyrisecities, high) | SkyriseCities 'Boyle Street Community Centre' matched by address 10116 105 AVENUE; Cancelled |
| Ice District | 479708637-002 | 10120 - 103 AVENUE NW | approved | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (CCA: 150 m); no dw |
| Ice District | 395066341-002 | 10220 - 104 AVENUE NW | construction | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (AED: 195 m); no dw |
| Ice District | 400008826-002 | 10255 - 104 AVENUE NW | stalled | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (AED: 195 m); no dw |
| The Quarters | REZ-MU-189183 | (rezoned parcel, 53.54536, -113.48084) | proposed | 27 st / 85 m (zone_max, low) | ceiling of MU h85 f8 (85 m), not a design height; the site was rezoned to it |
| The Quarters | REZ-MU-189200 | (rezoned parcel, 53.54458, -113.48610) | proposed | 24 st / 77 m (zone_max, low) | ceiling of MU h77 f8 cf (77 m), not a design height; the site was rezoned to it |
| The Quarters | REZ-MU-189190 | (rezoned parcel, 53.54558, -113.48489) | proposed | 24 st / 77 m (zone_max, low) | ceiling of MU h77 f8 (77 m), not a design height; the site was rezoned to it |
| The Quarters | 341529008-013 | 10210 - 97 STREET NW | construction | 19 st / 60 m (dc_text, medium) | DC: 3. The maximum Height shall be 60.0 m |
| The Quarters | 341529008-020 | 4 - SIR WINSTON CHURCHILL SQUARE NW | construction | 19 st / 60 m (dc_text, medium) | DC: 3. The maximum Height shall be 60.0 m |
| The Quarters | REZ-MU-189191 | (rezoned parcel, 53.54392, -113.48415) | proposed | 16 st / 50 m (zone_max, low) | ceiling of MU h50 f6 (50 m), not a design height; the site was rezoned to it |
| The Quarters | REZ-MU-189192 | (rezoned parcel, 53.54517, -113.48293) | proposed | 12 st / 40 m (zone_max, low) | SkyriseCities 'Artists Quarters' (18 storeys) is taller than the rezoned MU h40 f6.5 allows: older scheme, not |
| The Quarters | 573198770-006 | 9511 - 103 AVENUE NW | approved | 9 st / 28 m (zone_max, low) | ceiling of RM h28 (28 m), not a design height |
| The Quarters | REZ-DC-20908-204599 | (rezoned parcel, 53.54279, -113.48438) | proposed | 7 st / 24.5 m (dc_text, medium) | DC: .2. The maximum Height is 24.5 m |
| The Quarters | REZ-MU-189202 | (rezoned parcel, 53.54326, -113.48322) | proposed | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f4.5 cf (23 m), not a design height; the site was rezoned to it |
| The Quarters | 502329379-002 | 10131 - 97 STREET NW | approved | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f4.5 cf (23 m), not a design height |
| The Quarters | 442148159-002 | 10146 - 96 STREET NW | stalled | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f4.5 (23 m), not a design height |
| The Quarters | 189323110-001 | 9521 - 103A AVENUE NW | proposed | 7 st / 23 m (zone_max, low) | ceiling of RM h23 (23 m), not a design height |
| The Quarters | 660875144-002 | 9550 - 103 AVENUE NW | proposed | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f4.5 (23 m), not a design height |
| The Quarters | 653953886-002 | 9608 - 103 AVENUE NW | approved | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f4.5 (23 m), not a design height |
| The Quarters | 616725212-002 | 10333 - 97 STREET NW | construction | 5 st / 18 m (dc_text, medium) | DC: b. The maximum building Height shall not exceed 18.0 m / e maximum building Height shall not exceed 18.0 m |
| The Quarters | 455466686-002 | 10249 - 96 STREET NW | construction | 4 st / 14 m (zone_max, low) | ceiling of PSN (14 m), not a design height |
| The Quarters | 442013815-004 | 10173 - 97 STREET NW | stalled | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (MU h50 f6 cf: 50 m |
| The Quarters | 656120859-002 | 9804 - JASPER AVENUE NW | approved | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (CCA: 150 m); no dw |
| Station Lands | 315143826-064 | 10445 - 101 STREET NW | approved | 12 st / 37.2 m (dp_description, high) | work on an existing building, not a new one: already in base.glb at its measured height |
| Station Lands | 535898434-002 | 10423 - 101 STREET NW | construction | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); only a high zone ceiling (CCA: 150 m); no dw |
| Blatchford | REZ-MU-271095 | (rezoned parcel, 53.56681, -113.51110) | proposed | 20 st / 65 m (zone_max, low) | ceiling of MU h65 f5.5 cf (65 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-UI-235404 | (rezoned parcel, 53.57008, -113.51040) | proposed | 17 st / 55 m (zone_max, low) | ceiling of UI (55 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-UI-167638 | (rezoned parcel, 53.57225, -113.50581) | proposed | 17 st / 55 m (zone_max, low) | ceiling of UI (55 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-RM-176883 | (rezoned parcel, 53.56154, -113.52189) | proposed | 7 st / 23 m (zone_max, low) | ceiling of RM h23 (23 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-MU-271099 | (rezoned parcel, 53.56493, -113.50936) | proposed | 7 st / 23 m (zone_max, low) | ceiling of MU h23 f3 cf (23 m), not a design height; the site was rezoned to it |
| Blatchford | 648686740-002 | 2704 - BLATCHFORD ROAD NW | proposed | 6 st / 18.6 m (dp_description, high) |  |
| Blatchford | 648757765-002 | 7704 - YORKE MEWS NW | proposed | 6 st / 18.6 m (dp_description, high) |  |
| Blatchford | 471976665-002 | 6644 - ALPHA BOULEVARD NW | construction | 5 st / 15.5 m (dp_description, high) |  |
| Blatchford | REZ-DC1-20181-167484 | (rezoned parcel, 53.56928, -113.50821) | proposed | 5 st / 16 m (dc_text, medium) | DC: 2. The maximum building Height shall be 16.0 m |
| Blatchford | 619823658-002 | 10520 - 112 AVENUE NW | approved | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Blatchford | 548763415-002 | 10607 - PRINCESS ELIZABETH AVENUE NW | approved | 5 st / 16 m (zone_max, low) | ceiling of UF (16 m), not a design height |
| Blatchford | 470899771-002 | 11204 - 119 STREET NW | stalled | 5 st / 16 m (zone_max, low) | ceiling of BE (16 m), not a design height |
| Blatchford | 641333923-002 | 11255 - 116 STREET NW | approved | 5 st / 16 m (zone_max, low) | ceiling of PS (16 m), not a design height |
| Blatchford | 448463730-002 | 11314 - 119 STREET NW | stalled | 5 st / 16 m (zone_max, low) | ceiling of BE (16 m), not a design height |
| Blatchford | 394302366-002 | 12116 - 107 STREET NW | construction | 5 st / 18 m (zone_max, low) | ceiling of PU (18 m), not a design height |
| Blatchford | 489316030-002 | 8603 - FLYING CLUB ROAD NW | approved | 5 st / 16 m (zone_max, low) | ceiling of CG (16 m), not a design height |
| Blatchford | REZ-DC1-19628-167434 | (rezoned parcel, 53.56561, -113.51746) | proposed | 4 st / 14 m (dc_text, medium) | DC: e maximum height of any permanent or temporary structure shall not exceed or 14m |
| Blatchford | REZ-BRH-189586 | (rezoned parcel, 53.57111, -113.51867) | proposed | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-BRH-294702 | (rezoned parcel, 53.57130, -113.52821) | proposed | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height; the site was rezoned to it |
| Blatchford | REZ-BRH-175863 | (rezoned parcel, 53.57245, -113.52008) | proposed | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height; the site was rezoned to it |
| Blatchford | 424829766-002 | 2552 - TONY CASHMAN ROAD NW | construction | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | 385377176-002 | 2756 - BLATCHFORD ROAD NW | construction | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | 392660265-002 | 35 - AIRPORT ROAD NW | stalled | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | 404081612-002 | 7066 - FANE ROAD NW | stalled | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | 438575713-002 | 7738 - YORKE MEWS NW | stalled | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | 434868635-002 | 7746 - YORKE MEWS NW | stalled | 4 st / 13 m (zone_max, low) | ceiling of BRH (13 m), not a design height |
| Blatchford | REZ-DC2-1210-171885 | (rezoned parcel, 53.56466, -113.52743) | proposed | 3 st / 12 m (dc_text, medium) | DC: 2. The maximum Height shall not exceed 12.0 m / e maximum Height shall not exceed 12.0 m |
| Blatchford | 466446250-002 | 10413 - 121 AVENUE NW | stalled | 3 st / 12 m (zone_max, low) | ceiling of CN (12 m), not a design height |
| Blatchford | 585039723-002 | 2755 - BLATCHFORD ROAD NW | approved | 3 st / 10 m (zone_max, low) | ceiling of BP (10 m), not a design height |
| Blatchford | 639177329-002 | 10730 - 118 AVENUE NW | proposed | ? st / ? (unknown, —) | unknown: only a high zone ceiling (UI: 55 m); no dwelling count |
| Rossdale | 454482517-002 | 10209 - 100 AVENUE NW | stalled | 11 st / 35 m (dc_text, high) | DC: b. The maximum Height shall be 35.0 m |
| Rossdale | 396115771-002 | 10303 - 98 AVENUE NW | unknown | 9 st / 27.9 m (dwellings_estimate, low) | 68 dwellings -> est 6–12 storeys |
| Rossdale | 474523140-002 | 10621 - 100 AVENUE NW | approved | 9 st / 27.9 m (dwellings_estimate, low) | 111 dwellings -> est 6–12 storeys |
| Rossdale | 644762729-002 | 9708 - 104 STREET NW | approved | 9 st / 28 m (dc_text, medium) | DC: .2. The maximum Height is 28.0 m |
| Rossdale | 387338041-002 | 9745 - 106 STREET NW | stalled | 5 st / 15.5 m (dwellings_estimate, low) | 20 dwellings -> est 4–6 storeys |
| Rossdale | 609349243-002 | 9929 - 103 STREET NW | proposed | 2 st / 6.2 m (dp_description, high) |  |
| Rossdale | 349481394-002 | 9826 - 103 STREET NW | proposed | ? st / ? (unknown, —) | unknown: only a high zone ceiling (HDR: 50 m); 8 dwellings (< 20, no estimate) |
| Rossdale | 386685114-002 | 10150 - 97 AVENUE NW | stalled | ? st / ? (unknown, —) | unknown: not a new building (alteration / addition / use change); DC text states no height; no dwelling count |
| Exhibition Lands | 520979226-002 | 11403 - 82 STREET NW | approved | 7 st / 23 m (zone_max, low) | ceiling of RM h23 (23 m), not a design height |
| Exhibition Lands | 381366998-002 | 11405 - 82 STREET NW | unknown | 7 st / 23 m (zone_max, low) | ceiling of RM h23 (23 m), not a design height |
| Exhibition Lands | 656660182-002 | 11841 - 83 STREET NW | proposed | 5 st / 15.5 m (dp_description, high) |  |
| Exhibition Lands | 611848030-002 | 11220 - 78 STREET NW | unknown | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 534066839-002 | 11503 - 82 STREET NW | approved | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 534810153-002 | 11507 - 82 STREET NW | approved | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 670114983-002 | 11640 - 80 STREET NW | proposed | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 437316194-002 | 11823 - 83 STREET NW | stalled | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 441400979-002 | 11919 - WAYNE GRETZKY DRIVE NW | unknown | 5 st / 16 m (zone_max, low) | ceiling of MU h16 f3.5 cf (16 m), not a design height |
| Exhibition Lands | 503647558-002 | 11935 - WAYNE GRETZKY DRIVE NW | approved | 5 st / 16 m (zone_max, low) | ceiling of MU h16 f3.5 cf (16 m), not a design height |
| Exhibition Lands | 501252383-002 | 12022 - 77 STREET NW | construction | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 390378244-002 | 7020 - 118 AVENUE NW | unknown | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 561311280-002 | 7300 - 116 AVENUE NW | approved | 5 st / 16 m (zone_max, low) | ceiling of UF (16 m), not a design height |
| Exhibition Lands | 408768280-002 | 8003 - 120 AVENUE NW | stalled | 5 st / 16 m (zone_max, low) | ceiling of RM h16 (16 m), not a design height |
| Exhibition Lands | 543426469-002 | 11507 - 71 STREET NW | approved | 3 st / 9.3 m (dp_description, high) |  |
| Exhibition Lands | 633752709-002 | 11806 - 83 STREET NW | approved | 3 st / 9.3 m (dp_description, high) |  |
| Exhibition Lands | 667062870-002 | 11906 - 76 STREET NW | proposed | 3 st / 9.3 m (dp_description, high) |  |
| Exhibition Lands | 671117413-002 | 12121 - 82 STREET NW | proposed | 3 st / 9.3 m (dp_description, high) |  |
| Exhibition Lands | 516938279-002 | 11226 - 82 STREET NW | construction | 3 st / 12 m (zone_max, low) | ceiling of CN (12 m), not a design height |
| Exhibition Lands | 376743184-002 | 11246 - 82 STREET NW | stalled | 3 st / 12 m (zone_max, low) | ceiling of CN (12 m), not a design height |
| Exhibition Lands | 390304139-002 | 8118 - 118 AVENUE NW | stalled | 3 st / 10 m (dc_text, medium) | DC: b. The maximum building height shall not exceed 10 m / e maximum building height shall not exceed 10 m |
| Exhibition Lands | 580311880-002 | 11925 - 70 STREET NW | approved | 3 st / 12 m (zone_max, low) | ceiling of RSM h12 (12 m), not a design height |
| Exhibition Lands | 640652677-002 | 11723 - 80 STREET NW | proposed | ? st / ? (unknown, —) | unknown: only a high zone ceiling (RL h65: 65 m); 6 dwellings (< 20, no estimate) |
| Exhibition Lands | 564135410-002 | 11120 - 73 STREET NW | approved | ? st / ? (unknown, —) | unknown: only a high zone ceiling (UI: 55 m); no dwelling count |

## Method and judgment calls (Phase 3A.5)

- **Zone at each site**: the latest Zoning Bylaw Map snapshot (`67p2-r285`, 2026-09-14), point in polygon. Rezoning rows use their own polygon link.
- **Direct Control** (`dc_text`): the zone link on the map (`zoningbylaw.edmonton.ca/dc-NNNNN`, `dc1-`, `dc2-`) is fetched once and every line stating a *maximum* Height or storey count is read; podium, street-wall, stepback and setback limits are skipped. Multi-tower DCs take the tallest tower. Storeys = stated storeys, else floor(height ÷ 3.1 m).
- **Order changed from the brief**: SkyriseCities sits above `zone_max`. A zone maximum is a ceiling, not a height; a project's own storey count is better evidence, and scope.md's height priority (the source of truth) lists SkyriseCities but not zone maxima. `zone_max_m` is still recorded on every row.
- **High zone ceilings are not used as heights**: `zone_max` only fills a row when the ceiling is ≤ 40 m (it then caps the band below 13 storeys, which is what the storey floor needs). Downtown ceilings (CCA 150 m, AED 180–275 m, CMU/HDR/UW/RMU 50–70 m, UI 55 m, RL h65) fall through to the dwellings estimate or stay unknown.
- **Dwellings estimate**: the brief's ranges, stored as a single number for banding (4–6 → 5, 6–12 → 9, 12+ → 12), capped by the zone ceiling when that is lower. Fewer than 20 dwellings: no estimate.
- **SkyriseCities** (357 Edmonton projects, one fetch of the city page, then one fetch per project page within 120 m of a candidate, 2 s apart, cached; no forum threads). Matched by street address (high); by a DP name in brackets matching the project title within 250 m (medium; generic words such as 'apartment' or 'site' and neighbourhood names alone don't count beyond 30 m). A rezoning row has no address: it takes the one building project (≥ 3 storeys, not a park or transit job) whose pin is inside the rezoned parcel, only for parcels ≤ 15,000 m², not finished before the rezoning, and not more than 10% taller than the new zone allows (a taller one is an older scheme the rezoning replaced). A first pass also matched 'same block of the same street'; it linked unrelated buildings (a 9-dwelling DP to the 16-storey Forest Garden), so it was dropped. Coordinates alone never match.
- **Metres ↔ storeys**: 3.1 m per residential storey, 4.0 m for office-only DPs (scope.md).
- **Conversions** of existing buildings are flagged in `height_note`: they are already in `base.glb` at their measured height and are not new towers.
- Rows that already had storeys in the DP text keep them (`dp_description`, high).

## Status rules

- **construction**: a new-building / foundation / excavation / structure building permit (`24uj-dj8v`) issued within 40 m of the site (or at the same address) since the DP, no occupancy yet.
- **approved**: DP approved, no building permit yet, decision less than 3 years ago.
- **stalled**: DP approved more than 3 years ago and no building permit since.
- **proposed**: DP application In Progress or Appealed, or a rezoning adopted with no DP on the site yet.
- **unknown**: DP refused; DP status 'Other' (the portal lumps withdrawn, cancelled and expired together); or the building is complete (occupancy granted: a lead for an `existing` row if it post-dates the LiDAR).
