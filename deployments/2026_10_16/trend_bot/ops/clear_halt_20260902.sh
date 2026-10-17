#!/bin/bash
# One-shot halt clear — staged 2026-09-02 after verifying broker flat.
# Halt cause: 2026-08-27 "NQ entry state unresolved" (self-resolved 18:00Z
# same day via ADOPTED crash-window entry; NQ stopped out clean 08-28 13:35Z).
set -euo pipefail
cd /home/varmak/trendbot/trend_bot
PY=.venv/bin/python

echo "[1/5] re-verifying broker is flat (read-only, clientId 78)…"
set -a; source /home/varmak/.trendbot_env; set +a
$PY - <<PYEOF
from ib_insync import IB
import sys
ib=IB(); ib.connect("127.0.0.1",4002,clientId=78,timeout=20,readonly=True)
pos=[p for p in ib.positions() if p.position]; oo=ib.reqAllOpenOrders(); ib.sleep(2)
print(f"      positions={len(pos)} open_orders={len(oo)}")
ib.disconnect()
if pos or oo:
    print("      ABORT: broker not flat — investigate before clearing"); sys.exit(1)
print("      broker flat — safe to clear")
PYEOF

echo "[2/5] stopping trendbot.service…"
systemctl stop trendbot.service; sleep 3

echo "[3/5] backup + clear halt flags…"
TS=$(date +%Y%m%d%H%M%S)
cp state/state.json state/state.json.bak-halt-clear-$TS
$PY - <<PYEOF
import json
p="state/state.json"; d=json.load(open(p))
print("      before: halt_entries=%s reason=%s" % (d.get("halt_entries"), d.get("halt_reason")))
d["halt_entries"]=False; d["halt_reason"]=None
json.dump(d, open(p,"w"), indent=2)
print("      after : halt_entries=False reason=None")
PYEOF

echo "[4/5] starting trendbot.service…"
systemctl start trendbot.service; sleep 8

echo "[5/5] verify…"
systemctl is-active trendbot.service
$PY -c "import json;d=json.load(open(\"state/state.json\"));print(\"      halt_entries:\",d.get(\"halt_entries\"),\"| heartbeat:\",open(\"state/heartbeat\").read().strip())"
echo "DONE — entries re-enabled; next hourly wake will evaluate entries."
