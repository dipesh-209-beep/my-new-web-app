#!/usr/bin/env bash
#
# scripts/backup.sh -- timestamped, restorable PostgreSQL backup of the
# route-finder database.
#
#   scripts/backup.sh            # one backup, prune old ones
#   KEEP=14 scripts/backup.sh    # keep 14 instead of the default 7
#
# Writes backups/ktm_bus_route_finder-<UTC timestamp>.dump (gitignored).
# Custom format (-Fc), not SQL text: compressed, and restorable in
# parallel with pg_restore. Text format is only readable if you also
# remember to gzip it.
#
# Deliberate choices worth knowing about:
#
#   * pg_dump runs *inside* the db container. A host-installed pg_dump
#     may be a different major version than the server, and pg_dump
#     refuses to dump from a newer server with an older client. Running
#     inside the container guarantees the versions match, and means the
#     script works on a machine with no PostgreSQL client at all.
#   * The password is passed via PGPASSWORD inside the container's
#     environment, never as a command-line argument. Arguments are
#     visible in `ps` to every process on the host.
#   * Every backup is verified with `pg_restore --list` before it is
#     declared good. An unverified backup is a belief, not a backup, and
#     the moment you find out it is corrupt is the worst possible moment.
#   * Pruning happens *after* a successful verify, so a run that fails
#     never deletes the previous good backup on its way out.
#
# This backs up the whole database, including admin_audit_log. That table
# is the record of who changed what, and a backup taken without it is a
# partial record at best -- see docs/security.md for the retention
# discussion.
set -euo pipefail

DB_NAME="${DB_NAME:-ktm_bus_route_finder}"
DB_USER="${DB_USER:-ktm_bus}"
DB_CONTAINER="${DB_CONTAINER:-ktm_bus_db}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups}"
KEEP="${KEEP:-7}"

timestamp() { date -u +%Y%m%dT%H%M%SZ; }

# The password comes from the root .env, which is where docker-compose
# reads it from too, so the two cannot drift apart silently.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "error: $ENV_FILE not found -- run 'make backend-env' first" >&2
    exit 1
fi

POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
if [[ -z "$POSTGRES_PASSWORD" ]]; then
    echo "error: POSTGRES_PASSWORD not set in $ENV_FILE" >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    echo "error: container '$DB_CONTAINER' is not running." >&2
    echo "       start it with 'make up' (dev) or 'make up-prod'." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/${DB_NAME}-$(timestamp).dump"

echo "==> dumping $DB_NAME to $(basename "$out")"
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
    pg_dump --username "$DB_USER" --dbname "$DB_NAME" \
            --format custom --compress 9 --no-owner --no-privileges \
    > "$out.tmp"

# `pg_restore --list` reads the archive's table of contents. If that
# succeeds the archive is structurally valid and can actually be read
# back; if it fails, the file on disk is a truncated stream.
echo "==> verifying archive"
if ! docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
        pg_restore --list < "$out.tmp" > /dev/null 2>"$out.verify.log"; then
    echo "error: archive did not verify; keeping the log at $out.verify.log" >&2
    cat "$out.verify.log" >&2
    rm -f "$out.tmp"
    exit 1
fi
rm -f "$out.verify.log"

mv "$out.tmp" "$out"
chmod 600 "$out"

entries="$(docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$DB_CONTAINER" \
    pg_restore --list < "$out" | grep -c '^[0-9]' || true)"
size="$(du -h "$out" | cut -f1)"
echo "==> ok: $size, $entries table-of-contents entries"

# Prune oldest, newest $KEEP retained. Deliberately after the verify.
mapfile -t old < <(ls -1t "$BACKUP_DIR"/${DB_NAME}-*.dump 2>/dev/null | tail -n "+$((KEEP + 1))")
if (( ${#old[@]} )); then
    echo "==> pruning ${#old[@]} backup(s) beyond KEEP=$KEEP"
    rm -f "${old[@]}"
fi

# Reminder that matters: a backup nobody has restored from is untested.
echo
echo "Local copy: $out"
echo "This is on this machine only. Copy it somewhere else:"
echo "  docker cp $out <host>:/path/ && sync"
echo "And prove it restores:  make restore-backup FILE=$out DB_NAME=<scratch-db>"
