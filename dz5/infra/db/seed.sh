#!/bin/sh
# Seed the dz5 stand DB with synthetic users + PII.
# Idempotent: rebuilds the schema and reloads a fixed data set.
set -e

DB="${DB_PATH:-/data/db.sqlite}"
DIR="$(dirname "$0")"

# (Re)create schema, then load the fixed seed data.
sqlite3 "$DB" < "$DIR/init.sql"
sqlite3 "$DB" < "$DIR/seed.sql"

echo "seeded: $DB"
sqlite3 "$DB" "SELECT COUNT(*) AS users FROM users; SELECT COUNT(*) AS pii FROM personal_data;"
