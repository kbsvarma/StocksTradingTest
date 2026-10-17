#!/usr/bin/env python3
"""Migrate historical trades.csv into the SQLite journal (2026-07-18).

Review finding #5: the journal held 0 trades while corrections sat on an
empty base and the risk rails queried no history. Idempotent (marker row).
The two phantom trades are migrated too — they are voided by the existing
corrections, keeping both ledgers reconcilable line-by-line.
"""
import csv
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from state_db import StateDB, release_provenance  # noqa: E402
from process_lock import ProcessLock  # noqa: E402

MARKER = "csv-migration-2026-07-18"


def migrate(state_dir: Path, venue: str):
    db = StateDB(state_dir, venue=venue, run_kind="csv-migration")
    try:
        identity = db.identity()
        if identity:
            db.bind_identity(
                identity["account"], identity["venue"],
                generation=identity["generation"])
        if db.con.execute(
                "SELECT COUNT(*) FROM corrections WHERE note LIKE ?",
                (f"%{MARKER}%",)).fetchone()[0]:
            print("already migrated")
            return
        # ONE transaction: rows + marker commit atomically — a mid-migration
        # crash then rerun can never duplicate a completed prefix (review)
        n = 0
        with open(state_dir / "trades.csv") as f:
            rows = list(csv.DictReader(f))
        with db.con:
            for row in rows:
                gross = float(row["pnl_usd"])
                comm = (float(row["commission"])
                        if row["commission"] else 0.0)
                db.con.execute(
                    "INSERT INTO trades (run_id, exit_time, book, sym, qty, "
                    "entry_time, entry_px, exit_px, reason, pnl_points, "
                    "pnl_gross, commission, pnl_net, entry_weight, "
                    "local_symbol) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (db.run_id, row["exit_time"], row["book"], row["sym"],
                     float(row["qty"]), row["entry_time"],
                     float(row["entry_px"]), float(row["exit_px"]),
                     row["reason"], float(row["pnl_points"]), gross, comm,
                     gross - comm, row["entry_weight"], row["local_symbol"]))
                n += 1
            db.con.execute(
                "INSERT INTO corrections (ts, kind, ref, delta_usd, note) "
                "VALUES (datetime('now'),'note','trades.csv',0.0,?)",
                (f"[{MARKER}] migrated {n} historical CSV trades into the "
                 "journal; phantoms remain voided by existing corrections",))
        print(f"migrated {n} trades; v_net_pnl = {db.net_pnl():+.2f}")
    finally:
        db.close()


def main():
    provenance = release_provenance()
    if (provenance["tree_dirty"] or not provenance["code_commit"] or
            not provenance["config_sha256"]):
        raise RuntimeError(
            "migration refused from dirty or unverifiable release")
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    state_dir = BASE / cfg["ops"]["state_dir"]
    with ProcessLock(state_dir / "trading.lock", "migration"):
        migrate(state_dir, cfg["executor"]["venue"])


if __name__ == "__main__":
    main()
