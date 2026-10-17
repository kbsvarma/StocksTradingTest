"""ATM Bot Dashboard — Streamlit UI for the EC2 paper bot.

Runs on EC2 port 8505 (legacy dashboard on 8504, ATM on 8505 — no collision).
Reads ATM bot state, event log, and shared EC2 health.

Reuses styling from webull_bot/dashboard.py. Reads from atm_bot/ paths.
"""
from __future__ import annotations

# Install secret-scrub log filter at process start.
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
from webull_bot_atm import log_redact  # noqa: F401, E402

import json
import os
import socket
import subprocess
from datetime import UTC, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

ET = ZoneInfo("America/New_York")

# Path resolution: on EC2 we have /opt/webull-bot-atm (mirror of vG_w50 layout);
# on Mac we read from local repo paths
def _find_data_root() -> Path:
    """Find the ATM data root — EC2 mirror or local repo."""
    for candidate in [
        Path("/opt/webull-bot-atm/data/spx"),
        Path("/home/ubuntu/StocksTradingTest/spx_spread_bot/data/webull_live/atm_bot"),
        Path(__file__).resolve().parent.parent / "spx_spread_bot/data/webull_live/atm_bot",
    ]:
        if candidate.exists():
            return candidate
    return Path("spx_spread_bot/data/webull_live/atm_bot")  # fallback

DATA_DIR = _find_data_root()
EVENTS_DIR = DATA_DIR / "events"
CONFIG_FILE = Path(__file__).resolve().parent / "config_atm.yaml"
LOG_BOT_OUT = Path(__file__).resolve().parent / "logs" / "launchd.out"
LOG_BOT_ERR = Path(__file__).resolve().parent / "logs" / "launchd.err"

# Shared / ATM-specific status files
_RECON_STATUS_PATH = Path("/tmp/reconcile_watchdog_status_atm.json")
_MONITOR_TICK_PATH = Path("/tmp/monitor_tick_atm.json")
_TG_SENT_LOG = Path("/tmp/telegram_sent.log")
_EVENT_QUEUE = Path("/tmp/event_queue.jsonl")

