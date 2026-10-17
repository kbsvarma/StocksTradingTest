#!/usr/bin/env python3
"""Record one validated manual P&L correction while the bot is stopped."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from process_lock import ProcessLock  # noqa: E402
from state_db import StateDB, release_provenance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append a referenced correction to the durable ledger")
    parser.add_argument("kind", choices=("emergency_pnl", "basis_pnl"))
    parser.add_argument("event_key")
    parser.add_argument("delta_usd", type=float,
                        help="gross P&L before already-journaled commissions")
    parser.add_argument("--note", required=True,
                        help="external evidence and calculation summary")
    parser.add_argument("--confirm-event", required=True,
                        help="must exactly repeat event_key")
    args = parser.parse_args()
    if args.confirm_event != args.event_key:
        parser.error("--confirm-event must exactly repeat event_key")

    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    provenance = release_provenance()
    if (provenance["tree_dirty"] or not provenance["code_commit"] or
            not provenance["config_sha256"]):
        raise RuntimeError(
            "correction refused from dirty or unverifiable release")
    state_dir = BASE / cfg["ops"]["state_dir"]
    with ProcessLock(state_dir / "trading.lock", "manual-correction"):
        db = StateDB(state_dir, venue=cfg["executor"]["venue"],
                     run_kind="manual-correction")
        try:
            identity = db.identity()
            if identity is None:
                raise RuntimeError(
                    "runtime identity is unbound; correction refused")
            db.bind_identity(
                identity["account"], identity["venue"],
                generation=identity["generation"])
            db.correction(
                args.kind, args.event_key, args.delta_usd, args.note)
            print(f"recorded {args.kind} for {args.event_key}; "
                  f"authoritative net P&L={db.net_pnl():+.2f}")
        finally:
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
