from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

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
        name="XSP Live Bot", symbol="XSP", expected_mode="LIVE",
        strategy_scope="BULL PUT SPREAD",
        status_file=ROOT/"data"/"xsp"/"status.json",
        trades_file=ROOT/"data"/"xsp"/"trades.csv",
        signal_events_file=ROOT/"logs"/"xsp"/"signal_events.jsonl",
        order_events_file=ROOT/"logs"/"xsp"/"order_events.jsonl",
        system_log_file=ROOT/"logs"/"xsp"/"system.log",
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

/* ── Topbar: one card spanning the inner nested row ─────── */
/* Full path prevents bleeding into any nested columns      */
.stApp .main .block-container > div
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]:first-of-type
  > [data-testid="stColumn"]:first-child
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"] {
  background: var(--p1) !important;
  border: 1px solid var(--bd) !important;
  border-radius: 8px !important;
  box-shadow: var(--shadow-sm) !important;
  padding: 8px 16px !important;
  margin-bottom: 10px !important;
  align-items: center !important;
}
.stApp .main .block-container > div
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]:first-of-type
  > [data-testid="stColumn"]:first-child
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"] { padding: 0 !important; }
.stApp .main .block-container > div
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]:first-of-type
  > [data-testid="stColumn"]:first-child
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]
  .stElementContainer { margin-bottom: 0 !important; }
/* Outer row: zero column padding so card hugs tightly */
.stApp .main .block-container > div
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]:first-of-type
  > [data-testid="stColumn"] { padding-left: 0 !important; padding-right: 0 !important; }
.stApp .main .block-container > div
  > [data-testid="stVerticalBlock"]
  > [data-testid="stHorizontalBlock"]:first-of-type
  > [data-testid="stColumn"]:last-child { padding-left: 10px !important; }

/* ── Segmented control inside card ─────────────────────── */
[data-testid="stSegmentedControl"] {
  background: var(--p2) !important; border: 1px solid var(--bd) !important;
  border-radius: 6px !important; padding: 3px !important;
}
[data-testid="stSegmentedControl"] button {
  font-size: 12px !important; font-weight: 600 !important;
  font-family: var(--sans) !important; border-radius: 4px !important;
  border: none !important; color: var(--t2) !important;
  background: transparent !important; padding: 4px 12px !important;
}
[data-testid="stSegmentedControl"] button[aria-checked="true"] {
  background: var(--p1) !important; color: var(--t1) !important;
  border: 1px solid var(--bd) !important; box-shadow: var(--shadow-sm) !important;
}

/* ── Refresh button dark ────────────────────────────────── */
button[data-testid="baseButton-primary"] {
  background: #1f2328 !important; border: 1px solid #1f2328 !important;
  color: #ffffff !important; border-radius: 6px !important;
  font-size: 12px !important; font-weight: 600 !important;
}
button[data-testid="baseButton-primary"]:hover {
  background: #2d333a !important;
}

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
  padding: 5px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  background: transparent;
  color: var(--t2);
  transition: all .15s;
  white-space: nowrap;
  font-family: var(--sans);
}
.seg-btn:hover { background: var(--p3); color: var(--t1); }
.seg-btn.active {
  background: var(--p1);
  color: var(--t1);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--bd);
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
  display: grid; grid-template-columns: repeat(5, 1fr);
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


# ── Bot selection via session state ─────────────────────────────────────────

bot_key = st.session_state.get("bot_seg", "SPX Paper")
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

conn_b = _b("● CONNECTED", "grn") if connected else _b("● OFFLINE", "red")
mode_b = _b(actual_mode, "blu" if paper_mode else "grn")
sync_b = _b("SYNC ✓", "grn") if mode_match else _b("MISMATCH", "amb")
now_et = datetime.now().strftime("%H:%M:%S  ET")

# Outer row: card wrapper col + refresh button
# CSS turns the INNER nested row into the card
c_outer, c_ref = st.columns([9.3, 0.7], vertical_alignment="center")

with c_ref:
    if st.button("↻  Refresh", type="primary", use_container_width=True):
        st.rerun()

with c_outer:
    # Inner flat 3-col row — CSS targets this specific nested stHorizontalBlock as the card
    c_info, c_switch, c_clock = st.columns([5.8, 3.0, 1.8], vertical_alignment="center")
    with c_info:
        st.markdown(f"""
        <div class="tb-left">
          <div class="tb-icon">📈</div>
          <div>
            <div class="tb-name">{bot.name} &nbsp; {conn_b} &nbsp; {mode_b} &nbsp; {sync_b}</div>
            <div class="tb-sub">{bot.symbol} &nbsp;·&nbsp; {bot.strategy_scope}</div>
          </div>
        </div>""", unsafe_allow_html=True)
    with c_switch:
        st.segmented_control(
            "bot", list(BOT_VIEWS.keys()),
            default=bot_key, label_visibility="collapsed", key="bot_seg"
        )
    with c_clock:
        st.markdown(f'<div class="et-clock" style="text-align:right">{now_et}</div>',
                    unsafe_allow_html=True)

# ── Status strip ─────────────────────────────────────────────────────────────
hb   = _ts(status.get("ts"))
skip = state.get("skip_reason_today","") or "—"
wins_n   = int(state.get("wins",0) or 0)
losses_n = int(state.get("losses",0) or 0)
tc_color = "#1a7f37" if stats["today"]>0 else "#cf222e" if stats["today"]<0 else "#1f2328"

