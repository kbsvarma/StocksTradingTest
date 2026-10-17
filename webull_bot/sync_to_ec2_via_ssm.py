"""Push live-bot data files Mac → EC2 via SSM RunCommand.

Replaces the flaky rsync-over-SSH job. Pushes the small JSON files the
dashboard cares about (heartbeat, state) every cycle. trades.csv is
pushed too but on a slower cadence since it changes once per trade.

Why SSM instead of rsync:
  Comcast → AWS routing flakes the SSH window ~half the time, leaving
  rsync timing out for 20+ minutes at a stretch. SSM agent uses outbound
  HTTPS from the instance — works whenever the instance is online.

Run mode: invoked every 60s by launchd. Single-shot — no internal loop
so launchd handles cadence.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

INSTANCE_ID = "i-0707e83b14bb40051"
REGION = "us-east-1"
REMOTE_DIR = "/opt/mac_dashboard/spx_spread_bot/data/webull_live/vG_spx_w50_vix25"
DATA_DIR = Path(
    "/Users/varmakammili/Documents/GitHub/StocksTradingTest/"
    "spx_spread_bot/data/webull_live/vG_spx_w50_vix25"
)

# Files to push. Limit to small ones to keep SSM payload modest.
FILES = ["heartbeat.json", "state.json", "trades.csv"]


def _build_chunked_commands(local: Path, remote: str) -> list[str]:
    """Build SSM commands that decode base64 and atomically replace the file."""
    if not local.exists():
        return []
    b64 = base64.b64encode(local.read_bytes()).decode()
    CHUNK = 1500
    chunks = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    staging = f"/tmp/sync_{local.name}.b64"
    decoded = f"/tmp/sync_{local.name}"
    cmds = [f"rm -f {staging} {decoded}"]
    for c in chunks:
        cmds.append(f"printf %s '{c}' >> {staging}")
    cmds += [
        f"base64 -d {staging} > {decoded}",
        f"sudo mkdir -p {REMOTE_DIR}",
        # Atomic install: decoded full file is moved into place, dashboard
        # never sees a half-written file.
        f"sudo mv {decoded} {remote}",
        f"sudo chown webullbot:webullbot {remote}",
        f"rm -f {staging}",
    ]
    return cmds


def main() -> int:
    all_cmds: list[str] = []
    pushed: list[str] = []
    for fname in FILES:
        local = DATA_DIR / fname
        remote = f"{REMOTE_DIR}/{fname}"
        cmds = _build_chunked_commands(local, remote)
        if cmds:
            all_cmds.extend(cmds)
            pushed.append(fname)

    if not all_cmds:
        print("[sync] no files to push", flush=True)
        return 0

    payload = {"commands": all_cmds}
    payload_path = "/tmp/sync_to_ec2_payload.json"
    Path(payload_path).write_text(json.dumps(payload))

    try:
        send = subprocess.run(
            ["aws", "ssm", "send-command",
             "--region", REGION,
             "--instance-ids", INSTANCE_ID,
             "--document-name", "AWS-RunShellScript",
             "--parameters", f"file://{payload_path}",
             "--query", "Command.CommandId", "--output", "text"],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        print("[sync] aws ssm send-command timed out", flush=True)
        return 2

    if send.returncode != 0:
        print(f"[sync] aws send-command failed: {send.stderr.strip()}", flush=True)
        return 3

    cmd_id = send.stdout.strip()
    print(f"[sync] pushed {pushed} via SSM cmd_id={cmd_id[:12]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
