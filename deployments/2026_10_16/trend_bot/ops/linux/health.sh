#!/bin/bash
# trend_bot remote health report — every line PASS/FAIL, exit 1 if any FAIL
FAIL=0
ok()   { printf "PASS  %s\n" "$1"; }
bad()  { printf "FAIL  %s\n" "$1"; FAIL=1; }
H=/home/varmak
TB=$H/trendbot/trend_bot

echo "== SYSTEM =="
up=$(awk '{print int($1/3600)}' /proc/uptime)
echo "info  uptime ${up}h | load $(cut -d' ' -f1-3 /proc/loadavg)"
d=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
[ "$d" -gt 10 ] && ok "disk ${d}G free" || bad "disk LOW ${d}G"
m=$(free -m | awk 'NR==2{print $7}')
[ "$m" -gt 2000 ] && ok "mem ${m}M available" || bad "mem LOW ${m}M"
timedatectl show -p NTPSynchronized --value | grep -q yes \
  && ok "clock NTP-synced" || bad "clock NOT synced"
[ -f /var/run/reboot-required ] && bad "REBOOT PENDING (kernel)" \
  || ok "no reboot pending"

echo "== UNITS =="
for u in trendbot-xvfb trendbot-ibc trendbot trendbot-dashboard; do
  systemctl is-active --quiet $u && ok "unit $u active" || bad "unit $u DOWN"
done
systemctl is-active --quiet trendbot-watchdog.timer \
  && ok "watchdog timer active (next: $(systemctl show trendbot-watchdog.timer -p NextElapseUSecRealtime --value | cut -d' ' -f1-2))" \
  || bad "watchdog timer DOWN"

echo "== GATEWAY =="
ss -ltn | grep -q ':4002' && ok "port 4002 listening" || bad "4002 NOT listening"
grep -q "Login has completed" $H/trendbot/logs/ibc.log 2>/dev/null \
  && ok "IBC login completed ($(grep 'Login has completed' $H/trendbot/logs/ibc.log | tail -1 | cut -d' ' -f1-2))" \
  || bad "no IBC login record"

echo "== BOT =="
if [ -f $TB/state/heartbeat ]; then
  age=$(( $(date +%s) - $(stat -c %Y $TB/state/heartbeat) ))
  [ $age -lt 360 ] && ok "heartbeat fresh (${age}s)" || bad "heartbeat STALE (${age}s)"
else bad "heartbeat file missing"; fi
$TB/.venv/bin/python - <<'PY'
import json, sys
s = json.load(open("/home/varmak/trendbot/trend_bot/state/state.json"))
print(("FAIL  halt_entries LATCHED: " + str(s.get("halt_reason"))) if s.get("halt_entries")
      else "PASS  no entry halt")
print("FAIL  unresolved intents: " + str(s["order_intents"]) if s.get("order_intents")
      else "PASS  no unresolved intents")
ws = s.get("last_wake_status", "?")
print(("PASS  last wake: " + ws) if ws == "ok" else ("FAIL  last wake: " + ws))
PY
$TB/.venv/bin/python - <<'PY' 2>/dev/null
import sqlite3
con = sqlite3.connect("/home/varmak/trendbot/trend_bot/state/trendbot.db")
rows = dict(con.execute("SELECT component,status FROM readiness"))
bad = {k: v for k, v in rows.items() if v not in ("OK", "NOT_STARTED")}
print("FAIL  readiness: " + str(bad) if bad else "PASS  readiness all OK")
r = con.execute("SELECT code_commit, tree_dirty FROM runs ORDER BY started DESC LIMIT 1").fetchone()
print(f"PASS  running commit {r[0]} clean" if r and not r[1] else f"FAIL  dirty tree or no run row: {r}")
PY
grep -h "WAKE ERROR\|SATELLITE.*ERROR" $TB/state/events.log 2>/dev/null | tail -1 | \
  awk -v d="$(date -u -d '2 hours ago' +%Y-%m-%dT%H)" \
  '{ if (substr($1,1,13) >= d) print "FAIL  recent wake error: " $0; else print "PASS  no wake errors in last 2h" }'
[ -s "$TB/state/events.log" ] || echo "FAIL  events.log missing"

echo "== BROKER SMOKE TEST (read-only) =="
$TB/.venv/bin/python - <<'PY' 2>/dev/null
import json
from ib_insync import IB
ib = IB()
try:
    ib.connect("127.0.0.1", 4002, clientId=90, readonly=True, timeout=15)
except Exception as e:
    print("FAIL  broker probe cannot connect:", str(e)[:80]); raise SystemExit
ib.reqAllOpenOrders(); ib.sleep(2)
acct = {int(p.contract.conId): p.position for p in ib.positions() if p.position}
stops = {(t.contract.conId): t.order.auxPrice for t in ib.openTrades()
         if t.order.orderType == "STP" and t.order.action == "SELL"}
s = json.load(open("/home/varmak/trendbot/trend_bot/state/state.json"))
mm = []
seen = set()
for sym, p in s["positions"].items():
    if not p["position"]:
        continue
    cid = int(p["con_id"]); seen.add(cid)
    if acct.get(cid, 0) != p["qty"]:
        mm.append(f"{sym}: state {p['qty']} vs broker {acct.get(cid,0)}")
    elif cid not in stops:
        mm.append(f"{sym}: position has NO working stop at broker")
    else:
        print(f"PASS  {sym}: {p['qty']} lots protected, stop {stops[cid]}")
for cid, q in acct.items():
    if cid not in seen:
        mm.append(f"unmanaged broker position conId {cid} qty {q}")
print("FAIL  " + "; ".join(mm) if mm else "PASS  state == broker (positions & stops)")
ib.disconnect()
PY

echo "== DASHBOARD =="
curl -s -m 5 -o /dev/null -w "%{http_code}" http://localhost:8503 | grep -q 200 \
  && ok "dashboard :8503 responds" || bad "dashboard DOWN"
echo "=================================="
[ $FAIL -eq 0 ] && echo "OVERALL: HEALTHY" || echo "OVERALL: ATTENTION NEEDED"
exit $FAIL