st.markdown(f"""
<div class="strip">
  <div class="sc">
    <div class="sl">Heartbeat</div>
    <div class="sv" style="font-size:11px">{hb}</div>
    <div class="ss">Last status write</div>
  </div>
  <div class="sc">
    <div class="sl">Today PnL</div>
    <div class="sv" style="color:{tc_color}">{_cash(stats['today'],sign=True)}</div>
    <div class="ss">Since midnight ET</div>
  </div>
  <div class="sc">
    <div class="sl">Open Positions</div>
    <div class="sv">{len(pos_lst)}</div>
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

# ── KPI row ───────────────────────────────────────────────────────────────────
_rule("Performance")

total, avg, mdd = stats["total"], stats["avg"], stats["mdd"]
st.markdown('<div class="kpis">' + "".join([
    _kpi("Total PnL",    _cash(total,sign=True),   f"Avg {_cash(avg)} per trade",                  _acc(total)),
    _kpi("Today PnL",    _cash(stats['today'],sign=True), "Since midnight ET",                      _acc(stats['today'])),
    _kpi("Win Rate",     _pct(stats['wr']),         f"{wins_n}W  {losses_n}L  of {int(stats['n'])} trades", "t"),
    _kpi("Max Drawdown", _cash(mdd),                "Peak-to-trough",                              "r" if mdd<0 else ""),
    _kpi("Total Trades", str(int(stats['n'])),      f"Avg {_cash(avg,sign=True)} each",             "b"),
]) + '</div>', unsafe_allow_html=True)

# ── Open positions ────────────────────────────────────────────────────────────
_rule("Open Positions")

if not pos_lst:
    st.markdown('<div class="nd">No open positions</div>', unsafe_allow_html=True)
else:
    for p in pos_lst:
        legs_s = _legs(p.get("legs") if isinstance(p.get("legs"),list) else [])
        st.markdown(f"""
        <div class="pc">
          <div class="pc-h"><div class="pc-n">{p.get('strategy','—')}</div>{_b('OPEN','grn')}</div>
          <div class="pf">
            <div><div class="pfl">Contracts</div><div class="pfv">{p.get('contracts','—')}</div></div>
            <div><div class="pfl">Entry Credit</div><div class="pfv">{p.get('entry_credit','—')}</div></div>
            <div><div class="pfl">Stop</div><div class="pfv" style="color:#cf222e">{p.get('stop_price','—')}</div></div>
            <div><div class="pfl">Target</div><div class="pfv" style="color:#1a7f37">{p.get('profit_target_price','—')}</div></div>
            <div><div class="pfl">Expiry</div><div class="pfv">{p.get('expiry','—')}</div></div>
            <div><div class="pfl">Entry Time</div><div class="pfv">{_ts(p.get('entry_ts'))}</div></div>
            <div style="grid-column:span 2"><div class="pfl">Legs</div><div class="pfv">{legs_s}</div></div>
          </div>
        </div>""", unsafe_allow_html=True)

# ── Last signal ───────────────────────────────────────────────────────────────
if isinstance(last_sig, dict) and last_sig:
    _rule("Last Signal")
    dec = str(last_sig.get("decision","—")).upper()
    st.markdown('<div class="kpis">' + "".join([
        _kpi("Decision",  dec, "", "g" if dec=="TRADE" else "r" if dec=="SKIP" else ""),
        _kpi("Strategy",  str(last_sig.get("strategy","—")), "", "t"),
        _kpi("SPX / XSP", str(last_sig.get("spx","—")),      "", "b"),
        _kpi("VIX",       str(last_sig.get("vix","—")),       "", ""),
        _kpi("Quote Mid", str(last_sig.get("quote_mid","—")),
             f"DTE {last_sig.get('dte','—')}  ·  Exp {last_sig.get('expiry','—')}", ""),
    ]) + '</div>', unsafe_allow_html=True)
    if last_sig.get("reason"):
        st.markdown(
            f'<div class="reason"><span class="reason-k">Reason</span>{last_sig["reason"]}</div>',
            unsafe_allow_html=True)

# ── Activity tabs ─────────────────────────────────────────────────────────────
_rule("Activity Log")

t_trades, t_sigs, t_orders, t_log = st.tabs([
    "  Trades  ", "  Signals  ", "  Orders  ", "  System Log  "
])

with t_trades:
    if trades.empty:
        st.markdown('<div class="nd">No trades recorded yet</div>', unsafe_allow_html=True)
    else:
        want = ["Date","Entry","Exit","Short","Long","SPX","VIX","Credit","Cts","PnL/ct","Total PnL","W/L","Exit Reason","Strategy","Notes"]
        show = [c for c in want if c in trades.columns]
        disp = trades[show].tail(250).iloc[::-1].copy()
        if "Total PnL" in disp.columns:
            disp["Total PnL"] = disp["Total PnL"].apply(
                lambda x: (f"+${x:,.2f}" if x>0 else f"-${abs(x):,.2f}") if pd.notna(x) else "—")
        st.dataframe(disp, use_container_width=True, hide_index=True, height=320)

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
