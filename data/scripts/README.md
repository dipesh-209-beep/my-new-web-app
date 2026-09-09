# scripts/

Cleaning and validation pipeline for the Kathmandu Bus Route Finder dataset.
Turns `data/raw/*.csv` into `data/processed/*_clean.csv` + `processed/report.md`,
and can validate the result without needing a live Postgres instance.

## Setup

```bash
pip install -r scripts/requirements.txt
```

## Run the cleaning pipeline

```bash
python scripts/clean_data.py --raw-dir data/raw --out-dir data/processed
```

This will:
1. Remove `route_stops` rows referencing a `stop_id` that has no row in `stops`
2. Re-sequence `sequence_no` per route (1..N, gaps closed) after removals
3. Recompute `routes.start_stop_id` / `end_stop_id` / `total_stops` from the
   cleaned `route_stops`
4. Resolve or null out `routes.operator_id` values that don't match any row
   in `operators` — tries `operator_id_raw`, then `route_operators`, before
   giving up and setting `NULL`
5. Flag routes whose recorded distance looks implausible against a haversine
   (straight-line) estimate computed from stop coordinates
6. Regenerate `processed/report.md` documenting every change, in the same
   format as the original manual audit
7. Run the same referential-integrity checks `import_data.py`'s
   sanity-check block runs in Postgres — printed to the console as it goes

Add `--fail-on-verify-error` to exit non-zero if any check fails (used in CI).

## Load into the database

```bash
python scripts/import_data.py
```

Loads `data/processed/*_clean.csv` into the schema Alembic already
created, in the same table order and with the same COPY options the old
`data/import.sql` / `data/import_in_container.sql` used -- but takes the
CSV directory as an argument instead of requiring a hardcoded absolute
path hand-edited per machine/container first. Those two `.sql` files have
been retired now that this path is proven out end-to-end (data clean →
validate → migrate → import → OSRM, all via `make setup`, then `make up` to
build + start the backend); this script is
the only supported way to load the dataset.
Reads `DATABASE_URL` from `backend/.env` by default (`--database-url` to
override). Add `--truncate` to clear the six tables first when
re-importing into a non-empty dev database.

From the repo root, `make import` (see the root `Makefile`) runs this
plus the `pip install` step above in one go, after `make migrate`.

## Validate an already-cleaned dataset

If you just want to check `data/processed/` is internally consistent —
no cleaning, no database required:

```bash
python scripts/validate_clean.py --dir data/processed
```

## Run the tests

```bash
pip install pytest
pytest scripts/test_clean_data.py -v
```

These pin the exact behaviors documented in `report.md` (orphan removal,
resequencing, operator_id resolution, total_stops recomputation) against
small fixtures, so a future change to the pipeline can't silently break
them without a test failing.

## CI

`.github/workflows/ci.yml` has a dedicated `data-pipeline-tests` job for
this pipeline, run on every PR/push to `main`:

1. `pytest test_clean_data.py` — the regression tests above
2. `python scripts/clean_data.py --raw-dir raw --out-dir processed --fail-on-verify-error`
   — re-runs cleaning against the committed `data/raw` and fails the build
   if any referential-integrity check comes back nonzero
3. `python scripts/validate_clean.py --dir processed` — validates the
   resulting `data/processed` output

