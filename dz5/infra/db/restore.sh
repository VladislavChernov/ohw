#!/bin/sh
# Restore the dz5 stand DB to its initial seed state after a destructive
# tool-abuse run (DROP/DELETE). Idempotent: rebuild + reload, repeated calls
# yield identical content.
set -e

DB="${DB_PATH:-/data/db.sqlite}"
DIR="$(dirname "$0")"

sqlite3 "$DB" < "$DIR/init.sql"
sqlite3 "$DB" < "$DIR/seed.sql"

echo "restored: $DB"
sqlite3 "$DB" "SELECT username, role FROM users ORDER BY id;"
