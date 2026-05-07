from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

import pandas as pd
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).resolve().parent

@dataclass(frozen=True)
class BotView:
    name: str; symbol: str; expected_mode: str; strategy_scope: str
    status_file: Path; trades_file: Path; signal_events_file: Path
    order_events_file: Path; system_log_file: Path

BOT_VIEWS = {
    "SPX Paper": BotView(
        name="SPX Paper Bot", symbol="SPX", expected_mode="PAPER",
        strategy_scope="BPS / IC / BWB / IF",
        status_file=ROOT/"data"/"status.json",
        trades_file=ROOT/"data"/"trades.csv",
        signal_events_file=ROOT/"logs"/"signal_events.jsonl",
        order_events_file=ROOT/"logs"/"order_events.jsonl",
        system_log_file=ROOT/"logs"/"system.log",
    ),
    "XSP Live": BotView(
        name="SPX-XSP-Live Bot", symbol="SPX", expected_mode="PAPER",
        strategy_scope="BULL PUT SPREAD",
        status_file=ROOT/"data"/"spx_live"/"status.json",
        trades_file=ROOT/"data"/"spx_live"/"trades.csv",
        signal_events_file=ROOT/"logs"/"spx_live"/"signal_events.jsonl",
        order_events_file=ROOT/"logs"/"spx_live"/"order_events.jsonl",
        system_log_file=ROOT/"logs"/"spx_live"/"system.log",
    ),
}

st.set_page_config(page_title="Trading Terminal", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:    #f6f8fa;
  --p1:    #ffffff;
  --p2:    #f6f8fa;
  --p3:    #eaeef2;
  --bd:    #d0d7de;
  --bd2:   #b8c0cc;
  --t1:    #1f2328;
  --t2:    #57606a;
  --t3:    #8c959f;
  --grn:   #1a7f37;
  --red:   #cf222e;
  --blu:   #0969da;
  --tel:   #0d7377;
  --amb:   #9a6700;
  --grn-bg: rgba(26,127,55,0.08);
  --red-bg: rgba(207,34,46,0.08);
  --blu-bg: rgba(9,105,218,0.08);
  --tel-bg: rgba(13,115,119,0.08);
  --amb-bg: rgba(154,103,0,0.08);
  --mono:  'JetBrains Mono', ui-monospace, monospace;
  --sans:  'Inter', system-ui, sans-serif;
  --shadow: 0 1px 3px rgba(31,35,40,0.08), 0 0 0 1px rgba(31,35,40,0.06);
  --shadow-sm: 0 1px 2px rgba(31,35,40,0.06);
}

html, body, [class*="css"] {
  font-family: var(--sans) !important;
  font-size: 13px;
  -webkit-font-smoothing: antialiased;
  color: var(--t1);
}

.stApp { background: var(--bg) !important; }
#MainMenu, footer, [data-testid="stDecoration"],
[data-testid="stStatusWidget"], header { display: none !important; }
.main .block-container { max-width: 100% !important; padding: 16px 24px 48px !important; }

/* ── Topbar ─────────────────────────────────────────────── */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 2px 14px; margin-bottom: 10px;
  border-bottom: 1px solid var(--bd);
}
.tb-left  { display: flex; align-items: center; gap: 10px; }
.tb-right { display: flex; align-items: center; gap: 10px; }

/* ── Refresh link-button ─────────────────────────────────── */
.ref-btn {
  background: #1f2328; color: #ffffff !important; text-decoration: none !important;
  border-radius: 6px; font-size: 12px; font-weight: 600;
  font-family: var(--sans); padding: 6px 14px;
  border: 1px solid #1f2328; white-space: nowrap; transition: background .15s;
  display: inline-block;
}
.ref-btn:hover { background: #2d333a; }

.tb-icon {
  width: 32px; height: 32px; border-radius: 7px; flex-shrink: 0;
  background: linear-gradient(135deg, #0969da 0%, #2196f3 100%);
  display: flex; align-items: center; justify-content: center; font-size: 15px;
}
.tb-name { font-size: 14px; font-weight: 700; color: var(--t1); letter-spacing: -.2px; }
.tb-sub  { font-size: 11px; color: var(--t3); margin-top: 1px; }

/* ── Segmented switcher ──────────────────────────────────── */
.seg {
  display: inline-flex;
  background: var(--p2);
  border: 1px solid var(--bd);
  border-radius: 6px;
  padding: 3px;
  gap: 2px;
}
.seg-btn {
  padding: 5px 14px; border-radius: 4px; font-size: 12px; font-weight: 600;
  cursor: pointer; border: 1px solid transparent; background: transparent;
  color: var(--t2); transition: all .15s; white-space: nowrap;
  font-family: var(--sans); text-decoration: none !important; display: inline-block;
}
.seg-btn:hover { background: var(--p3); color: var(--t1); }
.seg-btn.active {
  background: var(--p1); color: var(--blu);
  box-shadow: var(--shadow-sm); border-color: rgba(9,105,218,.3);
}
.seg-btn.active.live { color: var(--grn); border-color: rgba(26,127,55,.3); }

.et-clock {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--t2);
  background: var(--p2);
  border: 1px solid var(--bd);
  border-radius: 5px;
  padding: 4px 10px;
  letter-spacing: .4px;
}

/* ── Badge ───────────────────────────────────────────────── */
.b {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 4px;
  font-size: 10px; font-weight: 700; letter-spacing: .5px;
  text-transform: uppercase; border: 1px solid transparent; white-space: nowrap;
  vertical-align: middle; line-height: 1;
}
.b-grn { color: var(--grn); background: var(--grn-bg); border-color: rgba(26,127,55,.25); }
.b-blu { color: var(--blu); background: var(--blu-bg); border-color: rgba(9,105,218,.25); }
.b-red { color: var(--red); background: var(--red-bg); border-color: rgba(207,34,46,.25); }
.b-amb { color: var(--amb); background: var(--amb-bg); border-color: rgba(154,103,0,.25); }
.b-dim { color: var(--t2); background: var(--p2); border-color: var(--bd); }
.dot  { width: 5px; height: 5px; border-radius: 50%; display: inline-block; }
.dg   { background: var(--grn); animation: pl 2s infinite; }
.dr   { background: var(--red); }
@keyframes pl { 0%,100%{opacity:1} 50%{opacity:.3} }

/* ── Status strip ────────────────────────────────────────── */
.strip {
  display: grid; grid-template-columns: repeat(7, 1fr);
  gap: 1px; background: var(--bd);
  border: 1px solid var(--bd); border-radius: 8px;
  overflow: hidden; margin-bottom: 10px;
  box-shadow: var(--shadow-sm);
}
.sc { background: var(--p1); padding: 10px 14px; }
.sl { font-size: 9px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--t3); margin-bottom: 4px; }
.sv { font-family: var(--mono); font-size: 13px; font-weight: 500; color: var(--t1); }
.ss { font-size: 10px; color: var(--t3); margin-top: 3px; }

