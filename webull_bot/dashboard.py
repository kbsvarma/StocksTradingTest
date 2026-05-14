"""EC2 Paper Bot Dashboard — Streamlit UI for the dry-run Webull bot.

Runs on EC2 port 8504. Reads bot state, event log, and EC2 health.
Visual style mirrors the Lightsail dashboard (light theme, grid strip, KPI tiles).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from datetime import UTC, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml
import yfinance as yf

ET = ZoneInfo("America/New_York")

ROOT       = Path("/opt/webull-bot")
DATA_SPX   = ROOT / "data" / "spx"
LOGS_SPX   = ROOT / "logs" / "spx"
LOGS_EVENT = ROOT / "logs" / "events"
LOG_BOT_OUT = ROOT / "logs" / "bot.out"
LOG_BOT_ERR = ROOT / "logs" / "bot.err"
CONFIG_FILE = ROOT / "webull_bot" / "config.yaml"

st.set_page_config(
    page_title="Webull EC2 Paper Bot",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Light-theme + Lightsail-style CSS ────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#f6f8fa; --p1:#fff; --p2:#f6f8fa; --p3:#eaeef2;
  --bd:#d0d7de; --bd2:#b8c0cc;
  --t1:#1f2328; --t2:#57606a; --t3:#8c959f;
  --grn:#1a7f37; --red:#cf222e; --blu:#0969da; --amb:#9a6700; --tel:#0d7377;
  --grn-bg:rgba(26,127,55,.08); --red-bg:rgba(207,34,46,.08);
  --blu-bg:rgba(9,105,218,.08); --amb-bg:rgba(154,103,0,.08);
  --mono:'JetBrains Mono',ui-monospace,monospace; --sans:'Inter',system-ui,sans-serif;
  --shadow-sm:0 1px 2px rgba(31,35,40,.06);
}
html, body, [class*="css"] { font-family:var(--sans) !important; font-size:13px; -webkit-font-smoothing:antialiased; color:var(--t1); }
.stApp { background:var(--bg) !important; }
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"], header { display:none !important; }
.main .block-container { max-width:100% !important; padding:14px 24px 48px !important; }

.topnav {
  display:flex; align-items:center; justify-content:flex-end;
  gap:8px; margin-bottom:8px;
}
.bot-card {
  display:flex; align-items:center; gap:12px;
  background:#fff; border:1px solid var(--bd); border-radius:8px;
  padding:12px 18px; margin-bottom:10px; box-shadow:var(--shadow-sm);
}
.tb-icon {
  width:32px; height:32px; border-radius:7px; flex-shrink:0;
  background:linear-gradient(135deg,#9a6700 0%,#d4a72c 100%);
  display:flex; align-items:center; justify-content:center; font-size:15px; color:#fff;
}
.tb-name { font-size:14px; font-weight:700; color:var(--t1); letter-spacing:-.2px; }
.tb-sub { font-size:11px; color:var(--t3); margin-top:2px; }

.b {
  display:inline-flex; align-items:center; gap:4px;
  padding:2px 8px; border-radius:4px;
  font-size:10px; font-weight:700; letter-spacing:.5px; text-transform:uppercase;
  border:1px solid transparent; white-space:nowrap; vertical-align:middle; line-height:1;
}
.b-grn { color:var(--grn); background:var(--grn-bg); border-color:rgba(26,127,55,.25); }
.b-blu { color:var(--blu); background:var(--blu-bg); border-color:rgba(9,105,218,.25); }
.b-red { color:var(--red); background:var(--red-bg); border-color:rgba(207,34,46,.25); }
.b-amb { color:var(--amb); background:var(--amb-bg); border-color:rgba(154,103,0,.25); }
.b-dim { color:var(--t2); background:var(--p2); border-color:var(--bd); }
.dot { width:5px; height:5px; border-radius:50%; display:inline-block; }
.dg { background:var(--grn); animation:pl 2s infinite; } .dr { background:var(--red); }
@keyframes pl { 0%,100%{opacity:1} 50%{opacity:.3} }

.et-clock {
  font-family:var(--mono); font-size:11px; color:var(--t2);
  background:var(--p2); border:1px solid var(--bd); border-radius:5px;
  padding:4px 10px; letter-spacing:.4px;
}
.ref-btn {
  background:#1f2328; color:#fff !important; text-decoration:none !important;
  border-radius:6px; font-size:12px; font-weight:600;
  padding:6px 14px; border:1px solid #1f2328; display:inline-block;
}
.ref-btn:hover { background:#2d333a; }

.strip {
  display:grid; grid-template-columns:repeat(6,1fr);
  gap:1px; background:var(--bd);
  border:1px solid var(--bd); border-radius:8px;
  overflow:hidden; margin-bottom:12px; box-shadow:var(--shadow-sm);
}
.sc { background:var(--p1); padding:10px 14px; }
.sl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:4px; }
.sv { font-family:var(--mono); font-size:13px; font-weight:500; color:var(--t1); }
.sv-big { font-family:var(--mono); font-size:18px; font-weight:700; color:var(--t1); line-height:1.1; }
.sv-pct { font-family:var(--mono); font-size:11px; font-weight:600; margin-left:6px; vertical-align:middle; }
.ss { font-size:10px; color:var(--t3); margin-top:3px; }
.sv.vg { color:var(--grn); } .sv.vr { color:var(--red); } .sv.va { color:var(--amb); } .sv.vb { color:var(--blu); }

.sr { display:flex; align-items:center; gap:8px; margin:16px 0 6px; }
.sr span { font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; color:var(--t3); white-space:nowrap; }
.sr hr { flex:1; border:none; border-top:1px solid var(--bd); }

.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:10px; }
.kpi {
  background:var(--p1); border:1px solid var(--bd);
  border-top:2px solid var(--bd2); border-radius:8px;
  padding:12px 14px; box-shadow:var(--shadow-sm);
}
.kpi.kg { border-top-color:var(--grn); } .kpi.kr { border-top-color:var(--red); }
.kpi.kb { border-top-color:var(--blu); } .kpi.ka { border-top-color:var(--amb); }
.kpi.kt { border-top-color:var(--tel); }
.kl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:8px; }
.kv { font-family:var(--mono); font-size:20px; font-weight:500; color:var(--t1); line-height:1; }
.kv.vg { color:var(--grn); } .kv.vr { color:var(--red); } .kv.vb { color:var(--blu); } .kv.va { color:var(--amb); }
.ks { font-size:10px; color:var(--t3); margin-top:5px; }

.pc {
  background:var(--p1); border:1px solid var(--bd);
  border-left:3px solid var(--blu); border-radius:8px;
  padding:12px 16px; margin-bottom:8px; box-shadow:var(--shadow-sm);
}
.pc.pos { border-left-color:var(--grn); }
.pc-h { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.pc-n { font-size:13px; font-weight:700; color:var(--t1); }
.pf { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
.pfl { font-size:9px; font-weight:700; letter-spacing:.6px; text-transform:uppercase; color:var(--t3); margin-bottom:3px; }
.pfv { font-family:var(--mono); font-size:12px; color:var(--t1); }

.empty {
  background:var(--p1); border:1px dashed var(--bd); border-radius:8px;
  padding:18px; text-align:center; color:var(--t3); font-size:12px;
}

.stTabs [data-baseweb="tab-list"] { gap:4px; border-bottom:1px solid var(--bd); }
.stTabs [data-baseweb="tab"] { height:36px; padding:4px 16px; font-weight:600; font-size:12px; color:var(--t2); }
.stTabs [data-baseweb="tab"][aria-selected="true"] { color:var(--blu); }
.stTabs [data-baseweb="tab-panel"] { padding-top:12px; }

.stDataFrame { border:1px solid var(--bd) !important; border-radius:6px !important; }

div[data-testid="stMetric"] {
  background:var(--p1); border:1px solid var(--bd); border-radius:6px;
  padding:8px 12px; box-shadow:var(--shadow-sm);
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _badge(text: str, color: str = "blu", dot: bool = False) -> str:
    dot_html = f'<span class="dot d{color[0]}"></span>' if dot else ''
    return f'<span class="b b-{color}">{dot_html}{text}</span>'


def _strip_cell(label: str, value: str, sub: str = "", color: str = "") -> str:
    color_class = f"sv {color}" if color else "sv"
    sub_html = f'<div class="ss">{sub}</div>' if sub else ""
    return f'<div class="sc"><div class="sl">{label}</div><div class="{color_class}">{value}</div>{sub_html}</div>'


def _kpi(label: str, value: str, sub: str = "", color: str = "") -> str:
    border_class = f" k{color}" if color else ""
    val_class = f" v{color}" if color else ""
    sub_html = f'<div class="ks">{sub}</div>' if sub else ""
    return (f'<div class="kpi{border_class}"><div class="kl">{label}</div>'
            f'<div class="kv{val_class}">{value}</div>{sub_html}</div>')


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _read_config() -> dict:
    try:
        return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def _read_events(date_str: str | None = None, limit: int = 2000) -> pd.DataFrame:
    if date_str is None:
        date_str = datetime.now(ET).strftime("%Y-%m-%d")
    p = LOGS_EVENT / f"events-{date_str}.jsonl"
    if not p.exists():
        return pd.DataFrame()
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows[-limit:])


def _read_trades() -> pd.DataFrame:
    p = DATA_SPX / "trades.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _seconds_since_iso(iso: str) -> float:
    if not iso:
        return float("inf")
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(iso).astimezone(UTC)).total_seconds()
    except Exception:
        return float("inf")


def _systemctl_active(unit: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
        return (r.stdout.strip() == "active", r.stdout.strip() or r.stderr.strip())
    except Exception as e:
        return (False, str(e)[:50])


def _systemctl_show(unit: str, prop: str) -> str:
    try:
        r = subprocess.run(["systemctl", "show", unit, "-p", prop, "--value"],
                          capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def _journal_tail(unit: str, lines: int = 20) -> list[str]:
    try:
        r = subprocess.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
                          capture_output=True, text=True, timeout=4)
        return r.stdout.splitlines()
    except Exception:
        return []


def _read_proc_meminfo() -> dict:
    out = {}
    try:
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _disk_usage(path: str = "/") -> tuple[int, int, int]:
    try:
        s = shutil.disk_usage(path)
        return (s.total // (2**30), s.used // (2**30), s.free // (2**30))
    except Exception:
        return (0, 0, 0)


def _uptime_human() -> str:
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
        d = int(up // 86400); h = int((up % 86400) // 3600); m = int((up % 3600) // 60)
        if d: return f"{d}d {h}h {m}m"
        if h: return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "?"


def _outbound_ip() -> str:
    try:
        r = subprocess.run(["curl", "-s", "-m", "3", "https://api.ipify.org"],
                          capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


def _port_listening(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


@st.cache_data(ttl=60)
def _yf_quote(symbol: str) -> tuple[float, float]:
    """Return (current_price, pct_change_from_prev_close). 60s cache."""
    try:
        tk = yf.Ticker(symbol)
        fi = tk.fast_info
        cur = float(getattr(fi, "last_price", 0) or 0)
        prev = float(getattr(fi, "previous_close", 0) or 0)
        if not (cur and prev):
            hist = tk.history(period="2d", interval="1d")
            if not hist.empty and len(hist) >= 2:
                cur = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
        if cur and prev:
            pct = (cur - prev) / prev * 100
            return cur, pct
    except Exception:
        pass
    return 0.0, 0.0


def _aws_metadata(path: str) -> str:
    try:
        t = subprocess.run(
            ["curl", "-s", "-m", "2", "-X", "PUT",
             "http://169.254.169.254/latest/api/token",
             "-H", "X-aws-ec2-metadata-token-ttl-seconds: 60"],
            capture_output=True, text=True, timeout=3)
        token = t.stdout.strip()
        if not token: return "?"
        r = subprocess.run(
            ["curl", "-s", "-m", "2",
             f"http://169.254.169.254/latest/meta-data/{path}",
             "-H", f"X-aws-ec2-metadata-token: {token}"],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


# ── DATA LOAD ────────────────────────────────────────────────────────────────
state = _read_json(DATA_SPX / "state.json")
heartbeat = _read_json(DATA_SPX / "heartbeat.json")
cfg = _read_config()
bot_active, _ = _systemctl_active("webull-bot.service")
hb_age = _seconds_since_iso(heartbeat.get("ts", ""))
dry_run = os.environ.get("WEBULL_DRY_RUN", "0") == "1"

today_str = datetime.now(ET).strftime("%Y-%m-%d")
df_events = _read_events(today_str)
op = state.get("open_position")
wins = int(state.get("wins", 0) or 0)
losses = int(state.get("losses", 0) or 0)
total_pnl = float(state.get("total_pnl", 0) or 0)


# ── TOPBAR ───────────────────────────────────────────────────────────────────
mode_badge = _badge("PAPER", "amb") if dry_run else _badge("LIVE", "red")
status_badge = _badge("ACTIVE", "grn", dot=True) if bot_active else _badge("DOWN", "red", dot=True)
now_et = datetime.now(ET).strftime("%H:%M:%S  ET")

st.markdown(f"""
<div class="topnav">
  <span class="et-clock">{now_et}</span>
  <a href="?_={now_et}" target="_self" class="ref-btn">↻ Refresh</a>
