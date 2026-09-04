#!/usr/bin/env bash
# Deploy the advisor to production and leave BOTH trees describable.
#
# Production was an unversioned rsync copy: no way to establish what code
# produced any historical artifact, no rollback, no audit trail. Now it is a
# git repo, and this script keeps it that way — rsync alone would leave the
# prod tree permanently dirty, which makes `git_dirty` on every artifact
# meaningless.
#
# Usage: advisor/ops/deploy.sh [host]
set -euo pipefail
HOST="${1:-varmak@192.168.3.36}"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
SHA="$(git -C "$SRC" rev-parse HEAD)"

if [ -n "$(git -C "$SRC" status --porcelain advisor)" ]; then
  echo "refusing: source advisor/ tree is dirty — commit first so the deploy" >&2
  echo "          stamp names a real commit" >&2
  exit 1
fi

rsync -az --delete --no-perms --no-owner --no-group \
  --exclude='data/' --exclude='logs/' --exclude='__pycache__/' \
  --exclude='*.pyc' \
  "$SRC/advisor/" "$HOST:~/stockstest/advisor/"

ssh "$HOST" "bash -s $SHA" <<'REMOTE'
set -euo pipefail
SHA="$1"
cd ~/stockstest
echo "$SHA" > .deployed_from
git add -A
if [ -n "$(git status --porcelain)" ]; then
  git commit -q -m "deploy from source $SHA"
fi
echo "prod HEAD: $(git rev-parse --short HEAD)  dirty: $(git status --porcelain | wc -l)"
.venv/bin/python -m pytest advisor/tests -q 2>&1 | tail -1
REMOTE
echo "deployed $SHA"
