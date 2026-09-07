#!/usr/bin/env bash
# Safe, auditable Advisor-only deployment to the Linux research host.
set -euo pipefail

HOST=${1:-varmak@192.168.3.36}
HOST_ADDR=${HOST#*@}
REMOTE=${ADVISOR_REMOTE_REPO:-/home/varmak/stockstest}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
COMMIT=$(git -C "$ROOT" rev-parse --short=12 HEAD)
DIRTY=false
if [[ -n "$(git -C "$ROOT" status --porcelain -- advisor)" ]]; then DIRTY=true; fi
TREE_HASH=$(PYTHONPATH="$ROOT" python3 -m advisor.release_integrity --root "$ROOT/advisor")
RELEASE_ID="${STAMP}-${COMMIT}-${TREE_HASH:0:12}"
ARCHIVE="~/.advisor_releases/advisor-$STAMP.tgz"
MANIFEST_BACKUP="~/.advisor_releases/deployment_manifest-$STAMP.json"
UNIT_BACKUP="~/.advisor_releases/units-$STAMP"
STAGE="~/.advisor_staging/$RELEASE_ID"
MUTATED=0
REMOTE_HASH=$(ssh "$HOST" "cd '$REMOTE' && .venv/bin/python -m advisor.release_integrity")

rollback() {
  rc=$?
  if [[ "$MUTATED" == 1 ]]; then
    echo "[deploy] FAILED — restoring previous advisor release" >&2
    ssh "$HOST" "set -e; tmp=\$(mktemp -d); tar -xzf $ARCHIVE -C \$tmp; \
      rsync -a --delete --exclude=data/ --exclude=logs/ \
      \$tmp/advisor/ '$REMOTE/advisor/'; \
      rsync -a $UNIT_BACKUP/ ~/.config/systemd/user/; \
      test ! -f $MANIFEST_BACKUP || cp $MANIFEST_BACKUP \
        '$REMOTE/advisor/data/deployment_manifest.json'; \
      systemctl --user daemon-reload; \
      systemctl --user restart advisor-terminal.service advisor-quoted.service advisor-exitwatch.service; \
      rm -rf \$tmp" || true
  fi
  exit "$rc"
}
trap rollback ERR

echo "[deploy] backup current advisor code"
ssh "$HOST" "set -e; mkdir -p ~/.advisor_releases && tar -C '$REMOTE' \
  --exclude='advisor/data' --exclude='advisor/logs' --exclude='advisor/__pycache__' \
  -czf $ARCHIVE advisor; \
  if test -f '$REMOTE/advisor/data/deployment_manifest.json'; then \
    cp '$REMOTE/advisor/data/deployment_manifest.json' $MANIFEST_BACKUP; fi; \
  mkdir -p $UNIT_BACKUP; \
  for unit in ~/.config/systemd/user/advisor-*; do \
    test ! -f \$unit || cp \$unit $UNIT_BACKUP/; done"

echo "[deploy] stage candidate release"
ssh "$HOST" "rm -rf $STAGE && mkdir -p $STAGE/advisor"
rsync -a --delete-delay --delay-updates \
  --exclude data/ --exclude logs/ --exclude '__pycache__/' --exclude '.pytest_cache/' \
  "$ROOT/advisor/" "$HOST:$STAGE/advisor/"

echo "[deploy] validate candidate outside the live tree"
ssh "$HOST" "cd $STAGE && PYTHONPATH=$STAGE \
  '$REMOTE/.venv/bin/python' -m pytest -q advisor/tests && \
  PYTHONPATH=$STAGE '$REMOTE/.venv/bin/python' -m compileall -q advisor"

echo "[deploy] promote code without touching runtime data or logs"
# Refuse a concurrent editor/deployer rather than overwriting unseen work.
CURRENT_REMOTE_HASH=$(ssh "$HOST" "cd '$REMOTE' && .venv/bin/python -m advisor.release_integrity")
if [[ "$CURRENT_REMOTE_HASH" != "$REMOTE_HASH" ]]; then
  echo "[deploy] remote code changed during staging; refusing promotion" >&2
  exit 1
fi
if ssh "$HOST" "flock -n -E 75 /tmp/advisor-pipeline.lock rsync -a --delete-delay --delay-updates \
  --exclude data/ --exclude logs/ --exclude '__pycache__/' --exclude '.pytest_cache/' \
  $STAGE/advisor/ '$REMOTE/advisor/'"; then
  MUTATED=1
else
  rc=$?
  # Lock contention changed nothing: do not roll back an active pipeline.
  if [[ "$rc" != 75 ]]; then MUTATED=1; fi
  false
fi

echo "[deploy] install hardened systemd units"
ssh "$HOST" "cd '$REMOTE' && .venv/bin/python -m advisor.ops.provision_remote \
  --repo '$REMOTE' --bind '$HOST_ADDR' --replace-services"

echo "[deploy] write release manifest"
ssh "$HOST" "RELEASE_ID='$RELEASE_ID' COMMIT='$COMMIT' TREE_HASH='$TREE_HASH' DIRTY='$DIRTY' \
  REMOTE='$REMOTE' '$REMOTE/.venv/bin/python' -c \"import json,os,datetime,pathlib; p=pathlib.Path(os.environ['REMOTE'])/'advisor/data/deployment_manifest.json'; d={'schema_version':1,'release_id':os.environ['RELEASE_ID'],'commit':os.environ['COMMIT'],'source_worktree_dirty':os.environ['DIRTY']=='true','advisor_tree_sha256':os.environ['TREE_HASH'],'deployed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'deployment_kind':'advisor-code-rsync','rollback_archive':'~/.advisor_releases/advisor-$STAMP.tgz'}; p.parent.mkdir(parents=True,exist_ok=True); q=p.with_suffix('.json.tmp'); q.write_text(json.dumps(d,indent=2)+'\\n'); os.replace(q,p)\""

echo "[deploy] reload and restart reader services"
ssh "$HOST" "systemctl --user daemon-reload && \
  systemctl --user reset-failed advisor-terminal.service advisor-quoted.service advisor-exitwatch.service && \
  systemctl --user restart advisor-terminal.service advisor-quoted.service advisor-exitwatch.service && \
  systemctl --user start advisor-watchdog.service && \
  for i in \$(seq 1 20); do \
    systemctl --user is-active --quiet advisor-terminal.service \
      advisor-quoted.service advisor-exitwatch.service && \
    curl -fsS 'http://$HOST_ADDR:8505/_stcore/health' >/dev/null && exit 0; \
    sleep 0.5; \
  done; exit 1"

# Verify content, not merely that the HTTP listener exists.
DEPLOYED_HASH=$(ssh "$HOST" "cd '$REMOTE' && .venv/bin/python -m advisor.release_integrity")
if [[ "$DEPLOYED_HASH" != "$TREE_HASH" ]]; then
  echo "[deploy] deployed content differs from tested release" >&2
  false
fi
MUTATED=0
trap - ERR
ssh "$HOST" "rm -rf $STAGE"
echo "[deploy] release $RELEASE_ID"
