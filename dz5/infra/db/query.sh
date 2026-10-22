#!/bin/sh
# Run an arbitrary read query against the stand DB (manual inspection / smoke).
# Usage: query.sh "SELECT username, role FROM users;"
set -e

DB="${DB_PATH:-/data/db.sqlite}"
sqlite3 -header -column "$DB" "$1"
