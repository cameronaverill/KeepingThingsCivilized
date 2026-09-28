#!/usr/bin/env bash
# Fly.io entrypoint for the single "web" process group (fly.toml, docs/deployment_plan.md item 9).
#
# Runs web (gunicorn) and the moderator worker (litestream-wrapped `manage.py run_moderator`) as two processes in
# ONE machine, not two. Real-deploy incident, 2026-09-28: a Fly Volume can only be mounted read/write on one host at
# a time. The original design pinned "web" and "worker" to two separate always-on machines that both expected the
# same volume -- the second one to claim it never could, so every worker machine crashed instantly and forever
# (`DJANGO_DB_PATH points into a folder that does not exist: /data`), hit its restart cap, and was left `stopped`.
# No moderator reply ever posted, because nothing was ever running to process a pending ModerationRun.
#
# Fix: put both processes on the one machine that actually holds the volume. Mirrors scripts/dev.sh's job-control
# shape (used for local dev): migrate once, then run both processes together; either one exiting stops both, and
# SIGTERM/SIGINT (how Fly stops a machine) is forwarded to both so a stop or redeploy shuts them down cleanly.
set -euo pipefail

python manage.py migrate --noinput

set -m
export PYTHONUNBUFFERED=1

# Litestream starts/stops with the worker command it wraps and continuously streams the shared /data/db.sqlite3 WAL
# to B2 regardless of which process (web or worker) wrote to it -- unchanged from the previous two-machine design,
# just now a second process on the same machine instead of a process on a second machine.
litestream replicate -config /etc/litestream.yml -exec "python manage.py run_moderator" &
WORKER_PID=$!

gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 1 --worker-class gthread --threads 4 --timeout 60 &
WEB_PID=$!

stop_all() {
    trap - INT TERM EXIT
    kill -TERM -- "-$WEB_PID" "-$WORKER_PID" 2>/dev/null || true
    wait "$WEB_PID" "$WORKER_PID" 2>/dev/null || true
}
trap 'stop_all; exit 143' TERM
trap 'stop_all; exit 130' INT
trap 'stop_all' EXIT

# Returns as soon as either process ends; its exit status becomes the container's own, so Fly can tell a real
# crash apart from a clean stop.
status=0
wait -n "$WEB_PID" "$WORKER_PID" || status=$?
stop_all
exit "$status"
