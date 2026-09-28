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
APP=tt-verify-app
PORT=18080

cleanup() {
    docker rm -f "$APP" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

secret_key() { python3 -c 'import secrets; print(secrets.token_urlsafe(50))'; }

echo "== 1/4: docker build =="
docker build -t "$IMAGE" .

echo "== 2/4: manage.py check --deploy =="
docker run --rm \
    -e DJANGO_ENV=production \
    -e DJANGO_SECRET_KEY="$(secret_key)" \
    -e DJANGO_ALLOWED_HOSTS=localhost \
    -e DJANGO_DB_PATH=/tmp/check.sqlite3 \
    "$IMAGE" python manage.py check --deploy

echo "== 3/4: litestream is installed =="
docker run --rm "$IMAGE" litestream version

echo "== 4/4: the real combined command (scripts/fly_start.sh: migrate, then web + worker together), against a"
echo "        throwaway volume standing in for Fly's /data -- this is what actually deploys as the one 'web'"
echo "        process group (fly.toml). Fake B2 credentials: this only proves litestream starts and still runs the"
echo "        wrapped worker command underneath it, not that it can reach B2 (a real bucket/keys are a separate,"
echo "        later check once you've created them)."
docker volume create "$VOLUME" >/dev/null
docker run -d --name "$APP" -p "$PORT":8080 \
    -e DJANGO_ENV=production -e DJANGO_SECRET_KEY="$(secret_key)" \
    -e DJANGO_ALLOWED_HOSTS=localhost -e DJANGO_DB_PATH=/data/db.sqlite3 \
    -e LITESTREAM_B2_BUCKET=test -e LITESTREAM_B2_ENDPOINT=test.example.com -e LITESTREAM_B2_REGION=test \
    -e LITESTREAM_ACCESS_KEY_ID=test -e LITESTREAM_SECRET_ACCESS_KEY=test \
    -v "$VOLUME":/data \
    "$IMAGE" bash scripts/fly_start.sh \
    >/dev/null

status=000
worker_started=""
for _ in $(seq 1 15); do
    status="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/accounts/login/" || echo 000)"
    logs="$(docker logs "$APP" 2>&1)"
    grep -q "Worker started" <<<"$logs" && worker_started=1
    [ "$status" = "200" ] && [ -n "$worker_started" ] && break
    sleep 1
done
echo "GET /accounts/login/ -> $status"
echo "$logs"
docker rm -f "$APP" >/dev/null
if [ "$status" != "200" ]; then
    echo "FAILED: web never returned 200 (see logs above)." >&2
    exit 1
fi
if [ -z "$worker_started" ]; then
    echo "FAILED: worker did not start under litestream -exec, in the same container as web (see logs above)." >&2
    exit 1
fi

echo
echo "All checks passed (both web and the worker came up together, in one container, sharing one volume)."