st.set_page_config(
    page_title="Webull ATM Bot (PROFILE_NEW_ATM)",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Reuse minimal styling (ATM bot uses blue accent vs legacy yellow)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*, *::before, *::after { box-sizing: border-box; }
body, .stApp { background: #f5f5f7 !important; color: #1d1d1f !important; font-family: 'Inter', sans-serif !important; }
.tile {
  background: white; border: 1px solid #e5e5e7; border-radius: 12px;
  padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.tile h3 { font-size: 12px; font-weight: 600; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.tile .v { font-size: 28px; font-weight: 700; color: #1d1d1f; font-family: 'JetBrains Mono', monospace; }
.tile .v.pos { color: #34c759; }
.tile .v.neg { color: #ff3b30; }
.tile .v.warn { color: #ff9500; }
.tile .sub { font-size: 11px; color: #6e6e73; margin-top: 4px; }
.profile-badge {
  display: inline-block; padding: 4px 10px; background: #007aff; color: white;
  border-radius: 6px; font-size: 12px; font-weight: 600; margin-left: 8px;
}
.section-h { font-size: 14px; font-weight: 600; margin: 24px 0 12px 0; color: #1d1d1f; }
.warn-banner { background: #fff3cd; border: 1px solid #ffc107; padding: 12px; border-radius: 8px; margin: 8px 0; }
.crit-banner { background: #f8d7da; border: 1px solid #dc3545; padding: 12px; border-radius: 8px; margin: 8px 0; }
hr { border: none; border-top: 1px solid #e5e5e7; margin: 16px 0; }
.stApp > header { background: transparent; }
</style>
""", unsafe_allow_html=True)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _read_csv_safe(p: Path) -> pd.DataFrame:
    try:
        if not p.exists(): return pd.DataFrame()
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _now() -> datetime:
    return datetime.now(ET)


# ── HEADER ────────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown(
        f"<h1 style='font-size:24px;font-weight:700;margin:0;'>"
        f"Webull ATM Bot <span class='profile-badge'>PROFILE_NEW_ATM</span></h1>"
        f"<p style='color:#6e6e73;font-size:13px;margin-top:4px;'>"
        f"45pt × $13.50 target × 09:45 ET × noThu × SL 3.0× | Updated: "
        f"{_now().strftime('%Y-%m-%d %H:%M:%S ET')}</p>",
        unsafe_allow_html=True,
    )
with col2:
    if st.button("🔄 Refresh"):
        st.rerun()

st.markdown("<hr>", unsafe_allow_html=True)

# ── DATA SOURCES STATUS ──────────────────────────────────────────────────────
heartbeat = _read_json(DATA_DIR / "heartbeat.json")
state = _read_json(DATA_DIR / "state.json")
recon = _read_json(_RECON_STATUS_PATH)
monitor = _read_json(_MONITOR_TICK_PATH)

hb_ts = heartbeat.get("ts", "")
if hb_ts:
    try:
        hb_dt = datetime.fromisoformat(hb_ts)
        if hb_dt.tzinfo is None: hb_dt = hb_dt.replace(tzinfo=ET)
        hb_age = (_now() - hb_dt).total_seconds()
        if hb_age > 600:
            st.markdown(f"<div class='crit-banner'>🔴 STALE: bot heartbeat is {hb_age:.0f}s old — bot may be dead</div>", unsafe_allow_html=True)
        elif hb_age > 180:
            st.markdown(f"<div class='warn-banner'>🟡 Heartbeat {hb_age:.0f}s old — within normal slow-poll range</div>", unsafe_allow_html=True)
    except Exception: pass
else:
    st.markdown("<div class='warn-banner'>⚠️ No heartbeat from ATM bot yet (or not started)</div>", unsafe_allow_html=True)

# ── KPI ROW ──────────────────────────────────────────────────────────────────
trades_csv = _read_csv_safe(DATA_DIR / "trades.csv")
today_str = _now().strftime("%Y-%m-%d")
today_trades = trades_csv[trades_csv["Date"] == today_str] if "Date" in trades_csv.columns else pd.DataFrame()
total_pnl_today = today_trades["PnL USD"].sum() if "PnL USD" in today_trades.columns else 0
total_pnl_all = trades_csv["PnL USD"].sum() if "PnL USD" in trades_csv.columns else 0
n_trades_total = len(trades_csv)
n_wins_total = len(trades_csv[trades_csv["PnL USD"] > 0]) if "PnL USD" in trades_csv.columns else 0

c1, c2, c3, c4 = st.columns(4)
with c1:
    cls = "pos" if total_pnl_today >= 0 else "neg"
    st.markdown(f"""<div class='tile'><h3>Today's P&L</h3>
        <div class='v {cls}'>${total_pnl_today:+,.0f}</div>
        <div class='sub'>{len(today_trades)} trade(s) today</div></div>""", unsafe_allow_html=True)
with c2:
    cls = "pos" if total_pnl_all >= 0 else "neg"
    st.markdown(f"""<div class='tile'><h3>All-Time P&L</h3>
        <div class='v {cls}'>${total_pnl_all:+,.0f}</div>
        <div class='sub'>{n_trades_total} trades</div></div>""", unsafe_allow_html=True)
with c3:
    win_pct = (n_wins_total / n_trades_total * 100) if n_trades_total else 0
    cls = "pos" if win_pct >= 60 else "warn" if win_pct >= 50 else "neg"
    st.markdown(f"""<div class='tile'><h3>Win Rate</h3>
        <div class='v {cls}'>{win_pct:.0f}%</div>
        <div class='sub'>{n_wins_total}/{n_trades_total}</div></div>""", unsafe_allow_html=True)
with c4:
    pos = state.get("open_position")
    pos_str = "OPEN" if pos else "FLAT"
    cls = "warn" if pos else ""
    st.markdown(f"""<div class='tile'><h3>Position</h3>
        <div class='v {cls}'>{pos_str}</div>
        <div class='sub'>{pos.get('symbol', '') if pos else 'no position'}</div></div>""", unsafe_allow_html=True)

# ── ACTIVE POSITION DETAIL ───────────────────────────────────────────────────
if state.get("open_position"):
    p = state["open_position"]
    st.markdown("<div class='section-h'>Active Position</div>", unsafe_allow_html=True)
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1: st.metric("Short Strike", p.get("short_strike", "?"))
    with pc2: st.metric("Long Strike", p.get("long_strike", "?"))
    with pc3: st.metric("Entry Credit", f"${p.get('entry_credit', 0):.2f}")
    with pc4:
        sl_thresh = p.get("entry_credit", 0) * 3.0
        st.metric("SL Trigger (3.0×)", f"${sl_thresh:.2f}")

    if monitor:
        mt = monitor.get("ts", "")
        try:
            mt_dt = datetime.fromisoformat(mt)
            mt_age = (_now() - mt_dt.replace(tzinfo=ET) if mt_dt.tzinfo is None else _now() - mt_dt).total_seconds()
            cur_mid = monitor.get("mid", 0)
            st.write(f"Monitor: current mid ${cur_mid:.2f} (last update {mt_age:.0f}s ago)")
        except Exception: pass

# ── HEARTBEAT DETAIL ─────────────────────────────────────────────────────────
st.markdown("<div class='section-h'>Bot Heartbeat</div>", unsafe_allow_html=True)
if heartbeat:
    hc1, hc2, hc3 = st.columns(3)
    with hc1:
        st.write(f"**SPX:** {heartbeat.get('live_spx', '?')} [{heartbeat.get('spx_source', '?')}]")
        st.write(f"**VIX:** {heartbeat.get('live_vix', '?')} [{heartbeat.get('vix_source', '?')}]")
    with hc2:
        st.write(f"**Trading date:** {heartbeat.get('trading_date', '?')}")
        st.write(f"**Trades today:** {heartbeat.get('trade_taken_today', '?')}")
    with hc3:
        st.write(f"**Total trades:** {heartbeat.get('total_trades', 0)}")
        st.write(f"**W/L:** {heartbeat.get('wins', 0)}/{heartbeat.get('losses', 0)}")
else:
    st.info("No heartbeat data — bot may not have run yet today")

# ── TRADES TABLE ─────────────────────────────────────────────────────────────
st.markdown("<div class='section-h'>Recent Trades</div>", unsafe_allow_html=True)
if not trades_csv.empty:
    show_cols = [c for c in [
        "Date", "Short Strike", "Long Strike", "Entry Credit", "Exit Price",
        "PnL USD", "Exit Reason", "Profile",
    ] if c in trades_csv.columns]
    st.dataframe(trades_csv[show_cols].tail(25), use_container_width=True, hide_index=True)
else:
    st.info("No trades logged yet")

# ── WATCHDOG + EVENT QUEUE ───────────────────────────────────────────────────
st.markdown("<div class='section-h'>Watchdog & Event Queue</div>", unsafe_allow_html=True)
wc1, wc2 = st.columns(2)
with wc1:
    if recon:
        st.write(f"**Last reconcile:** {recon.get('last_run_ts', '?')}")
        st.write(f"**Orphans detected:** {recon.get('orphans_detected', 0)}")
        st.write(f"**Phantoms detected:** {recon.get('phantoms_detected', 0)}")
    else:
        st.info("Watchdog has not run yet (outside market hours?)")
with wc2:
    # Show last 5 ATM events from queue
    if _EVENT_QUEUE.exists():
        atm_events = []
        try:
            with _EVENT_QUEUE.open() as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                        if ev.get("bot") == "ATM" or "[ATM]" in ev.get("subject", ""):
                            atm_events.append(ev)
                    except Exception: continue
        except Exception: pass
        st.write(f"**Last 5 ATM events:** ({len(atm_events)} total in queue)")
        for ev in atm_events[-5:]:
            st.text(f"  {ev.get('ts', '')[:19]} [{ev.get('severity','?')}] {ev.get('subject','')}")
    else:
        st.info("No event queue file yet")

# ── CONFIG VIEW ──────────────────────────────────────────────────────────────
with st.expander("View active config"):
    if CONFIG_FILE.exists():
        st.code(CONFIG_FILE.read_text(), language="yaml")
    else:
        st.warning(f"Config file not found: {CONFIG_FILE}")

# ── RECENT LOGS ──────────────────────────────────────────────────────────────
with st.expander("Recent stdout (last 50 lines)"):
    if LOG_BOT_OUT.exists():
        try:
            lines = LOG_BOT_OUT.read_text().splitlines()[-50:]
            st.text("\n".join(lines))
        except Exception as e:
            st.error(f"Failed to read log: {e}")
    else:
        st.info(f"Log not found: {LOG_BOT_OUT}")

with st.expander("Recent stderr (last 50 lines)"):
    if LOG_BOT_ERR.exists():
        try:
            lines = LOG_BOT_ERR.read_text().splitlines()[-50:]
            st.text("\n".join(lines))
        except Exception as e:
            st.error(f"Failed to read log: {e}")
    else:
        st.info(f"Log not found: {LOG_BOT_ERR}")

# ── FOOTER ───────────────────────────────────────────────────────────────────
st.markdown(f"""<div style='text-align:center;color:#6e6e73;font-size:11px;margin-top:24px;'>
ATM Bot Dashboard | Data dir: {DATA_DIR} | Host: {socket.gethostname()}
</div>""", unsafe_allow_html=True)
