#!/usr/bin/env python3
"""Start a durable qualification epoch after final-host broker verification."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from data import IBKRData  # noqa: E402
from executor import make_executor  # noqa: E402
from process_lock import ProcessLock  # noqa: E402
from reconcile import _db_invariants  # noqa: E402
from state_db import StateDB, release_provenance  # noqa: E402
from state_store import StateStore  # noqa: E402


REQUIRED_READINESS = {
    "broker", "data", "protection", "reconciliation", "accounting",
    "provenance",
}


def assert_broker_quiescent(executor) -> None:
    """Require broker truth to be flat with no scoped account orders."""
    positions = {
        con_id: qty for con_id, qty in executor.positions().items()
        if float(qty) != 0
    }
    if positions:
        raise RuntimeError(f"broker account is not flat: {positions}")
    orders = executor.open_account_orders()
    if orders:
        public = [
            {key: value for key, value in order.items() if key != "trade"}
            for order in orders
        ]
        raise RuntimeError(
            f"live account orders remain at broker: {public}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the final-host qualification clock")
    parser.add_argument("--note", required=True,
                        help="review/freeze evidence reference")
    parser.add_argument("--confirm", required=True,
                        help="must be exactly START-SOAK")
    args = parser.parse_args()
    if args.confirm != "START-SOAK":
        parser.error("--confirm must be exactly START-SOAK")

    provenance = release_provenance()
    if (provenance["tree_dirty"] or not provenance["code_commit"] or
            not provenance["config_sha256"]):
        raise RuntimeError("soak start refused from dirty release")
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    state_dir = BASE / cfg["ops"]["state_dir"]
    noop_log = SimpleNamespace(log=lambda *a, **k: None)

    with ProcessLock(state_dir / "trading.lock", "soak-control"):
        st = StateStore(state_dir)
        db = StateDB(state_dir, venue=cfg["executor"]["venue"],
                     run_kind="soak-control")
        executor = data = None
        try:
            identity = db.identity()
            if identity is None or st.state.get("runtime_identity") != identity:
                raise RuntimeError(
                    "DB/state runtime identity must already be bound and equal")
            if identity["venue"] != cfg["executor"]["venue"]:
                raise RuntimeError("config venue differs from state identity")
            db.bind_identity(
                identity["account"], identity["venue"],
                generation=identity["generation"])

            runtime = db.latest_runtime_provenance()
            if runtime is None:
                raise RuntimeError("no candidate daemon runtime was recorded")
            expected_runtime = (
                provenance["code_commit"], provenance["config_sha256"],
                False, identity["account"], identity["venue"])
            actual_runtime = (
                runtime["code_commit"], runtime["config_sha256"],
                runtime["tree_dirty"], runtime["account"], runtime["venue"])
            if actual_runtime != expected_runtime:
                raise RuntimeError(
                    "latest daemon runtime is not this exact clean release: "
                    f"{actual_runtime}")

            if st.state.get("last_wake_status") != "ok":
                raise RuntimeError(
                    f"last candidate wake was not clean: "
                    f"{st.state.get('last_wake_status')}")
            last_wake = datetime.fromisoformat(st.state["last_wake"])
            age_s = (datetime.now(timezone.utc) - last_wake).total_seconds()
            if age_s < 0 or age_s > 900:
                raise RuntimeError(
                    f"last clean wake is stale ({age_s:.0f}s old)")
            readiness = db.readiness()
            failed = sorted(
                component for component in REQUIRED_READINESS
                if readiness.get(component, {}).get("status") != "OK")
            if failed:
                raise RuntimeError(
                    f"candidate readiness is not clean for {failed}")
            if any(p.get("position") or float(p.get("qty") or 0)
                   for p in st.state.get("positions", {}).values()):
                raise RuntimeError(
                    "JSON strategy positions must be flat before soak start")
            durable_blockers = db.soak_blockers()
            if durable_blockers:
                raise RuntimeError(
                    "durable soak blockers: " + "; ".join(durable_blockers))
            invariant_failures, _ = _db_invariants(db.path, st.state)
            allowed_epoch_failures = {
                "durable soak epoch has not been started",
                "runtime code/config changed after soak epoch",
            }
            invariant_failures = [
                failure for failure in invariant_failures
                if failure not in allowed_epoch_failures and not
                failure.startswith("durable soak epoch is invalidated:")]
            if invariant_failures:
                raise RuntimeError(
                    "nightly invariant blockers: "
                    + "; ".join(invariant_failures))

            executor = make_executor(
                cfg, alert=lambda message: None, dlog=noop_log)
            executor.connect()
            if (executor.account != identity["account"] or
                    executor.venue != identity["venue"]):
                raise RuntimeError("connected executor identity mismatch")
            assert_broker_quiescent(executor)

            ib = cfg["ibkr"]
            data = IBKRData(
                ib["host"], ib["data_port"], ib["data_client_id"],
                dlog=noop_log)
            data.connect()

            # The data-leg handshake can take seconds. Re-read broker truth
            # immediately before the durable commit so an order/position that
            # appeared during that interval cannot start a false-flat epoch.
            assert_broker_quiescent(executor)
            epoch = db.start_soak(args.note)
            st.state["soak_start"] = epoch["started"]
            st.state["soak_release_valid"] = True
            st.event(
                f"SOAK EPOCH {epoch['epoch_id']} STARTED on explicit gate; "
                f"commit={epoch['code_commit']}")
            st.save()
            print(
                f"soak epoch {epoch['epoch_id']} started at "
                f"{epoch['started']} on commit {epoch['code_commit']}")
        finally:
            if data is not None:
                data.disconnect()
            if executor is not None:
                executor.disconnect()
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
