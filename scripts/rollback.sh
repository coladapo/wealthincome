#!/bin/bash
# rollback.sh — put backend/ and core/ back to an earlier git ref.
#
# Usage:
#   scripts/rollback.sh            # roll back to the previous commit (HEAD~1)
#   scripts/rollback.sh <ref>      # any commit / tag / branch
#   scripts/rollback.sh <ref> --force   # proceed even with uncommitted edits in backend/ or core/
#
# What it does:
#   1. Refuses if backend/ or core/ has uncommitted changes (they would be lost)
#      unless --force. Either way it first tars the current backend/ + core/ into
#      logs/rollback-<timestamp>.tar.gz so the rollback itself can be undone.
#   2. `git checkout <ref> -- backend core` — overwrites those two trees with the
#      ref's versions (files are staged; nothing is committed). Files added after
#      <ref> are listed but NOT deleted (the old code never imports them).
#   3. Never touches data/ (ledger DB, caches), .env, launchd/ or anything else.
#   4. Prints the exact launchctl commands to restart the services. It does NOT
#      run them — a human (or the boss session) restarts deliberately.
#
# To undo a rollback: `git checkout HEAD -- backend core` (or untar the backup).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:-HEAD~1}"
FORCE="${2:-}"
cd "$ROOT"

if ! git rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
  echo "rollback: '$REF' is not a commit in $ROOT" >&2
  exit 2
fi
TARGET="$(git rev-parse --short "${REF}^{commit}")"

DIRTY="$(git status --porcelain -- backend core)"
if [[ -n "$DIRTY" && "$FORCE" != "--force" ]]; then
  echo "rollback: backend/ or core/ has uncommitted changes — commit them or re-run with --force:" >&2
  echo "$DIRTY" >&2
  exit 3
fi

mkdir -p logs
BACKUP="logs/rollback-$(date +%Y%m%d-%H%M%S).tar.gz"
tar --exclude='__pycache__' -czf "$BACKUP" backend core
echo "Backed up current backend/ + core/ to $BACKUP"

ADDED="$(git diff --name-only --diff-filter=A "$TARGET" -- backend core || true)"
git checkout "$TARGET" -- backend core
find backend core -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "Rolled backend/ and core/ back to $TARGET ($(git log -1 --format='%s' "$TARGET"))."
echo "data/ and .env were not touched."
if [[ -n "$ADDED" ]]; then
  echo "Files added after $TARGET were left in place (unused by the old code):"
  echo "$ADDED" | sed 's/^/  /'
fi

UID_NUM="$(id -u)"
echo
echo "Not restarted. To load the rolled-back code, run:"
echo "  launchctl kickstart -k gui/${UID_NUM}/com.wealthincome.trader"
echo "  launchctl kickstart -k gui/${UID_NUM}/com.wealthincome.api"
echo "(digest / brainhealth / healthmonitor are periodic jobs and pick it up on their next run.)"
