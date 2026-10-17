"""Fail-closed nightly replay of decisions, broker events, and FSM state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import strategy  # noqa: E402
from data import BarStore  # noqa: E402
from market_calendar import is_market_open  # noqa: E402
from rotation import missing_expected_hourly_bars  # noqa: E402
from state_store import EMPTY_POS  # noqa: E402


def reference_signals(B: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    frame = strategy.signal_frame(B, cfg)
    return pd.DataFrame({"signal": frame["signal"], "close": B["close"],
                         "donch": frame["donchian"], "atr": frame["atr"],
                         "atr_pct": frame["atr_pct"],
                         "thresh": frame["vol_thresh"]})


def _db_invariants(db_path: Path, state: dict) -> tuple[list[str], list[str]]:
    failures, info = [], []
    if not db_path.exists():
        return ["durable journal missing"], info
    con = sqlite3.connect(db_path)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            failures.append(f"SQLite integrity_check={integrity}")
        identity = con.execute(
            "SELECT venue,account,generation FROM runtime_identity "
            "WHERE singleton=1").fetchone()
        if identity is None:
            failures.append("runtime identity is unbound")
        elif not state.get("runtime_identity"):
            failures.append("state.json runtime identity is missing")
        else:
            si = state["runtime_identity"]
            if tuple(identity) != (si.get("venue"), si.get("account"),
                                   si.get("generation")):
                failures.append("DB/state runtime identity mismatch")
        if identity is not None:
            foreign = con.execute(
                "SELECT COUNT(*) FROM executions WHERE account IS NULL "
                "OR account<>?", (identity[1],)).fetchone()[0]
            if foreign:
                failures.append(
                    f"null/foreign-account executions in journal={foreign}")
            foreign_events = con.execute(
                "SELECT COUNT(*) FROM broker_events WHERE account IS NULL "
                "OR account<>? OR venue<>?", (identity[1], identity[0])
            ).fetchone()[0]
            if foreign_events:
                failures.append(
                    f"foreign-identity broker events={foreign_events}")
            foreign_fsm = con.execute(
                "SELECT COUNT(*) FROM position_fsm WHERE account<>? "
                "OR venue<>?", (identity[1], identity[0])).fetchone()[0]
            if foreign_fsm:
                failures.append(
                    f"foreign-identity FSM rows={foreign_fsm}")
            run = con.execute(
                "SELECT code_commit,config_sha256,tree_dirty,account,venue "
                "FROM runs WHERE run_kind='runtime' "
                "ORDER BY run_id DESC LIMIT 1").fetchone()
            try:
                head = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"], cwd=BASE,
                    capture_output=True, text=True, timeout=5,
                    check=True).stdout.strip()
                dirty = bool(subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=all"],
                    cwd=BASE, capture_output=True, text=True, timeout=5,
                    check=True).stdout.strip())
                cfg_sha = hashlib.sha256(
                    (BASE / "config.yaml").read_bytes()).hexdigest()[:16]
            except Exception as e:
                failures.append(f"release provenance check failed: {e}")
            else:
                if run is None:
                    failures.append("no runtime provenance row")
                else:
                    if run[0] != head:
                        failures.append(
                            f"running commit {run[0]} != checkout {head}")
                    if run[1] != cfg_sha:
                        failures.append("running config hash != checkout")
                    if run[2] != 0 or dirty:
                        failures.append("runtime or current worktree is dirty")
                    if (run[3], run[4]) != (identity[1], identity[0]):
                        failures.append("latest run venue/account mismatch")
            soak = con.execute(
                "SELECT s.started,s.account,s.venue,s.generation,"
                "s.code_commit,s.config_sha256,i.invalidated,i.reason "
                "FROM soak_epochs s LEFT JOIN soak_invalidations i "
                "ON i.epoch_id=s.epoch_id ORDER BY s.epoch_id DESC "
                "LIMIT 1").fetchone()
            if soak is None:
                failures.append("durable soak epoch has not been started")
            elif state.get("soak_start") != soak[0]:
                failures.append("DB/state soak epoch mismatch")
            elif tuple(soak[1:4]) != (identity[1], identity[0], identity[2]):
                failures.append("soak epoch runtime identity mismatch")
            elif run is not None and tuple(soak[4:6]) != tuple(run[0:2]):
                failures.append(
                    "runtime code/config changed after soak epoch")
            if soak is not None and soak[6] is not None:
                failures.append(
                    "durable soak epoch is invalidated: "
                    f"{soak[6]} {soak[7]}")

        pending = list(con.execute(
            "SELECT event_key,status FROM broker_events WHERE status NOT IN "
            "('COMMITTED','RESOLVED','CANCELLED','FLAT')"))
        if pending:
            failures.append(f"unresolved broker events={pending[:10]}")

        unapplied = list(con.execute(
            "SELECT e.exec_id,e.sym,e.side,e.qty FROM executions e "
            "LEFT JOIN applied_executions a ON a.account=e.account "
            "AND a.exec_id=e.exec_id WHERE e.exec_id IS NOT NULL "
            "AND a.exec_id IS NULL"))
        if unapplied:
            failures.append(f"unapplied executions={unapplied[:10]}")
        unidentified = list(con.execute(
            "SELECT id,sym,side,qty,transition_key FROM executions "
            "WHERE transition_key IS NOT NULL AND exec_id IS NULL"))
        if unidentified:
            failures.append(
                "transitioned executions without broker execId="
                f"{unidentified[:10]}")
        orphan_applied = list(con.execute(
            "SELECT a.account,a.exec_id,a.event_key FROM applied_executions a "
            "LEFT JOIN executions e ON e.account=a.account "
            "AND e.exec_id=a.exec_id WHERE e.exec_id IS NULL"))
        if orphan_applied:
            failures.append(
                f"applied markers without executions={orphan_applied[:10]}")
        orphan_commissions = list(con.execute(
            "SELECT c.account,c.exec_id,c.commission "
            "FROM commission_reports c LEFT JOIN executions e "
            "ON e.account=c.account AND e.exec_id=c.exec_id "
            "WHERE e.exec_id IS NULL"))
        if orphan_commissions:
            failures.append(
                "commission reports without executions="
                f"{orphan_commissions[:10]}")
        orphan_trade_execs = list(con.execute(
            "SELECT te.account,te.exec_id,te.trade_event_key "
            "FROM trade_executions te LEFT JOIN executions e "
            "ON e.account=te.account AND e.exec_id=te.exec_id "
            "LEFT JOIN trades t ON t.event_key=te.trade_event_key "
            "WHERE e.exec_id IS NULL OR t.id IS NULL"))
        if orphan_trade_execs:
            failures.append(
                "trade mappings without execution/trade="
                f"{orphan_trade_execs[:10]}")
        role_mismatches = list(con.execute(
            "SELECT te.exec_id,te.leg,e.side FROM trade_executions te "
            "JOIN executions e ON e.account=te.account "
            "AND e.exec_id=te.exec_id WHERE "
            "(te.leg='ENTRY' AND UPPER(e.side) NOT IN ('BUY','BOT')) OR "
            "(te.leg='EXIT' AND UPPER(e.side) NOT IN ('SELL','SLD'))"))
        if role_mismatches:
            failures.append(
                f"trade execution role mismatches={role_mismatches[:10]}")

        durable = {sym: json.loads(payload) for sym, payload in con.execute(
            "SELECT sym,payload FROM position_fsm")}
        projected = state.get("positions") or {}
        for sym in set(durable) | set(projected):
            d = {**EMPTY_POS, **durable.get(sym, {})}
            p = {**EMPTY_POS, **projected.get(sym, {})}
            d.pop("fsm_state", None)
            p.pop("fsm_state", None)
            differing = sorted(
                key for key in set(d) | set(p) if d.get(key) != p.get(key))
            if differing:
                failures.append(
                    f"{sym}: FSM/state projection mismatch fields="
                    f"{differing[:10]}")

        pending_comm = con.execute(
            "SELECT COUNT(*) FROM executions e LEFT JOIN commission_reports c "
            "ON c.account=e.account AND c.exec_id=e.exec_id "
            "WHERE e.transition_key IS NOT NULL AND e.exec_id IS NOT NULL "
            "AND c.exec_id IS NULL AND e.commission IS NULL").fetchone()[0]
        if pending_comm:
            failures.append(f"{pending_comm} applied executions lack commission")
        emergency_gaps = list(con.execute(
            "SELECT b.event_key FROM broker_events b WHERE "
            "b.event_type LIKE 'EMERGENCY_FLATTEN%' AND NOT EXISTS ("
            "SELECT 1 FROM corrections c WHERE c.ref=b.event_key "
            "AND c.kind='emergency_pnl')"))
        if emergency_gaps:
            failures.append(
                "emergency executions require manual P&L correction="
                f"{emergency_gaps[:10]}")
        corrected_refs = {r[0] for r in con.execute(
            "SELECT ref FROM corrections WHERE kind='basis_pnl' "
            "AND ref IS NOT NULL")}
        basis_gaps = []
        for event_key, payload_text in con.execute(
                "SELECT event_key,payload FROM broker_events"):
            payload = json.loads(payload_text or "{}")
            unbased = float(payload.get("unbased_qty") or 0)
            broker_closed = float(payload.get("broker_closed") or 0)
            booked = payload.get("booked_qty")
            if (unbased > 0 or
                    (booked is not None and broker_closed > float(booked))):
                if event_key not in corrected_refs:
                    basis_gaps.append(event_key)
        if basis_gaps:
            failures.append(
                "broker closes exceeded tracked P&L basis; correction "
                f"required={basis_gaps[:10]}")
        info.append(f"durable events={con.execute('SELECT COUNT(*) FROM broker_events').fetchone()[0]}")
        info.append(f"executions={con.execute('SELECT COUNT(*) FROM executions').fetchone()[0]}")
    except (sqlite3.Error, TypeError, ValueError) as e:
        failures.append(f"journal schema/data failure: {e}")
    finally:
        con.close()
    return failures, info


def build_report(cfg: dict, sdir: Path) -> tuple[str, bool]:
    scfg = cfg["strategy"]
    store = BarStore(sdir)
    state_path = sdir / "state.json"
    if not state_path.exists():
        return "VERDICT: FAILED\nstate.json missing", False
    state = json.loads(state_path.read_text())
    if not state.get("soak_start"):
        return "VERDICT: FAILED\nsoak_start missing — qualification clock " \
            "has not been explicitly authorized", False
    start = pd.Timestamp(state["soak_start"])
    decisions_path = sdir / "decisions.csv"
    decisions = (pd.read_csv(decisions_path)
                 if decisions_path.exists() else pd.DataFrame())
    failures, lines = [], [
        f"reconcile report — soak_start={start}  "
        f"generated={pd.Timestamp.now(tz='UTC')}"]
    if decisions.empty:
        failures.append("decisions.csv missing or empty")
    else:
        decisions["bar"] = pd.to_datetime(decisions["bar"], utc=True)
        decisions = decisions[decisions["bar"] >= start.floor("h")].copy()
        if decisions.empty:
            failures.append("zero decisions in the active soak epoch")
        dup = decisions.duplicated(["sym", "bar"], keep=False)
        if dup.any():
            failures.append(
                f"duplicate decision rows={int(dup.sum())}")

    all_syms = {**cfg["core_instruments"], **cfg["sleeve_instruments"]}
    for sym in all_syms:
        stored = store.load(sym)
        if stored.empty:
            failures.append(f"{sym}: no stored bars")
            continue
        complete = stored[stored.index < pd.Timestamp.now(tz="UTC").floor("h")]
        live = complete[(complete.index >= start.floor("h")) &
                        (complete.get("source") == "ibkr")]
        history_gaps = missing_expected_hourly_bars(
            live.index, is_market_open,
            until=pd.Timestamp.now(tz="UTC"))   # tail outage counts (P1-2)
        if history_gaps:
            failures.append(
                f"{sym}: {len(history_gaps)} missing open-session hourly "
                f"bars; first={history_gaps[0]}")
        B = complete[["open", "high", "low", "close"]]
        ref = reference_signals(B, scfg)
        expected_bars = set(live.index)
        if decisions.empty:
            bot_all, bot_sig = set(), set()
        else:
            d = decisions[decisions["sym"] == sym]
            bot_all = set(d["bar"])
            bot_sig = set(d.loc[d["signal"] == True, "bar"])  # noqa: E712
        missing = sorted(expected_bars - bot_all)
        extra_eval = sorted(bot_all - set(B.index))
        expected_sig = {t for t in expected_bars
                        if t in ref.index and bool(ref.loc[t, "signal"])}
        wrong_neg = sorted(expected_sig - bot_sig)
        wrong_pos = sorted((bot_sig & expected_bars) - expected_sig)
        if not bot_all:
            failures.append(f"{sym}: zero bot evaluations")
        if missing:
            failures.append(f"{sym}: {len(missing)} evaluation gaps")
        if extra_eval:
            failures.append(f"{sym}: {len(extra_eval)} decisions lack source bar")
        if wrong_neg or wrong_pos:
            failures.append(
                f"{sym}: false-neg={len(wrong_neg)} false-pos={len(wrong_pos)}")
        lines.append(
            f"{sym}: expected-evaluations={len(expected_bars)} "
            f"bot-evaluated={len(bot_all)} gaps={len(missing)} "
            f"false-neg={len(wrong_neg)} false-pos={len(wrong_pos)}")

    db_failures, db_info = _db_invariants(sdir / "trendbot.db", state)
    failures.extend(db_failures)
    lines.extend(db_info)
    if failures:
        lines.append("FAILURES:")
        lines.extend(f"- {failure}" for failure in failures)
        lines.append(f"VERDICT: FAILED ({len(failures)} gates)")
        return "\n".join(lines), False
    lines.append("VERDICT: CLEAN")
    return "\n".join(lines), True


def main() -> int:
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    sdir = BASE / cfg["ops"]["state_dir"]
    try:
        report, clean = build_report(cfg, sdir)
    except Exception as exc:
        report = ("VERDICT: FAILED\n"
                  f"reconcile crashed: {type(exc).__name__}: {exc}")
        clean = False
    tmp = (sdir / "reconcile_report.txt").with_suffix(".tmp")
    tmp.write_text(report)
    tmp.replace(sdir / "reconcile_report.txt")
    print(report)
    if not clean:
        # a FAILED nightly verdict must reach a human, not just a file
        # (review 2026-07-18 P1-3)
        try:
            from alerts import send_alert
            first = "; ".join(report.splitlines()[:3])
            send_alert(f"🚨 NIGHTLY RECONCILE FAILED — soak-gate verdict "
                       f"not clean: {first[:300]}")
        except Exception:
            pass
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
