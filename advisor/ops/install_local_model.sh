#!/usr/bin/env bash
# Rootless, pinned inference runtime. Safe to rerun on a replacement Linux x86_64 host.
set -euo pipefail
MODEL="${1:-qwen3.5:9b}"
case "$MODEL" in
  qwen3.5:9b) MODEL_SHA=6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7 ;;
  gemma4:12b) MODEL_SHA=4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c ;;
  qwen3.8:27b) MODEL_SHA=22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643 ;;
  *) echo 'Supported pinned models: qwen3.5:9b, gemma4:12b, qwen3.8:27b'; exit 1 ;;
esac
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'Linux x86_64 required'; exit 1; }
VERSION=0.33.3
SHA256=c13cea8f3389db4145f8a6cb88d1747242a48639d7c13e3bda7c1ebdc6eebb2f
BASE="${ADVISOR_MODEL_HOME:-$HOME/.local/share/advisor/ollama}"
mkdir -p "$BASE/releases" "$BASE/models" "$HOME/.config/systemd/user"
chmod 700 "$BASE"
if [[ ! -f "$BASE/releases/$VERSION/.verified" ]]; then
  curl --fail --location --retry 3 --continue-at - \
    "https://github.com/ollama/ollama/releases/download/v$VERSION/ollama-linux-amd64.tar.zst" -o "$BASE/runtime.tar.zst"
  printf '%s  %s\n' "$SHA256" "$BASE/runtime.tar.zst" | sha256sum --check
  mkdir -p "$BASE/releases/$VERSION"
  tar --zstd -xf "$BASE/runtime.tar.zst" -C "$BASE/releases/$VERSION"
  touch "$BASE/releases/$VERSION/.verified"
  rm "$BASE/runtime.tar.zst"
fi
ln -sfn "$BASE/releases/$VERSION" "$BASE/current"
cat > "$HOME/.config/systemd/user/advisor-model.service" <<EOF
[Unit]
Description=Advisor private local research model
After=network-online.target
[Service]
ExecStart="$BASE/current/bin/ollama" serve
Environment="OLLAMA_MODELS=$BASE/models"
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_NO_CLOUD=true
Environment=OLLAMA_VULKAN=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_QUEUE=2
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_KEEP_ALIVE=30m
Environment=OLLAMA_CONTEXT_LENGTH=32768
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
Environment=OLLAMA_GPU_OVERHEAD=2147483648
Environment=LLAMA_ARG_CACHE_RAM=0
Environment=LLAMA_ARG_CTX_CHECKPOINTS=0
EnvironmentFile=-$HOME/.advisor_model.env
Restart=on-failure
RestartSec=5
Nice=10
CPUQuota=1200%
MemoryHigh=24G
MemoryMax=32G
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable advisor-model.service
systemctl --user restart advisor-model.service
for attempt in {1..30}; do
  curl -fsS http://127.0.0.1:11434/api/version >/dev/null && break
  sleep 1
done
"$BASE/current/bin/ollama" pull "$MODEL"
curl -fsS http://127.0.0.1:11434/api/tags > "$BASE/model-manifest.json"
python3 - "$BASE/model-manifest.json" "$MODEL" "$MODEL_SHA" <<'PYMODEL'
import json,sys
model,expected=sys.argv[2:4]
models=json.load(open(sys.argv[1]))['models']
assert any(m['name']==model and m['digest']==expected for m in models), 'Model tag changed; review the new digest before using it'
PYMODEL
echo 'Local model installed. Configuration and intelligence validation are separate steps.'