The `backend-tests` job in the same workflow separately runs
`python data/scripts/import_data.py` (from the repo root) to load that
`data/processed` output into Postgres before the backend test suite runs,
since several backend tests assert against real stop/route IDs from the
shipped dataset (e.g. `test_route_finder_api.py`'s `S0072`,
`test_admin_route_status.py`'s `R2295986`).

## Known caveats / things to verify against your real data

- **Raw filename mapping**: `clean_data.py`'s `RAW_FILENAMES` dict currently
  points at the v2-named files (`stops_production_v2.csv`,
  `routes_production_v2_fixed.csv`, etc.). The original `report.md` refers to
  v3-named files. If your actual raw filenames differ, edit `RAW_FILENAMES`
  at the top of `clean_data.py` — don't guess, just match what's in
  `data/raw/`.
- **`geo_out_of_bounds` bounding box** (`VALLEY_BBOX` in `clean_data.py`) is
  a rough Kathmandu Valley box, not sourced from your original cleaning
  logic (which wasn't available to reconstruct from). Verify it against
  what the original v3 cleaning actually used, or your real stop
  coordinates, and adjust.
- **`unverified_fields` inference** (which optional stop fields count as
  "unverified") is a best-effort reconstruction from the clean CSVs'
  existing values, not a documented rule from the original run. Check a
  sample of `stops_clean.csv` against what this script would produce and
  adjust the `optional_fields` list in `clean_stops()` if it doesn't match.
- **Distance-flagging threshold** (`recorded < haversine * 0.9` in
  `clean_routes()`) is a reasonable heuristic, not a value taken from the
  original report — tune it if it flags too many/few routes on your real
  data.

Run the pipeline against your real `data/raw`, diff the output against the
currently-committed `data/processed`, and adjust the three items above until
they match (or intentionally differ, if you're improving on the original
logic).

## Other scripts in this folder

Not part of the main `clean_data.py` → `validate_clean.py` pipeline above —
one-off tools used during dataset QA:

- **`merge_stops.py`** — applies confirmed stop-duplicate merges from
  `stop_dedup_overrides.yaml` directly against an already-imported live
  database (repoints `route_stops`/`routes` references, then deletes the
  now-orphaned `stops` rows). Human-reviewed input required; not run
  automatically. **Note:** `clean_data.py`'s `dedup_stops()` now applies
  the same `stop_dedup_overrides.yaml` merges at the CSV stage, before
  the data ever reaches the DB — for the standard workflow (regenerate
  CSVs, `import_data.py --truncate`), that supersedes this script. Kept
  for the one case that's still useful: patching an already-imported DB
  in place after confirming a new merge, without a full reimport.
- **`fix_compound_csv_fields.py`** — one-off fixer for raw rows where an
  unquoted comma inside a free-text field (`aliases` in
  `stops_production_v2.csv`, `operator`/`notes`/etc. in
  `routes_production_v2_fixed.csv`) shifted every column after it out of
  alignment. Two profiles, `stops` and `routes` (`--profile` positional
  arg), sharing one CLI: dry run by default, `--apply` to write, backup
  taken first. As of the currently-committed raw CSVs both profiles
  report 0 rows needing a fix — kept for the next raw-data refresh, in
  case the corruption recurs. (Merged from two near-identical files,
  `fix_stops_aliases.py` and `fix_routes_compound_fields.py`, whose own
  docstrings already noted they were "the same idea" — verified
  byte-identical output against both the original scripts before
  deleting them, including the unfixed-row and trailing-column-padding
  edge cases.)
- **`verify_stop_coordinates.py`** — cross-checks stop coordinates against
  OpenStreetMap (Nominatim) by name/alias/zone-district context; read-only,
  writes a verification report CSV rather than modifying the input. Run
  with no flags for a full pass over every stop, or pass
  `--previous <prior verification CSV>` to only re-query stops that came
  back `NO_MATCH` last time (much faster re-runs — everything else is
  carried over unchanged from the previous file). (Previously two separate
  files, `verify_stop_coordinates.py` and `verify_stop_coordinates_v2.py`,
  which duplicated ~90% of the same code; merged into one script with a
  flag once it became clear they were sequential stages of one workflow,
  not two independent tools.)
- **`stop_dedup_overrides.yaml`**, **`route_dedup_overrides.yaml`**,
  **`return_leg_overrides.yaml`** — human-confirmed override files consumed
  by the pipeline/merge scripts above for candidate duplicates flagged
  during cleaning.
