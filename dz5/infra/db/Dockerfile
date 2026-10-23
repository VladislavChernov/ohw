# SQLite CLI image for the dz5 security stand database.
#
# Pinned tag (not :latest) so the CLI is reproducible. The container is not a
# server: the DB is a file in the db_data volume, seeded/restored on demand and
# read by the agent's db_query tool.
FROM keinos/sqlite3:3.53.4

# The base image runs as the non-root `sqlite` user (uid 100 / gid 101), and
# already ships a root-owned /data. Switch to root just long enough to make the
# db_data mount point writable by the runtime user, then drop back to sqlite.
USER root
RUN mkdir -p /data && chown -R 100:101 /data
USER sqlite

WORKDIR /db

# Schema + seed/restore/query scripts. They are also bind-mounted read-only in
# compose, so edits take effect without rebuilding; the COPY is a fallback so
# the image is self-contained. Scripts are invoked via `sh /db/<script>` (not
# direct exec) because the exec bit does not survive a Windows bind-mount.
COPY init.sql /db/init.sql
COPY seed.sh /db/seed.sh
COPY restore.sh /db/restore.sh
COPY query.sh /db/query.sh
COPY admin.sh /db/admin.sh

# Default DB path inside the container (backed by the db_data volume).
ENV DB_PATH=/data/db.sqlite

# No long-running process; used via `docker compose run --rm db sh /db/<script>`.
ENTRYPOINT ["/bin/sh"]