/* ── Section rule ────────────────────────────────────────── */
.sr { display: flex; align-items: center; gap: 8px; margin: 18px 0 8px; }
.sr span { font-size: 9px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: var(--t3); white-space: nowrap; }
.sr hr { flex: 1; border: none; border-top: 1px solid var(--bd); }

/* ── KPI cards ───────────────────────────────────────────── */
.kpis { display: grid; grid-template-columns: repeat(5,1fr); gap: 8px; margin-bottom: 10px; }
.kpi {
  background: var(--p1); border: 1px solid var(--bd);
  border-top: 2px solid var(--bd2); border-radius: 8px;
  padding: 12px 14px; box-shadow: var(--shadow-sm);
}
.kpi.kg { border-top-color: var(--grn); }
.kpi.kr { border-top-color: var(--red); }
.kpi.kb { border-top-color: var(--blu); }
.kpi.kt { border-top-color: var(--tel); }
.kpi.ka { border-top-color: var(--amb); }

.kl { font-size: 9px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--t3); margin-bottom: 8px; }
.kv { font-family: var(--mono); font-size: 20px; font-weight: 500; color: var(--t1); line-height: 1; }
.kv.vg { color: var(--grn); } .kv.vr { color: var(--red); }
.kv.vb { color: var(--blu); } .kv.vt { color: var(--tel); }
.ks { font-size: 10px; color: var(--t3); margin-top: 5px; }