</div>
<div class="bot-card">
  <div class="tb-icon">🟡</div>
  <div>
    <div class="tb-name">Webull SPXW Paper Bot &nbsp; {mode_badge} &nbsp; {status_badge}</div>
    <div class="tb-sub">SPXW · Bull Put Spread · 0DTE · EC2 (52.44.18.84) · uptime {_uptime_human()}</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── STATUS STRIP (6 cells like Lightsail) ────────────────────────────────────
hb_str = (
    f"{int(hb_age)}s ago" if hb_age < 60 else
    f"{int(hb_age // 60)}m ago" if hb_age < 3600 else
    f"{int(hb_age // 3600)}h ago" if hb_age != float("inf") else "—"
)
hb_color = "vg" if hb_age < 120 else "va" if hb_age < 900 else "vr"
pnl_color = "vg" if total_pnl > 0 else ("vr" if total_pnl < 0 else "")
n_signal = (df_events["event"] == "signal_eval").sum() if not df_events.empty else 0
n_skip = ((df_events["event"] == "vix_skip") | (df_events["event"] == "direction_skip")).sum() if not df_events.empty else 0

pos_value = (
    f"{int(op['short_strike'])}/{int(op['long_strike'])}P"
    if op else "watching"
)
pos_sub = (
    f"qty {op['quantity']} · credit ${op['entry_credit']:.2f}"
    if op else "no position"
)

