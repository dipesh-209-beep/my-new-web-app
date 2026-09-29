#!/usr/bin/env bash
#
# scripts/restore.sh -- restore a dump produced by scripts/backup.sh.
#
#   make restore-backup FILE=backups/ktm_bus_route_finder-....dump
#   DB_NAME=ktm_bus_sectest make restore-backup FILE=...   # into a scratch db
#
# Refuses to run against a database that already has data unless
# FORCE_REPLACE=1, and even then it takes a safety backup first. The
# default here is "you have to mean it": a restore is one of the few
# operations in this project that can lose data that exists nowhere else,
# and the whole point of the prompt is that it cannot happen by accident
# at 2am because someone fat-fingered a variable.
#
#   -h / --dry-run   list the archive contents and stop
#   FORCE_REPLACE=1  allow restoring over a non-empty database
#
# pg_restore runs inside the db container (see the note in backup.sh about
# client/server version skew) and with --exit-on-error, so a restore that
# half-fails says so loudly rather than leaving a silently incomplete
# database behind.
set -euo pipefail

# Accept configuration in either form, because both are natural to type
# and only supporting one of them produces a confusing "FILE is required"
# when the other obviously looks right:
#
#   FILE=x ./scripts/restore.sh        (environment prefix)
#   ./scripts/restore.sh FILE=x        (argument, as `make` passes it)
#
# Argument-form KEY=value pairs are promoted into the environment before
# anything reads them. Unrecognised leading-dash arguments are an error;
# an unrecognised KEY is passed through silently, since this script does
# not enumerate every variable it consults indirectly.
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
        --*) echo "error: unknown option '$arg'" >&2; exit 2 ;;
        *=*) export "${arg?}" ;;
        *)  echo "error: unexpected argument '$arg' (use KEY=value)" >&2; exit 2 ;;
    esac
done

DB_NAME="${DB_NAME:-ktm_bus_route_finder}"
DB_USER="${DB_USER:-ktm_bus}"
DB_CONTAINER="${DB_CONTAINER:-ktm_bus_db}"

if [[ -z "${FILE:-}" ]]; then
    echo "error: FILE is required, e.g. FILE=backups/ktm_bus_route_finder-20260927T120000Z.dump" >&2
    exit 2
fi
if [[ ! -f "$FILE" ]]; then
    echo "error: no such file: $FILE" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
if [[ -z "$POSTGRES_PASSWORD" ]]; then
    echo "error: POSTGRES_PASSWORD not found in $ENV_FILE -- run 'make backend-env'" >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    echo "error: container '$DB_CONTAINER' is not running" >&2
    exit 1
fi

psql_q() {
    docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
        psql --username "$DB_USER" --dbname "$1" --tuples-only --no-align -c "$2"
}

if ! psql_q postgres "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    echo "error: database '$DB_NAME' does not exist" >&2
    exit 1
fi

rows="$(psql_q "$DB_NAME" "SELECT count(*) FROM pg_class WHERE relkind IN ('r','m') AND relnamespace = 'public'::regnamespace" || echo 0)"

out="$(mktemp)"
trap 'rm -f "$out"' EXIT

echo "==> archive: $FILE ($(du -h "$FILE" | cut -f1))"
echo "==> target:  $DB_NAME (public tables currently: $rows)"

if [[ "$DRY_RUN" == "1" ]]; then
    # pg_restore --list emits `dumpId; tableoid oid TAG [schema] name owner`
    # after a `;`-prefixed comment header. Splitting that on whitespace is a
    # trap: TAG can be two words ("TABLE DATA"), so field-index-based
    # parsing silently shreds it. Strip the three leading numeric fields
    # instead and let the rest of the line stand as-is.
    echo "==> contents (dry run, nothing written):"
    docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
        pg_restore --list < "$FILE" \
      | sed -E 's/^[0-9]+;[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+//' \
      | grep -E '^TABLE( DATA)? ' \
      | sed -E 's/[[:space:]]+[^[:space:]]+$//' \
      | sort > "$out.list" || true
    sed 's/^/  /' "$out.list"
    printf '\n  %s tables, %s table-data sections\n' \
        "$(grep -c '^TABLE ' "$out.list" || true)" \
        "$(grep -c '^TABLE DATA ' "$out.list" || true)"
    exit 0
fi

if [[ "$rows" != "0" ]]; then
    if [[ "${FORCE_REPLACE:-0}" != "1" ]]; then
        cat >&2 <<EOF

error: '$DB_NAME' already has $rows public tables. Refusing to restore over it.

    This would DROP and replace everything in that database. If that is
    what you want, re-run with FORCE_REPLACE=1 -- and note that a safety
    backup of the current contents is taken first.

    To restore somewhere harmless first and check the result:

        docker exec $DB_CONTAINER createdb -U $DB_USER ktm_bus_scratch
        DB_NAME=ktm_bus_scratch make restore-backup FILE=$FILE

EOF
        exit 1
    fi

    echo "==> FORCE_REPLACE set: taking a safety backup of the current contents first"
    KEEP=7 "$REPO_ROOT/scripts/backup.sh" || {
        echo "error: safety backup failed; not proceeding" >&2
        exit 1
    }
fi

echo "==> restoring (--exit-on-error, --clean drops objects that are in the way)"
if ! docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
        pg_restore --username "$DB_USER" --dbname "$DB_NAME" \
                   --clean --if-exists --exit-on-error --no-owner --no-privileges \
                   < "$FILE"; then
    echo >&2
    echo "error: restore failed partway. The database is now INCOMPLETE." >&2
    echo "       Do not start the application against it. Re-run the restore" >&2
    echo "       from the same archive, or restore a known-good backup." >&2
    exit 1
fi

echo "==> restored. Sanity check:"
psql_q "$DB_NAME" "SELECT 'stops=' || count(*) FROM stops" 2>/dev/null || echo "  (stops table absent -- expected if the archive predates it)"
psql_q "$DB_NAME" "SELECT 'routes=' || count(*) FROM routes" 2>/dev/null || true
echo
echo "Remember to check the migration version matches the code you are about to run:"
echo "  docker exec $DB_CONTAINER psql -U $DB_USER -d $DB_NAME -c 'SELECT * FROM alembic_version'"
echo "  (then 'alembic upgrade head' in backend/ if it is behind)"
