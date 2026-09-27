#!/usr/bin/env bash
# Development: runs the web server and the moderation worker together, each line of output prefixed [web] / [worker].
# Ctrl-C, or either process exiting, stops the other one too.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "dev.sh: $PY not found; create the virtual environment first." >&2
    exit 1
fi

if [ "${1:-}" = "--live" ]; then
    # Real API calls, for this run only (config/tunables.py is unchanged). The budget caps and breaker still apply.
    read -r -p "This will make real Anthropic API calls (spend caps still apply). Type yes to continue: " answer
    if [ "$answer" != "yes" ]; then
        echo "dev.sh: not confirmed, nothing started." >&2
        exit 1
    fi
    export DJANGO_SETTINGS_MODULE=config.settings_live
    echo "LIVE: the moderator will call the API for this run only."
else
    echo "LLM_ENABLED is False in config/tunables.py: moderation runs will be recorded as skipped_disabled until it is turned on."
fi

# Job control gives each background job its own process group, so stopping one also stops the runserver reloader child.
set -m
export PYTHONUNBUFFERED=1

prefix() {
    local tag="$1" line
    while IFS= read -r line || [ -n "$line" ]; do
        printf '[%s] %s\n' "$tag" "$line"
    done
}

"$PY" manage.py runserver > >(prefix web) 2>&1 &
WEB_PID=$!
"$PY" manage.py run_moderator > >(prefix worker) 2>&1 &
WORKER_PID=$!

stop_all() {
    trap - INT TERM EXIT
    kill -TERM -- "-$WEB_PID" "-$WORKER_PID" 2>/dev/null || true
    wait "$WEB_PID" "$WORKER_PID" 2>/dev/null || true
}
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM
trap 'stop_all' EXIT

# Returns as soon as either process ends (bash 4.3+); its status becomes ours.
status=0
wait -n "$WEB_PID" "$WORKER_PID" || status=$?
stop_all
exit "$status"