spx_price, spx_pct = _yf_quote("^GSPC")
vix_price, vix_pct = _yf_quote("^VIX")

def _pct_html(pct: float) -> str:
    """Up arrow if positive (green), down arrow if negative (red), neutral if zero."""
    if pct > 0:
        return f'<span class="sv-pct" style="color:var(--grn);">▲ {abs(pct):.2f}%</span>'
    if pct < 0:
        return f'<span class="sv-pct" style="color:var(--red);">▼ {abs(pct):.2f}%</span>'
    return f'<span class="sv-pct" style="color:var(--t3);">0.00%</span>'

spx_value_html = (
    f'<span class="sv-big">{spx_price:,.2f}</span>{_pct_html(spx_pct)}'
    if spx_price else '<span class="sv-big">—</span>'
)
vix_value_html = (
    f'<span class="sv-big">{vix_price:.2f}</span>{_pct_html(vix_pct)}'
    if vix_price else '<span class="sv-big">—</span>'
)

st.markdown(f"""
<div class="strip">
  <div class="sc">
    <div class="sl">SPX Price</div>
    <div>{spx_value_html}</div>
  </div>
  <div class="sc">
    <div class="sl">VIX</div>
    <div>{vix_value_html}</div>
  </div>
  {_strip_cell("Bot Heartbeat", hb_str, "monitor tick", hb_color)}
  {_strip_cell("Open Position", pos_value, pos_sub, "vg" if op else "")}
  {_strip_cell("Net PnL Today", f"${total_pnl:+,.0f}", "synth/dry-run", pnl_color)}
  <div class="sc">
    <div class="sl">W / L</div>
    <div class="sv"><span style="color:var(--grn);font-weight:600;">{wins}W</span> / <span style="color:var(--red);font-weight:600;">{losses}L</span></div>
    <div class="ss">since bot start</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── TABS ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "Today's Session", "Compare to Mac", "Historical", "EC2 Health",
])

# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Today's Session
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="sr"><span>Latest Signal Evaluation</span><hr/></div>', unsafe_allow_html=True)

    if not df_events.empty and (df_events["event"] == "signal_eval").any():
        latest = df_events[df_events["event"] == "signal_eval"].iloc[-1]
        spx = latest.get("spx", 0); vix = latest.get("vix", 0)
        vix_ok = bool(latest.get("vix_ok"))
        vix_reason = latest.get("vix_reason", "")
        ts = str(latest.get("ts_et", ""))[11:19]
        st.markdown(f"""
        <div class="kpis">
          {_kpi("SPX", f"{spx:,.2f}", "spot", "b")}
          {_kpi("VIX", f"{vix:.2f}", "spot", "b")}
          {_kpi("VIX Gate", "✓ pass" if vix_ok else "✗ skip", vix_reason or "in zone", "g" if vix_ok else "a")}
          {_kpi("Last Eval", ts, "ET", "")}
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">No signal evaluations recorded yet today. Bot may still be waiting for entry window (10:30–14:30 ET).</div>', unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>Open Position</span><hr/></div>', unsafe_allow_html=True)
    if op:
        st.markdown(f"""
        <div class="pc pos">
          <div class="pc-h"><div class="pc-n">{int(op['short_strike'])}/{int(op['long_strike'])}P · qty {op['quantity']}</div>{_badge("OPEN", "grn", dot=True)}</div>
          <div class="pf">
            <div><div class="pfl">Entry credit</div><div class="pfv">${op['entry_credit']:.2f}</div></div>
            <div><div class="pfl">Stop (2×)</div><div class="pfv">${op['stop_price']:.2f}</div></div>
            <div><div class="pfl">Entry SPX</div><div class="pfv">{op.get('entry_spx', 0):,.0f}</div></div>
            <div><div class="pfl">Entry VIX</div><div class="pfv">{op.get('entry_vix', 0):.1f}</div></div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty">No open position.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>Live Event Feed (last 30)</span><hr/></div>', unsafe_allow_html=True)
    if not df_events.empty:
        recent = df_events.tail(30)[::-1].copy()
        keep = [c for c in ["ts_et", "event", "spx", "vix", "short_strike", "long_strike",
                            "mark", "stop", "fill_price", "reason", "source", "pnl_usd"]
                if c in recent.columns]
        if "ts_et" in recent.columns:
            recent["ts_et"] = recent["ts_et"].astype(str).str[11:19]
        st.dataframe(recent[keep].fillna(""), height=420, use_container_width=True, hide_index=True)
    else:
        st.markdown('<div class="empty">Event log empty for today. Will populate during market hours.</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Compare to Mac
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="sr"><span>Mac Live vs EC2 Paper — Today</span><hr/></div>', unsafe_allow_html=True)
    st.markdown('<div class="empty">Mac trades.csv sync to EC2 not configured yet. Once set up, Mac\'s real outcomes will appear here side-by-side with EC2\'s dry-run synthesized trades.</div>', unsafe_allow_html=True)
    st.markdown('<div class="sr"><span>EC2 Dry-Run Trades (this server)</span><hr/></div>', unsafe_allow_html=True)
    ec2_trades = _read_trades()
    if not ec2_trades.empty:
        st.dataframe(ec2_trades.tail(20), use_container_width=True, hide_index=True)
    else:
        st.markdown('<div class="empty">No trades recorded on EC2 yet.</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Historical
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown('<div class="sr"><span>Recent Dry-Run Trades</span><hr/></div>', unsafe_allow_html=True)
    trades = _read_trades()
    if not trades.empty:
        if "pnl_usd" in trades.columns:
            st.markdown(f"""
            <div class="kpis">
              {_kpi("Total trades", str(len(trades)))}
              {_kpi("Win rate", f"{(trades['pnl_usd'] > 0).mean()*100:.0f}%", "", "g")}
              {_kpi("Total PnL", f"${trades['pnl_usd'].sum():+.0f}", "", "g" if trades['pnl_usd'].sum() > 0 else "r")}
              {_kpi("Avg / trade", f"${trades['pnl_usd'].mean():+.0f}", "")}
            </div>
            """, unsafe_allow_html=True)
        st.dataframe(trades, use_container_width=True, hide_index=True, height=400)
    else:
        st.markdown('<div class="empty">No historical trades yet — will fill in over the week.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>Event Log Files on Disk</span><hr/></div>', unsafe_allow_html=True)
    if LOGS_EVENT.exists():
        files = sorted(LOGS_EVENT.glob("events-*.jsonl"), reverse=True)[:14]
        if files:
            df_files = pd.DataFrame([{
                "date": f.stem.replace("events-", ""),
                "size": f"{f.stat().st_size / 1024:.1f} KB",
                "events": (sum(1 for _ in open(f, encoding="utf-8")) if f.stat().st_size < 5_000_000 else "large"),
            } for f in files])
            st.dataframe(df_files, use_container_width=True, hide_index=True)
        else:
            st.markdown('<div class="empty">No event log files yet.</div>', unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — EC2 Health
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    mem = _read_proc_meminfo()
    used_pct = 0; mem_sub = ""
    if mem:
        total_kb = int(mem.get("MemTotal", "0").split()[0])
        avail_kb = int(mem.get("MemAvailable", "0").split()[0])
        used_pct = (1 - avail_kb / total_kb) * 100 if total_kb else 0
        mem_sub = f"{(total_kb-avail_kb)/1024/1024:.1f} / {total_kb/1024/1024:.1f} GB"
    total_gb, used_gb, free_gb = _disk_usage("/")
    disk_pct = used_gb / total_gb * 100 if total_gb else 0
    try:
        load1 = open("/proc/loadavg").read().split()[0]
    except Exception:
        load1 = "?"

    st.markdown('<div class="sr"><span>Instance</span><hr/></div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="kpis">
      {_kpi("Uptime", _uptime_human())}
      {_kpi("Memory used", f"{used_pct:.0f}%", mem_sub, "a" if used_pct > 80 else "")}
      {_kpi("Disk used", f"{disk_pct:.0f}%", f"{used_gb}/{total_gb} GB · {free_gb} free", "a" if disk_pct > 80 else "")}
      {_kpi("Load (1m)", load1)}
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>Services</span><hr/></div>', unsafe_allow_html=True)
    services = ["webull-bot.service", "webull-watchdog.timer", "webull-ibgateway.service", "webull-dashboard.service", "ssh.socket", "ufw"]
    rows = []
    for s in services:
        active, status = _systemctl_active(s)
        enabled = _systemctl_show(s, "UnitFileState") or "?"
        ts = _systemctl_show(s, "ActiveEnterTimestamp") or ""
        since = " ".join(ts.split()[1:3]) if len(ts.split()) >= 3 else ts
        rows.append({"service": s, "active": "✓" if active else "✗", "status": status, "enabled": enabled, "since": since})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown('<div class="sr"><span>Network</span><hr/></div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="kpis">
      {_kpi("Outbound IP", _outbound_ip(), "for Webull whitelist")}
      {_kpi("SSH (22)", "✓ listening" if _port_listening(22) else "✗", "", "g" if _port_listening(22) else "r")}
      {_kpi("IBG (4001)", "✓ listening" if _port_listening(4001) else "off", "this week", "g" if _port_listening(4001) else "")}
      {_kpi("Dashboard (8504)", "✓ listening" if _port_listening(8504) else "✗", "", "g" if _port_listening(8504) else "r")}
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>AWS Metadata</span><hr/></div>', unsafe_allow_html=True)
    iam_raw = _aws_metadata("iam/info")
    iam_role = "?"
    try:
        iam_role = json.loads(iam_raw).get("InstanceProfileArn", "?").split("/")[-1]
    except Exception:
        pass
    st.markdown(f"""
    <div class="kpis">
      {_kpi("Instance ID", _aws_metadata("instance-id"))}
      {_kpi("Type", _aws_metadata("instance-type"))}
      {_kpi("AZ", _aws_metadata("placement/availability-zone"))}
      {_kpi("IAM Role", iam_role)}
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sr"><span>Recent bot.err (last 30 lines)</span><hr/></div>', unsafe_allow_html=True)
    if LOG_BOT_ERR.exists():
        try:
            lines = LOG_BOT_ERR.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]
            st.code("\n".join(lines) or "(empty)", language="text")
        except Exception as e:
            st.error(f"Couldn't read bot.err: {e}")
    else:
        st.markdown('<div class="empty">bot.err not yet created.</div>', unsafe_allow_html=True)
