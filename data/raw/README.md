# Raw Data Sources

This folder holds the unprocessed inputs to the cleaning pipeline documented in
`../processed/report.md`. Each file below is paired with where it came from.

## 1. OSM / Overpass Turbo exports
**Source:** [Overpass Turbo](https://overpass-turbo.eu/) queries against OpenStreetMap,
filtered to `bus`, `microbus`, and `tempo` route/stop tags for the Kathmandu Valley.
**Method:** manual Overpass QL query, exported as raw OSM XML, then converted
to CSV for the cleaning pipeline.
**Files:**
- `overpass_export_2026-07-11_1331.osm`, `overpass_export_2026-07-11_1335.osm`
  — raw Overpass XML exports (2026-07-11)
- `stops_production_v2.csv` — stop locations, names, tags (converted from the
  exports above)
- `route_stops_production_v2.csv` — stop sequences per route
- `routes_production_v2_fixed.csv` — route geometries/metadata

## 2. 2013 Yatayat (neogeomat.github.io/yatayat)
**Source:** [Kathmandu Public Transport](https://neogeomat.github.io/yatayat/) —
a community mapping project by Kathmandu University Geomatics and the Monsoon
Collective, built on OSM data with a Leaflet routing UI and fare reference page.
**Method:** exported/scraped route and fare listings from the site.
**Files:**
- `yatayat_export.osm` — the underlying 2013 OSM export (`osm_base` timestamp
  2013-08-07) the Yatayat project was built on
- Cross-referenced against `routes_production_v2_fixed.csv` and
  `route_stops_production_v2.csv` for route naming and continuity

## 3. DOTM (Department of Transport Management) records
**Source:** Nepal DOTM operator/route registration records.
**Method:** manual transcription / public dataset 
**Files:**
- `operators.csv` — registered transport operator/company details
- `route_operators_production.csv` — operator-to-route assignments

## Previously unintegrated candidate routes — now merged

- `routes_needs_review.csv`, `route_stops_production_v2_needs_review.csv`,
  `route_operators_production_needs_review.csv` used to hold 6 candidate
  routes (`R2282031`, `R3020212`, `R3020213`, `R3020231`, `R3028077`,
  `R3102124`) with per-route notes marking them "PROMOTED" via a
  direct-ID Overpass query on 2026-07-30, plus 5 further routes
  (`R3351751`–`R3351755`) that were only in a newer OSM re-export and
  hadn't been folded in yet. Both batches have since been merged into
  the three production CSVs above (`routes_production_v2_fixed.csv`,
  `route_stops_production_v2.csv`, `stops_production_v2.csv`), along
  with the 25 new `S_xxx`-prefixed stops those routes reference and
  `route_operators_production.csv` rows where an operator was known
  (6 of the 11 routes still have no resolved operator — see
  `../processed/report.md`). The three `*_needs_review.csv` files have
  been deleted; `clean_data.py`'s `RAW_FILENAMES` map never referenced
  them, so removing them doesn't change what the pipeline reads.

## Congestion severity tables — origin NOT recorded here

`../congestion_loss_table.csv`, `../congestion_zones.csv` and
`../stop_congestion_scores.csv` are **not** parts of this raw set: there is no
raw counterpart for them, `clean_data.py` never reads them, and no export,
transcription date, author/publisher, method or license for the severity
scores is recorded here (or anywhere else in the repo). Earlier code described
the table as a "real, measured" study; that could not be substantiated and has
been corrected. If you know the real source, record it here and update
`../LICENSING.md`. Until then these are treated as unverified estimates — see
`../README.md` ("Congestion data provenance") and
`backend/app/routing/congestion_provenance.py`.

## Notes
- Files above are the pre-cleaning originals. See `../processed/report.md` for
  the orphan-pair audit and cleanup applied to produce `../processed/*_clean.csv`.
- Collection dates and exact Overpass QL queries are not yet recorded — add them
  here if you still have them, since OSM data changes over time and future
  reproducibility depends on knowing the export date.
