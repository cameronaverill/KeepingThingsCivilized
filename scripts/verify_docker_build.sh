#!/usr/bin/env bash
# One-shot local verification of the deploy image (docs/deployment_plan.md section 4). Needs Docker on your own
# machine (this repo's own sandbox has neither Docker nor flyctl). Costs nothing, touches no Fly account.
# Run from anywhere: scripts/verify_docker_build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
    echo "verify_docker_build.sh: docker not found on PATH." >&2
    exit 1
fi

IMAGE=think-together-verify
VOLUME=tt-smoke-data
WEB=tt-verify-web
WORKER=tt-verify-worker
PORT=18080

cleanup() {
    docker rm -f "$WEB" "$WORKER" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

secret_key() { python3 -c 'import secrets; print(secrets.token_urlsafe(50))'; }

echo "== 1/5: docker build =="
docker build -t "$IMAGE" .

echo "== 2/5: manage.py check --deploy =="
docker run --rm \
    -e DJANGO_ENV=production \
    -e DJANGO_SECRET_KEY="$(secret_key)" \
    -e DJANGO_ALLOWED_HOSTS=localhost \
    -e DJANGO_DB_PATH=/tmp/check.sqlite3 \
    "$IMAGE" python manage.py check --deploy

echo "== 3/5: litestream is installed =="
docker run --rm "$IMAGE" litestream version

echo "== 4/5: the real web command (migrate, then serve), against a throwaway volume standing in for Fly's /data =="
# Runs the exact fly.toml "web" command, not the Dockerfile's default CMD, so this is what actually deploys.
docker volume create "$VOLUME" >/dev/null
docker run -d --name "$WEB" -p "$PORT":8080 \
    -e DJANGO_ENV=production -e DJANGO_SECRET_KEY="$(secret_key)" \
    -e DJANGO_ALLOWED_HOSTS=localhost -e DJANGO_DB_PATH=/data/db.sqlite3 \
    -v "$VOLUME":/data \
    "$IMAGE" sh -c "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 1 --worker-class gthread --threads 4 --timeout 60" \
    >/dev/null

status=000
for _ in $(seq 1 15); do
    status="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/accounts/login/" || echo 000)"
    [ "$status" = "200" ] && break
    sleep 1
done
echo "GET /accounts/login/ -> $status"
docker logs "$WEB" --tail 20
docker rm -f "$WEB" >/dev/null
if [ "$status" != "200" ]; then
    echo "FAILED: web never returned 200 (see logs above)." >&2
    exit 1
fi

echo "== 5/5: worker + litestream's -exec wrapping actually starts =="
# Fake B2 credentials: this only proves litestream starts and still runs the wrapped worker command underneath it,
# not that it can reach B2 (a real bucket/keys are a separate, later check once you've created them).
docker run -d --name "$WORKER" \
    -e DJANGO_ENV=production -e DJANGO_SECRET_KEY="$(secret_key)" \
    -e DJANGO_ALLOWED_HOSTS=localhost -e DJANGO_DB_PATH=/data/db.sqlite3 \
    -e LITESTREAM_B2_BUCKET=test -e LITESTREAM_B2_ENDPOINT=test.example.com -e LITESTREAM_B2_REGION=test \
    -e LITESTREAM_ACCESS_KEY_ID=test -e LITESTREAM_SECRET_ACCESS_KEY=test \
    -v "$VOLUME":/data \
    "$IMAGE" litestream replicate -config /etc/litestream.yml -exec "python manage.py run_moderator" >/dev/null

started=""
for _ in $(seq 1 15); do
    logs="$(docker logs "$WORKER" 2>&1)"
    if grep -q "Worker started" <<<"$logs"; then
        started=1
        break
    fi
    sleep 1
done
echo "$logs"
docker rm -f "$WORKER" >/dev/null
if [ -z "$started" ]; then
    echo "FAILED: worker did not start under litestream -exec (see logs above)." >&2
    exit 1
fi

echo
echo "All checks passed."