/* ── Position card ───────────────────────────────────────── */
.pc {
  background: var(--p1); border: 1px solid var(--bd);
  border-left: 3px solid var(--grn); border-radius: 8px;
  padding: 12px 16px; margin-bottom: 8px; box-shadow: var(--shadow-sm);
}
.pc-h { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.pc-n { font-size: 13px; font-weight: 700; color: var(--grn); }
.pf   { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.pfl  { font-size: 9px; font-weight: 700; letter-spacing: .6px; text-transform: uppercase; color: var(--t3); margin-bottom: 3px; }
.pfv  { font-family: var(--mono); font-size: 12px; color: var(--t1); }

/* ── Tabs ────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
  background: var(--p1) !important;
  border-bottom: 1px solid var(--bd) !important;
  gap: 0 !important; padding: 0 !important;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important; border: none !important;
  border-bottom: 2px solid transparent !important; border-radius: 0 !important;
  padding: 8px 20px !important; color: var(--t3) !important;
  font-size: 12px !important; font-weight: 500 !important;
}
.stTabs [aria-selected="true"] {
  color: var(--t1) !important;
  border-bottom-color: var(--blu) !important;
  font-weight: 600 !important;
}
.stTabs [data-baseweb="tab-panel"] {
  background: var(--p1) !important; border: 1px solid var(--bd) !important;
  border-top: none !important; border-radius: 0 0 8px 8px !important;
  padding: 12px !important;
}

/* ── Tables ──────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
  border: 1px solid var(--bd) !important; border-radius: 8px !important; overflow: hidden;
  box-shadow: var(--shadow-sm);
}
[data-testid="stDataFrame"] thead th {
  background: var(--p2) !important; color: var(--t3) !important;
  font-size: 9px !important; font-weight: 700 !important;
  letter-spacing: .6px !important; text-transform: uppercase !important;
  border-bottom: 1px solid var(--bd) !important;
}
[data-testid="stDataFrame"] tbody td {
  color: var(--t1) !important; border-bottom: 1px solid var(--p3) !important;
}
[data-testid="stDataFrame"] tbody tr:hover td { background: var(--p2) !important; }

/* ── Log ─────────────────────────────────────────────────── */
.log-w { background: #f6f8fa; border: 1px solid var(--bd); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow-sm); }
.log-h { background: var(--p3); border-bottom: 1px solid var(--bd); padding: 6px 14px;
         font-size: 9px; font-weight: 700; letter-spacing: .8px; text-transform: uppercase; color: var(--t3); }
.stApp pre, .stApp code {
  font-family: var(--mono) !important; font-size: 11px !important;
  background: transparent !important; color: #0550ae !important;
}

/* ── No data ─────────────────────────────────────────────── */
.nd {
  background: var(--p2); border: 1px dashed var(--bd); border-radius: 8px;
  padding: 16px; text-align: center; font-size: 12px; color: var(--t3);
}

/* ── Reason bar ──────────────────────────────────────────── */
.reason {
  background: var(--p2); border: 1px solid var(--bd); border-radius: 8px;
  padding: 8px 14px; font-size: 12px; color: var(--t2); margin-top: 4px;
}
.reason-k { font-size: 9px; font-weight: 700; letter-spacing: .6px;
            text-transform: uppercase; color: var(--t3); margin-right: 8px; }

/* ── Refresh button ──────────────────────────────────────── */
button[data-testid="baseButton-secondary"] {
  background: var(--p1) !important; border: 1px solid var(--bd) !important;
  color: var(--t1) !important; border-radius: 6px !important;
  font-size: 12px !important; font-weight: 600 !important;
  padding: 5px 14px !important; box-shadow: var(--shadow-sm) !important;
  transition: border-color .15s, background .15s !important;
}
button[data-testid="baseButton-secondary"]:hover {
  border-color: var(--blu) !important; background: var(--blu-bg) !important;
}


/* ── Scrollbar ───────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--bd2); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--t3); }

@media (max-width: 1100px) { .kpis { grid-template-columns: repeat(3,1fr); } }
@media (max-width: 1100px) { .strip { grid-template-columns: repeat(3,1fr); } }
@media (max-width: 900px)  {
  .kpis  { grid-template-columns: repeat(2,1fr); }
  .strip { grid-template-columns: repeat(2,1fr); }
  .pf    { grid-template-columns: repeat(2,1fr); }
}
</style>
""", unsafe_allow_html=True)


# ── helpers ─────────────────────────────────────────────────────────────────

def _rj(p):
    try: return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except: return {}

def _rjl(p, n=300):
    if not p.exists(): return []
    buf: deque[str] = deque(maxlen=n)
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln: buf.append(ln)
    except: return []
    out = []
    for r in buf:
        try: out.append(json.loads(r))
        except: pass
    return out

def _rl(p, n=200):
    if not p.exists(): return []
    buf: deque[str] = deque(maxlen=n)
    try:
        with p.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if ln: buf.append(ln)
    except: return []
    return list(buf)

@st.cache_data(ttl=60)
def _yf_spx_price() -> float:
    """Fetch last SPX price from Yahoo Finance (^GSPC). Cached 60s."""
    try:
        ticker = yf.Ticker("^GSPC")
        info = ticker.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if price and price > 0:
            return float(price)
        # fallback: last close from history
        hist = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def _rt(p):
    if not p.exists() or p.stat().st_size == 0: return pd.DataFrame()
    try: df = pd.read_csv(p)
    except: return pd.DataFrame()
    if df.empty: return df
    # Handle both title-case CSV headers and legacy snake_case
    df = df.rename(columns={
        # Title-case (actual CSV format)
        "Entry time":           "Entry",
        "Exit time":            "Exit",
        "Exit reason":          "Exit Reason",
        "Exit price":           "Exit Price",
        "PnL per contract":     "PnL/ct",
        "Total PnL":            "Total PnL",
        "Win/Loss":             "W/L",
        "Credit received":      "Credit",
        "Contracts":            "Cts",
        "Short strike":         "Short",
        "Long strike":          "Long",
        "SPX price at entry":   "SPX",
        "VIX at entry":         "VIX",
        # Legacy snake_case fallback
        "date":                 "Date",
        "strategy":             "Strategy",
        "contracts":            "Cts",
        "entry_time":           "Entry",
        "exit_time":            "Exit",
        "exit_reason":          "Exit Reason",
        "pnl_per_contract":     "PnL/ct",
        "total_pnl":            "Total PnL",
        "win_loss":             "W/L",
        "credit_received":      "Credit",
    })
    for c in ["Total PnL", "PnL/ct", "Cts", "SPX", "VIX", "Short", "Long", "Exit Price"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna(df["Date"].astype(str))
    return df

def _cash(v, sign=False):
    try:
        f = float(v); p = "+" if sign and f > 0 else ""
        return f"{p}${f:,.2f}"
    except: return "—"

def _pct(v):
    try: return f"{float(v)*100:.1f}%"
    except: return "—"

def _ts(v):
    if not v: return "—"
    try: return datetime.fromisoformat(str(v).replace("Z","+00:00")).strftime("%Y-%m-%d  %H:%M:%S")
    except: return str(v)

def _mdd(s):
    if s.empty: return 0.0
    e = s.cumsum(); return float((e - e.cummax()).min())

def _stats(df):
    z = dict(total=0.,avg=0.,wr=0.,mdd=0.,n=0.,today=0.)
    if df.empty or "Total PnL" not in df.columns: return z
    pnl = pd.to_numeric(df["Total PnL"], errors="coerce").fillna(0.)
    n = len(df)
    wins = int((df["W/L"].str.strip().str.lower()=="win").sum()) if "W/L" in df.columns else int((pnl>0).sum())
    today = datetime.now().strftime("%Y-%m-%d")
    tp = float(pnl[df["Date"].astype(str)==today].sum()) if "Date" in df.columns else 0.
    return dict(total=float(pnl.sum()), avg=float(pnl.mean()) if n else 0.,
                wr=wins/n if n else 0., mdd=_mdd(pnl), n=float(n), today=tp)

def _pos(state):
    raw = state.get("open_positions")
    if isinstance(raw, list): return [p for p in raw if isinstance(p, dict)]
    leg = state.get("open_position")
    return [leg] if isinstance(leg, dict) and leg else []

def _legs(legs):
    if not legs: return "—"
    return " | ".join(f"{str(l.get('direction',''))[:1].upper()} {l.get('right','')}{l.get('strike','')}" for l in legs)

def _b(txt, cls="dim"):
    dot = {"grn":"dg","red":"dr"}.get(cls,"")
    d = f'<span class="dot {dot}"></span>' if dot else ""
    return f'<span class="b b-{cls}">{d}{txt}</span>'

def _acc(v):
    return "g" if v > 0 else "r" if v < 0 else ""

def _kpi(label, val, sub="", c=""):
    vc = f"kv v{c}" if c else "kv"
    cc = f"kpi k{c}" if c else "kpi"
    s  = f'<div class="ks">{sub}</div>' if sub else ""
    return f'<div class="{cc}"><div class="kl">{label}</div><div class="{vc}">{val}</div>{s}</div>'

def _rule(label):
    st.markdown(f'<div class="sr"><span>{label}</span><hr></div>', unsafe_allow_html=True)


# ── Bot selection via query params ──────────────────────────────────────────

bot_key = st.query_params.get("bot", "SPX Paper")
if bot_key not in BOT_VIEWS:
    bot_key = "SPX Paper"

is_live = bot_key == "XSP Live"

bot    = BOT_VIEWS[bot_key]
status = _rj(bot.status_file)
state  = status.get("state", {}) if isinstance(status, dict) else {}
if not isinstance(state, dict): state = {}
trades   = _rt(bot.trades_file)
stats    = _stats(trades)
pos_lst  = _pos(state)
last_sig = status.get("last_signal") if isinstance(status, dict) else None

connected   = bool(status.get("connected"))
paper_mode  = bool(status.get("paper_trading", True))
actual_mode = "PAPER" if paper_mode else "LIVE"
mode_match  = actual_mode == bot.expected_mode

# ── Machine health (from psutil written by the bot process) ─────────────────
_sys = status.get("system", {}) if isinstance(status, dict) else {}
def _sys_row() -> str:
    if not _sys:
        return ""
    cpu  = _sys.get("cpu_pct")
    ru   = _sys.get("ram_used_gb");  rt = _sys.get("ram_total_gb")
    rp   = _sys.get("ram_pct")
    su   = _sys.get("swap_used_mb", 0)
    du   = _sys.get("disk_used_gb"); dt = _sys.get("disk_total_gb")
    ram_col  = "#cf222e" if (rp or 0) > 85 else "#d4a72c" if (rp or 0) > 70 else "#8c959f"
    swap_col = "#cf222e" if (su or 0) > 200 else "#8c959f"
    cpu_col  = "#cf222e" if (cpu or 0) > 80 else "#d4a72c" if (cpu or 0) > 50 else "#8c959f"
    sep = '<span style="color:#d0d7de;margin:0 5px;">·</span>'
    parts = []
    if cpu  is not None: parts.append(f'<span style="color:{cpu_col}">CPU {cpu}%</span>')
    if ru   is not None: parts.append(f'<span style="color:{ram_col}">RAM {ru}/{rt}GB</span>')
    if su   is not None: parts.append(f'<span style="color:{swap_col}">Swap {int(su)}MB</span>')
    if du   is not None: parts.append(f'<span style="color:#8c959f">Disk {du}/{dt}GB</span>')
    return sep.join(parts)

conn_b = _b("CONNECTED", "grn") if connected else _b("OFFLINE", "red")
mode_b = _b(actual_mode, "blu" if paper_mode else "grn")
sync_b = _b("SYNC ✓", "grn") if mode_match else _b("MISMATCH", "amb")

# Bot-process alive indicator — based on age of last status write (bot writes every 2s)
_ts_raw = status.get("ts", "") if isinstance(status, dict) else ""
_ts_age_s = 9999.0
if _ts_raw:
    try:
        _ts_age_s = (datetime.now(UTC) - datetime.fromisoformat(_ts_raw).astimezone(UTC)).total_seconds()
    except Exception:
        pass
if _ts_age_s < 30:
    alive_b = _b("● ALIVE", "grn")
elif _ts_age_s < 300:
    alive_b = _b("● STALE", "amb")
else:
    alive_b = _b("● DOWN", "red")

now_et = datetime.now(ET).strftime("%H:%M:%S  ET")

# ── Topbar — fully inline styles so Streamlit scoping cannot interfere ──────
_S  = "font-family:Inter,system-ui,sans-serif;"
_M  = "font-family:'JetBrains Mono',ui-monospace,monospace;"
bot_qp = bot_key.replace(" ", "+")

def _seg(label, href, active, live=False):
    if active:
        c = "#1a7f37" if live else "#0969da"
        bc = "rgba(26,127,55,.3)" if live else "rgba(9,105,218,.3)"
        s = f"background:#fff;color:{c};border:1px solid {bc};box-shadow:0 1px 2px rgba(31,35,40,.06);"
    else:
        s = "background:transparent;color:#57606a;border:1px solid transparent;"
    return (f'<a href="{href}" target="_self" style="{_S}display:inline-block;padding:5px 14px;border-radius:4px;'
            f'font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;text-decoration:none;{s}">'
            f'{label}</a>')

spx_btn = _seg("📊 SPX Paper", "?bot=SPX+Paper", bot_key == "SPX Paper")
xsp_btn = _seg("🔴 XSP Live",  "?bot=XSP+Live",  bot_key == "XSP Live", live=True)

st.markdown(f"""
<div style="{_S}display:flex;align-items:center;justify-content:space-between;
            background:#ffffff;border:1px solid #d0d7de;border-radius:8px;
            padding:10px 16px;margin-bottom:10px;
            box-shadow:0 1px 2px rgba(31,35,40,.06);">
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="width:32px;height:32px;border-radius:7px;flex-shrink:0;
                background:linear-gradient(135deg,#0969da,#2196f3);
                display:flex;align-items:center;justify-content:center;font-size:15px;">📈</div>
    <div>
      <div style="{_S}font-size:14px;font-weight:700;color:#1f2328;letter-spacing:-.2px;">
        {bot.name}&nbsp; {conn_b}&nbsp; {mode_b}&nbsp; {sync_b}&nbsp; {alive_b}
      </div>
      <div style="{_S}font-size:11px;color:#8c959f;margin-top:1px;">
        {bot.symbol}&nbsp;·&nbsp;{bot.strategy_scope}
      </div>
      {f'<div style="{_S}font-size:10px;margin-top:3px;font-family:\'JetBrains Mono\',ui-monospace,monospace;">{_sys_row()}</div>' if _sys else ''}
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="display:inline-flex;background:#f6f8fa;border:1px solid #d0d7de;
                border-radius:6px;padding:3px;gap:2px;">
      {spx_btn}{xsp_btn}
    </div>
    <span style="{_M}font-size:11px;color:#57606a;background:#f6f8fa;border:1px solid #d0d7de;
                 border-radius:5px;padding:4px 10px;letter-spacing:.4px;">{now_et}</span>
    <a href="?bot={bot_qp}" target="_self" style="{_S}background:#1f2328;color:#fff;text-decoration:none;
       border-radius:6px;font-size:12px;font-weight:600;padding:6px 14px;
       border:1px solid #1f2328;white-space:nowrap;display:inline-block;">↻ Refresh</a>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Live section — auto-refreshes every 2 s ──────────────────────────────────
@st.fragment(run_every=2)
def _live_section() -> None:
    live_status  = _rj(bot.status_file)
    live_state   = live_status.get("state", {}) if isinstance(live_status, dict) else {}
    if not isinstance(live_state, dict): live_state = {}
    live_trades  = _rt(bot.trades_file)
    live_stats   = _stats(live_trades)
    live_pos     = _pos(live_state)
    live_sig     = live_status.get("last_signal") if isinstance(live_status, dict) else None

    wins_n   = int(live_state.get("wins",   0) or 0)
    losses_n = int(live_state.get("losses", 0) or 0)
    tc_color = "#1a7f37" if live_stats["today"]>0 else "#cf222e" if live_stats["today"]<0 else "#1f2328"
    hb       = _ts(live_status.get("ts"))
    skip     = live_state.get("skip_reason_today","") or "—"

    # ── Bot-down alert banner (auto-refreshes every 2s) ──────────────────────
    _live_ts_raw = live_status.get("ts", "") if isinstance(live_status, dict) else ""
    _live_age_s  = 9999.0
    if _live_ts_raw:
        try:
            _live_age_s = (datetime.now(UTC) - datetime.fromisoformat(_live_ts_raw).astimezone(UTC)).total_seconds()
        except Exception:
            pass
    _has_open = len(live_pos) > 0
    if _live_age_s > 300:
        _alert_color, _alert_bg, _alert_icon, _alert_msg = (
            "#cf222e", "rgba(207,34,46,0.07)", "🔴",
            f"Bot process appears DOWN — last heartbeat {int(_live_age_s//60)}m ago."
            + (" <strong>Open position unmonitored — stop-loss not active.</strong>" if _has_open else "")
        )
        st.markdown(
            f'<div style="border:1px solid {_alert_color};background:{_alert_bg};border-radius:6px;'
            f'padding:8px 14px;margin-bottom:8px;font-size:12px;color:{_alert_color};font-family:Inter,sans-serif;">'
            f'{_alert_icon}&nbsp; {_alert_msg}</div>',
            unsafe_allow_html=True,
        )
    elif _live_age_s > 60:
        _alert_color, _alert_bg = "#9a6700", "rgba(154,103,0,0.07)"
        st.markdown(
            f'<div style="border:1px solid {_alert_color};background:{_alert_bg};border-radius:6px;'
            f'padding:8px 14px;margin-bottom:8px;font-size:12px;color:{_alert_color};font-family:Inter,sans-serif;">'
            f'⚠️&nbsp; Bot heartbeat stale — last write {int(_live_age_s)}s ago. May be reconnecting.</div>',
            unsafe_allow_html=True,
        )

    net_liq     = live_status.get("net_liquidation")
    nliq_val    = _cash(net_liq) if net_liq is not None else "—"
    nliq_color  = "#1a7f37" if (net_liq or 0) > 0 else "#1f2328"
    mode_label  = "Paper" if bool(live_status.get("paper_trading", True)) else "Live"

    # ── Underlying price ──────────────────────────────────────────────────────
    now_et      = datetime.now(ET)
    _is_market  = (now_et.weekday() < 5
                   and dtime(9, 30) <= now_et.time() <= dtime(16, 0))
    raw_price   = live_status.get("underlying_price", 0) or 0
    price_source = "ib"
    if not raw_price:
        # IB not streaming — fall back to Yahoo Finance
        spx_yf    = _yf_spx_price()
        raw_price = spx_yf / 10 if bot.symbol == "XSP" else spx_yf
        price_source = "yf"
    if raw_price > 0:
        price_val = f"{raw_price:,.2f}"
        if _is_market and price_source == "ib":
            price_badge = ('<span class="b b-grn" style="font-size:9px;padding:2px 6px;margin-left:6px">'
                           '<span class="dot dg"></span>&nbsp;LIVE</span>')
            price_sub = "Real-time · IB"
        elif _is_market:
            price_badge = ('<span class="b b-grn" style="font-size:9px;padding:2px 6px;margin-left:6px">'
                           'REAL-TIME</span>')
            price_sub = "Real-time · Yahoo"
        else:
            price_badge = ('<span class="b b-amb" style="font-size:9px;padding:2px 6px;margin-left:6px">'
                           'AFTER HRS</span>')
            price_sub = "Last close"
    else:
        price_val   = "—"
        price_badge = ""
        price_sub   = ""

    # ── Net PnL (unrealized) ─────────────────────────────────────────────────
    spread_marks = live_status.get("spread_marks", {}) if isinstance(live_status, dict) else {}
    unrealized_pnl: float = 0.0
    for pos in live_pos:
        strat  = pos.get("strategy", "")
        credit = float(pos.get("entry_credit") or 0)
        contr  = int(pos.get("contracts") or 1)
        mark   = float(spread_marks.get(strat, 0) or 0)
        if credit > 0 and mark > 0:
            unrealized_pnl += (credit - mark) * 100 * contr
    net_pnl       = live_stats["today"] + unrealized_pnl
    net_pnl_color = "#1a7f37" if net_pnl > 0 else "#cf222e" if net_pnl < 0 else "#1f2328"
    net_pnl_val   = _cash(net_pnl, sign=True) if (live_pos or live_stats["today"] != 0) else "—"

    st.markdown(f"""
<div class="strip">
  <div class="sc">
    <div class="sl">{bot.symbol} Price {price_badge}</div>
    <div class="sv" style="font-size:15px;font-weight:600">{price_val}</div>
    <div class="ss">{price_sub}</div>
  </div>
  <div class="sc">
    <div class="sl">Portfolio · {mode_label}</div>
    <div class="sv" style="font-size:15px;font-weight:600;color:{nliq_color}">{nliq_val}</div>
    <div class="ss">Net liquidation · IBKR</div>
  </div>
  <div class="sc">
    <div class="sl">Heartbeat</div>
    <div class="sv" style="font-size:11px">{hb}</div>
    <div class="ss">Last status write</div>
  </div>
  <div class="sc">
    <div class="sl">Net PnL Today</div>
    <div class="sv" style="color:{net_pnl_color}">{net_pnl_val}</div>
    <div class="ss">Realized + unrealized</div>
  </div>
  <div class="sc">
    <div class="sl">Open Positions</div>
    <div class="sv">{len(live_pos)}</div>
    <div class="ss">Active trades</div>
  </div>
  <div class="sc">
    <div class="sl">W / L</div>
    <div class="sv">
      <span style="color:#1a7f37">{wins_n}W</span>
      <span style="color:#8c959f"> / </span>
      <span style="color:#cf222e">{losses_n}L</span>
    </div>
    <div class="ss">All time</div>
  </div>
  <div class="sc">
    <div class="sl">Skip Reason</div>
    <div class="sv" style="font-size:11px;color:#57606a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{skip}</div>
    <div class="ss">Today</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── KPI row ──────────────────────────────────────────────────────────────
    _rule("Performance")
    total, avg, mdd = live_stats["total"], live_stats["avg"], live_stats["mdd"]
    st.markdown('<div class="kpis">' + "".join([
        _kpi("Total PnL",    _cash(total,sign=True),   f"Avg {_cash(avg)} per trade",                  _acc(total)),
        _kpi("Today PnL",    _cash(live_stats['today'],sign=True), "Since midnight ET",                 _acc(live_stats['today'])),
        _kpi("Win Rate",     _pct(live_stats['wr']),    f"{wins_n}W  {losses_n}L  of {int(live_stats['n'])} trades", "t"),
        _kpi("Max Drawdown", _cash(mdd),                "Peak-to-trough",                              "r" if mdd<0 else ""),
        _kpi("Total Trades", str(int(live_stats['n'])), f"Avg {_cash(avg,sign=True)} each",             "b"),
    ]) + '</div>', unsafe_allow_html=True)

    # ── Open positions — card view (sorted by entry time asc) ────────────────
    _rule("Open Positions")
    if not live_pos:
        st.markdown('<div class="nd">No open positions</div>', unsafe_allow_html=True)
    else:
        def _pos_ts(p):
            try: return datetime.fromisoformat(str(p.get("entry_ts","")).replace("Z","+00:00"))
            except: return datetime.min.replace(tzinfo=UTC)

        _POS_CAP = 30
        _all_pos = sorted(live_pos, key=_pos_ts, reverse=True)  # newest first
        _sorted_pos = _all_pos[:_POS_CAP]
        _total_unreal = 0.0
        _has_marks = False
        _cards_html = ""
        for p in _sorted_pos:
            entry_ts_raw = p.get("entry_ts", "")
            if entry_ts_raw:
                try:
                    _et_dt = datetime.fromisoformat(str(entry_ts_raw).replace("Z", "+00:00")).astimezone(ET)
                    entry_time_card = _et_dt.strftime("%Y-%m-%d  %H:%M:%S")
                except Exception:
                    entry_time_card = str(entry_ts_raw)
            else:
                entry_time_card = "—"

            strat_key   = p.get("strategy", "")
            _sp_strike  = p.get("short_put_strike") or p.get("short_call_strike") or ""
            _mark_key   = f"{strat_key}_{_sp_strike}" if _sp_strike else strat_key
            _cred       = float(p.get("entry_credit") or 0)
            _contr      = int(p.get("contracts") or 1)
            _mark       = float(spread_marks.get(_mark_key, 0) or 0)
            if _mark > 0 and _cred > 0:
                _pnl_val  = (_cred - _mark) * 100 * _contr
                _pnl_col  = "#1a7f37" if _pnl_val >= 0 else "#cf222e"
                _pnl_str  = f'{"+" if _pnl_val>=0 else ""}${_pnl_val:,.2f}'
                _mark_str = f"{_mark:.2f}"
                _total_unreal += _pnl_val
                _has_marks = True
            else:
                _pnl_col, _pnl_str, _mark_str = "#8c959f", "—", "—"

            _exp_raw = str(p.get('expiry',''))
            _exp_fmt = datetime.strptime(_exp_raw, '%Y%m%d').strftime('%d %b %Y') if _exp_raw.isdigit() and len(_exp_raw) == 8 else (_exp_raw or '—')
            _cards_html += f"""<div class="pc">
              <div class="pc-h"><div class="pc-n">{p.get('strategy','—')}</div>{_b('OPEN','grn')}</div>
              <div class="pf">
                <div><div class="pfl">Contracts</div><div class="pfv">{p.get('contracts','—')}</div></div>
                <div><div class="pfl">Entry Credit</div><div class="pfv">{p.get('entry_credit','—')}</div></div>
                <div><div class="pfl">Stop</div><div class="pfv" style="color:#cf222e">{p.get('stop_price','—')}</div></div>
                <div><div class="pfl">Target</div><div class="pfv" style="color:#1a7f37">{p.get('profit_target_price','—')}</div></div>
                <div><div class="pfl">Expiry</div><div class="pfv">{_exp_fmt}</div></div>
                <div><div class="pfl">Entry Time</div><div class="pfv">{entry_time_card}</div></div>
                <div><div class="pfl">Mark</div><div class="pfv">{_mark_str}</div></div>
                <div><div class="pfl">Unrealized P&amp;L</div><div class="pfv" style="color:{_pnl_col};font-weight:600">{_pnl_str}</div></div>
              </div>
            </div>"""

        # Summary bar above the scrollable card list
        _n_total = len(_all_pos)
        _n_shown = len(_sorted_pos)
        _unreal_col = "#1a7f37" if _total_unreal >= 0 else "#cf222e"
        _unreal_disp = (f'<span style="color:{_unreal_col};font-weight:600;">{"+" if _total_unreal>=0 else ""}${_total_unreal:,.2f}</span>' if _has_marks else '<span style="color:#8c959f;">—</span>')
        _cap_note = (f' &nbsp;<span style="color:#8c959f;font-weight:400;">· showing {_n_shown} of {_n_total} most recent</span>' if _n_total > _POS_CAP else "")
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:16px;margin-bottom:8px;">'
            f'<span style="font-family:Inter,sans-serif;font-size:11px;font-weight:700;color:#57606a;">'
            f'{_n_total} position{"s" if _n_total!=1 else ""} open{_cap_note}</span>'
            f'<span style="font-family:Inter,sans-serif;font-size:11px;color:#57606a;">Unrealized P&amp;L (shown): {_unreal_disp}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Scrollable card container — caps height at ~3 cards, scrolls for more
        st.markdown(
            f'<div style="max-height:480px;overflow-y:auto;padding-right:4px;">{_cards_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Last signal ───────────────────────────────────────────────────────────
    if isinstance(live_sig, dict) and live_sig:
        _rule("Last Signal")
        dec = str(live_sig.get("decision","—")).upper()
        st.markdown('<div class="kpis">' + "".join([
            _kpi("Decision",  dec, "", "g" if dec=="TRADE" else "r" if dec=="SKIP" else ""),
            _kpi("Strategy",  str(live_sig.get("strategy","—")), "", "t"),
            _kpi("SPX / XSP", str(live_sig.get("spx","—")),      "", "b"),
            _kpi("VIX",       str(live_sig.get("vix","—")),       "", ""),
            _kpi("Quote Mid", str(live_sig.get("quote_mid","—")),
                 f"DTE {live_sig.get('dte','—')}  ·  Exp {live_sig.get('expiry','—')}", ""),
        ]) + '</div>', unsafe_allow_html=True)
        if live_sig.get("reason"):
            st.markdown(
                f'<div class="reason"><span class="reason-k">Reason</span>{live_sig["reason"]}</div>',
                unsafe_allow_html=True)

_live_section()

# ── Activity tabs ─────────────────────────────────────────────────────────────
_rule("Activity Log")

t_trades, t_sigs, t_orders, t_log = st.tabs([
    "  Trades  ", "  Signals  ", "  Orders  ", "  System Log  "
])

with t_trades:
    # ── Open positions table ──────────────────────────────────────────────────
    _open_pos = pos_lst  # use the outer-scope list (not live-fragment, but fine for display)
    if _open_pos:
        _M2 = "font-family:'JetBrains Mono',ui-monospace,monospace;"
        _S2 = "font-family:Inter,system-ui,sans-serif;"
        _TH = (f"background:#f6f8fa;color:#8c959f;{_S2}font-size:9px;font-weight:700;"
               "letter-spacing:.7px;text-transform:uppercase;padding:7px 12px;"
               "border-bottom:1px solid #d0d7de;white-space:nowrap;text-align:left;")
        _TD = f"{_M2}font-size:12px;color:#1f2328;padding:8px 12px;border-bottom:1px solid #eaeef2;white-space:nowrap;"
        _sm = status.get("spread_marks", {}) if isinstance(status, dict) else {}

        cols = ["Strategy","Expiry","Short Strike","Long Strike","Contracts",
                "Entry Credit","Stop","Target","Entry Time ET","Mark","P&L"]
        hdr  = "".join(f'<th style="{_TH}">{c}</th>' for c in cols)

        def _pos_strike_str(legs_raw, direction, right):
            return [l["strike"] for l in legs_raw
                    if l.get("direction","").upper()==direction and l.get("right","").upper()==right]

        def _tab_pos_ts(p):
            try: return datetime.fromisoformat(str(p.get("entry_ts","")).replace("Z","+00:00"))
            except: return datetime.min.replace(tzinfo=UTC)
        _TAB_CAP = 30
        _open_pos_all = sorted(_open_pos, key=_tab_pos_ts, reverse=True)
        _open_pos_shown = _open_pos_all[:_TAB_CAP]
        rows_html = ""
        for p in _open_pos_shown:
            strat      = p.get("strategy", "—")
            expiry     = p.get("expiry", "—")
            legs_raw   = p.get("legs", []) if isinstance(p.get("legs"), list) else []
            sp_put  = (_pos_strike_str(legs_raw,"SHORT","P") or [p.get("short_put_strike")])[0] or None
            lp_put  = (_pos_strike_str(legs_raw,"LONG", "P") or [p.get("long_put_strike")])[0]  or None
            sp_call = (_pos_strike_str(legs_raw,"SHORT","C") or [p.get("short_call_strike")])[0] or None
            lp_call = (_pos_strike_str(legs_raw,"LONG", "C") or [p.get("long_call_strike")])[0]  or None
            def _sk(put, call):
                parts = ([f"{put:.0f}P"] if put else []) + ([f"{call:.0f}C"] if call else [])
                return " / ".join(parts) or "—"
            contracts  = p.get("contracts", "—")
            entry_cred = p.get("entry_credit")
            stop_p     = p.get("stop_price")
            target_p   = p.get("profit_target_price")
            entry_ts_raw = p.get("entry_ts", "")
            if entry_ts_raw:
                try:
                    _edt = datetime.fromisoformat(str(entry_ts_raw).replace("Z","+00:00")).astimezone(ET)
                    entry_time = _edt.strftime("%Y-%m-%d  %H:%M ET")
                except Exception:
                    entry_time = str(entry_ts_raw)
            else:
                entry_time = "—"
            _sp = p.get("short_put_strike") or p.get("short_call_strike") or ""
            _mk = f"{strat}_{_sp}" if _sp else strat
            mark = float(_sm.get(_mk, 0) or 0)
            mark_s = f"{mark:.2f}" if mark > 0 else "—"
            if entry_cred and mark > 0 and contracts:
                pv  = (float(entry_cred) - mark) * 100 * int(contracts)
                pc  = "#1a7f37" if pv >= 0 else "#cf222e"
                pnl = f'<span style="color:{pc};font-weight:600;">{"+" if pv>=0 else ""}${pv:,.2f}</span>'
            else:
                pnl = "—"
            def _fv(v, color=None):
                s = f"{float(v):.2f}" if v is not None else "—"
                return (f'<span style="color:{color};font-weight:600;">{s}</span>' if color and v is not None else s)
            rows_html += (
                f'<tr>'
                f'<td style="{_TD}font-weight:600;color:#0969da;">{strat}</td>'
                f'<td style="{_TD}">{expiry}</td>'
                f'<td style="{_TD}">{_sk(sp_put, sp_call)}</td>'
                f'<td style="{_TD}">{_sk(lp_put, lp_call)}</td>'
                f'<td style="{_TD}text-align:center;">{contracts}</td>'
                f'<td style="{_TD}">{_fv(entry_cred)}</td>'
                f'<td style="{_TD}">{_fv(stop_p,  "#cf222e")}</td>'
                f'<td style="{_TD}">{_fv(target_p,"#1a7f37")}</td>'
                f'<td style="{_TD}font-size:11px;color:#57606a;">{entry_time}</td>'
                f'<td style="{_TD}">{mark_s}</td>'
                f'<td style="{_TD}">{pnl}</td>'
                f'</tr>'
            )
        _label_style = (
            "font-family:Inter,sans-serif;font-size:9px;font-weight:700;"
            "letter-spacing:.8px;text-transform:uppercase;color:#8c959f;"
            "margin-bottom:6px;margin-top:2px;"
        )
        _tab_total = len(_open_pos_all)
        _tab_cap_note = f" — showing {_TAB_CAP} of {_tab_total} most recent" if _tab_total > _TAB_CAP else ""
        st.markdown(
            f'<div style="{_label_style}">Open Positions ({_tab_total}){_tab_cap_note}</div>'
            f'<div style="overflow-x:auto;overflow-y:auto;max-height:320px;margin-bottom:16px;border:1px solid #d0d7de;border-radius:8px;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead style="position:sticky;top:0;z-index:1;"><tr>{hdr}</tr></thead><tbody>{rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )

    # ── Closed trades ─────────────────────────────────────────────────────────
    _label_style = (
        "font-family:Inter,sans-serif;font-size:9px;font-weight:700;"
        "letter-spacing:.8px;text-transform:uppercase;color:#8c959f;"
        "margin-bottom:6px;"
    )
    st.markdown(f'<div style="{_label_style}">Closed Trades</div>', unsafe_allow_html=True)
    if trades.empty:
        st.markdown(
            '<div style="font-family:Inter,sans-serif;font-size:12px;color:#8c959f;'
            'padding:12px 4px;">No closed trades recorded yet</div>',
            unsafe_allow_html=True,
        )
    else:
        want = ["Date","Entry","Exit","Short","Long","SPX","VIX","Credit","Cts","PnL/ct","Total PnL","W/L","Exit Reason","Strategy","Notes"]
        show = [c for c in want if c in trades.columns]
        disp = trades[show].tail(250).iloc[::-1].copy()
        if "Total PnL" in disp.columns:
            disp["Total PnL"] = disp["Total PnL"].apply(
                lambda x: (f"+${x:,.2f}" if x>0 else f"-${abs(x):,.2f}") if pd.notna(x) else "—")
        st.dataframe(disp, use_container_width=True, hide_index=True, height=390)

with t_sigs:
    sigs = _rjl(bot.signal_events_file, 300)
    if not sigs:
        st.markdown('<div class="nd">No signal events yet</div>', unsafe_allow_html=True)
    else:
        sdf = pd.DataFrame(sigs)
        want = ["ts","event","decision","reason","strategy","contracts","spx","vix","dte","expiry","quote_mid"]
        st.dataframe(sdf[[c for c in want if c in sdf.columns]].tail(300).iloc[::-1],
                     use_container_width=True, hide_index=True, height=320)

with t_orders:
    ords = _rjl(bot.order_events_file, 300)
    if not ords:
        st.markdown('<div class="nd">No order events yet</div>', unsafe_allow_html=True)
    else:
        odf = pd.DataFrame(ords)
        want = ["ts","event","strategy","reason","combo_order_id","entry_credit","contracts","pnl"]
        st.dataframe(odf[[c for c in want if c in odf.columns]].tail(300).iloc[::-1],
                     use_container_width=True, hide_index=True, height=320)

with t_log:
    lines = _rl(bot.system_log_file, 200)
    if not lines:
        st.markdown('<div class="nd">No system logs yet</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="log-w"><div class="log-h">System Log</div>', unsafe_allow_html=True)
        st.code("\n".join(lines), language="text")
        st.markdown("</div>", unsafe_allow_html=True)
