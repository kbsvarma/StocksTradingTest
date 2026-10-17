#!/usr/bin/env python3
"""One-shot backfill of the corrections ledger (2026-07-17).

Formally voids the records already annotated in events.log so net P&L
queries are honest without editing the append-only trades ledger.
Idempotent: refuses to run twice (checks for its own marker note).
"""
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from process_lock import ProcessLock  # noqa: E402
from state_db import StateDB, release_provenance  # noqa: E402

MARKER = "backfill-2026-07-17"


def main():
    provenance = release_provenance()
    if (provenance["tree_dirty"] or not provenance["code_commit"] or
            not provenance["config_sha256"]):
        raise RuntimeError(
            "backfill refused from dirty or unverifiable release")
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    state_dir = BASE / cfg["ops"]["state_dir"]
    with ProcessLock(state_dir / "trading.lock", "correction-backfill"):
        db = StateDB(state_dir, venue=cfg["executor"]["venue"],
                     run_kind="correction-backfill")
        try:
            identity = db.identity()
            if identity:
                db.bind_identity(
                    identity["account"], identity["venue"],
                    generation=identity["generation"])
            n = db.con.execute(
                "SELECT COUNT(*) FROM corrections WHERE note LIKE ?",
                (f"%{MARKER}%",)).fetchone()[0]
            if n:
                print("already backfilled — nothing to do")
                return
            rows = [
                ("void_trade", "trades.csv 2026-07-10 GC +$54.00", -54.00,
                 f"[{MARKER}] PHANTOM: IBKR paper nightly reset (07-09 "
                 "21:11Z) wiped the position; the surviving GTC stop fired "
                 "on a flat book. Trade never existed. Window 07-09 21:11Z "
                 "-> 07-10 EOD excluded."),
                ("void_trade", "trades.csv 2026-07-10 YM +$61.50", -61.50,
                 f"[{MARKER}] PHANTOM: same nightly-reset incident — stop "
                 "fired flat, created a short covered by the 17:00Z entry. "
                 "Trade never existed."),
                ("fee_adjust", "trades.csv rows 1,4 commissions", -2.83,
                 f"[{MARKER}] under-recorded commissions: trade 1 (GC "
                 "orphan-adopted) logged one leg only ($0.97), trade 4 (YM "
                 "post-disconnect) logged entry leg only ($1.86); ~$2.83 of "
                 "fees missing from the ledger."),
            ]
            with db.con:
                db.con.executemany(
                    "INSERT INTO corrections "
                    "(ts,kind,ref,delta_usd,note) "
                    "VALUES (datetime('now'),?,?,?,?)", rows)
            print("backfilled 3 corrections; net effect on recorded P&L: "
                  f"{-54.00 - 61.50 - 2.83:+.2f}")
            print("v_net_pnl now:", db.net_pnl())
        finally:
            db.close()


if __name__ == "__main__":
    main()
