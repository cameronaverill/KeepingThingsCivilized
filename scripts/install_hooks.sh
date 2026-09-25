#!/bin/sh
# Turns on the secret-scanning git hooks in .githooks/ for this clone only (a repo-local git setting).
set -e
cd "$(dirname "$0")/.."
git config --local core.hooksPath .githooks
if [ -d .githooks ]; then
    chmod +x .githooks/* scripts/check_secrets.py 2>/dev/null || true
fi
echo "Git hooks enabled for this clone: core.hooksPath = .githooks (pre-commit and pre-push secret scans)."
