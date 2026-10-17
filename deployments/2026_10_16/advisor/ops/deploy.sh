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
# All callers use the staged, health-checked, rollback-capable deployment.
exec bash "$(dirname "${BASH_SOURCE[0]}")/deploy_linux.sh" "$@"
