from __future__ import annotations

import json
import os
import re
import sys
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

ROOT        = Path(__file__).resolve().parent
REPO_ROOT   = ROOT.parent
WEBULL_DATA = ROOT / "data" / "webull_live" / "vG_spx_w50_vix25"
WEBULL_LOGS = ROOT / "logs" / "webull_live" / "vG_spx_w50_vix25"

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
        name="SPX-XSP-Live Bot", symbol="XSP", expected_mode="PAPER",
        strategy_scope="BULL PUT SPREAD",
        status_file=ROOT/"data"/"xsp_live"/"vG_otm010_w5_vix25"/"status.json",
        trades_file=ROOT/"data"/"xsp_live"/"vG_otm010_w5_vix25"/"trades.csv",
        signal_events_file=ROOT/"logs"/"xsp_live"/"vG_otm010_w5_vix25"/"signal_events.jsonl",
        order_events_file=ROOT/"logs"/"xsp_live"/"vG_otm010_w5_vix25"/"order_events.jsonl",
        system_log_file=ROOT/"logs"/"xsp_live"/"vG_otm010_w5_vix25"/"daily_summary.log",
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
    try:
        ticker = yf.Ticker("^GSPC")
        info = ticker.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if price and price > 0:
            return float(price)
        hist = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=60)
def _yf_vix_price() -> float:
    try:
        ticker = yf.Ticker("^VIX")
        info = ticker.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if price and price > 0:
            return float(price)
        hist = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0

@st.cache_data(ttl=60)
def _yf_quote_full(symbol: str) -> tuple[float, float, float]:
    """Return (last_price, abs_change_from_prev_close, pct_change). 60s cache."""
    try:
        tk = yf.Ticker(symbol)
        fi = tk.fast_info
        cur = float(getattr(fi, "last_price", 0) or getattr(fi, "regularMarketPrice", 0) or 0)
        prev = float(getattr(fi, "previous_close", 0) or 0)
        if not (cur and prev):
            hist = tk.history(period="2d", interval="1d")
            if not hist.empty and len(hist) >= 2:
                cur = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
        if cur and prev:
            abs_chg = cur - prev
            pct = abs_chg / prev * 100
            return cur, abs_chg, pct
        if cur:
            return cur, 0.0, 0.0
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _render_delta(abs_chg: float, pct: float, kind: str = "spx") -> str:
    """Render '+12.75 (0.17%) ▲ today' style delta. Color by sign."""
    if abs_chg == 0 and pct == 0:
        return '<div class="ss" style="color:#8c959f">— flat</div>'
    up = abs_chg > 0
    color = "#1a7f37" if up else "#cf222e"
    arrow = "▲" if up else "▼"
    sign = "+" if up else "−"
    fmt = f"{sign}{abs(abs_chg):.2f}" if kind == "spx" else f"{sign}{abs(abs_chg):.2f}"
    return (
        f'<div class="ss" style="color:{color};font-weight:600;font-size:12px">'
        f'{fmt} ({abs(pct):.2f}%) {arrow} today</div>'
    )


@st.cache_data(ttl=120)
def _webull_nvda_shares() -> str:
    """Fetch NVDA share count from Webull account positions API (account_v2)."""
    try:
        env_path = REPO_ROOT / ".webull_env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                m = re.match(r'export\s+(\w+)="([^"]+)"', line.strip())
                if m:
                    os.environ[m.group(1)] = m.group(2)

        sys.path.insert(0, str(REPO_ROOT))
        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient

        tc = TradeClient(ApiClient(
            app_key=os.environ["WEBULL_APP_KEY"],
            app_secret=os.environ["WEBULL_APP_SECRET"],
            region_id="us",
        ))
        resp = tc.account_v2.get_account_position("QJJGQCBGAN8M2JL2C1OA1J37KB")
        body = json.loads(resp.text)
        positions = body if isinstance(body, list) else body.get("data", [])
        for pos in positions:
            if not isinstance(pos, dict): continue
            sym = str(pos.get("symbol", "")).upper()
            if sym == "NVDA":
                return str(pos.get("quantity") or pos.get("position") or pos.get("qty") or "?")
        return "0"
    except Exception:
        return "—"

def _webull_mark(pos: dict) -> float | None:
    """Live spread mark for an open Webull position via yfinance."""
    try:
        short = float(pos.get("short_strike", 0))
        long_ = float(pos.get("long_strike", 0))
        expiry = str(pos.get("expiry", ""))
        sym = str(pos.get("yf_options_symbol", "^SPX"))
        if not (short and long_ and expiry):
            return None
        chain = yf.Ticker(sym).option_chain(expiry)
        puts = chain.puts.set_index("strike")
        short_row = puts.loc[short]
        long_row  = puts.loc[long_]
        sb = float(short_row.get("bid", 0) or 0)
        sa = float(short_row.get("ask", 0) or 0)
        lb = float(long_row.get("bid", 0) or 0)
        la = float(long_row.get("ask", 0) or 0)
        if sb <= 0 or la <= 0:
            return None
        return round((sb + sa) / 2 - (lb + la) / 2, 2)
    except Exception:
        return None

def _rt_webull(p: Path) -> pd.DataFrame:
    if not p.exists() or p.stat().st_size == 0: return pd.DataFrame()
    try: df = pd.read_csv(p)
    except: return pd.DataFrame()
    for c in ["PnL pts", "PnL USD", "Entry Credit", "Short Strike", "Long Strike"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def _rt(p):
    if not p.exists() or p.stat().st_size == 0: return pd.DataFrame()
    try: df = pd.read_csv(p)
    except: return pd.DataFrame()
    if df.empty: return df
    df = df.rename(columns={
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

def _webull_stats(df: pd.DataFrame) -> dict:
    z = dict(total=0., avg=0., wr=0., mdd=0., n=0., today=0.)
    if df.empty or "PnL USD" not in df.columns: return z
    pnl = pd.to_numeric(df["PnL USD"], errors="coerce").fillna(0.)
    n = len(df)
    wins = int((pnl > 0).sum())
    today = datetime.now().strftime("%Y-%m-%d")
    tp = float(pnl[df["Date"].astype(str) == today].sum()) if "Date" in df.columns else 0.
    return dict(total=float(pnl.sum()), avg=float(pnl.mean()) if n else 0.,
                wr=wins / n if n else 0., mdd=_mdd(pnl), n=float(n), today=tp)

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

def _seg(label, href, active, live=False):
    if active:
        c = "#1a7f37" if live else "#0969da"
        bc = "rgba(26,127,55,.3)" if live else "rgba(9,105,218,.3)"
        s = f"background:#fff;color:{c};border:1px solid {bc};box-shadow:0 1px 2px rgba(31,35,40,.06);"
    else:
        s = "background:transparent;color:#57606a;border:1px solid transparent;"
    return (f'<a href="{href}" target="_self" style="font-family:Inter,system-ui,sans-serif;display:inline-block;'
            f'padding:5px 14px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;'
            f'white-space:nowrap;text-decoration:none;{s}">{label}</a>')


# ── Query params ──────────────────────────────────────────────────────────────

broker    = st.query_params.get("broker", "webull").lower()
is_webull = broker == "webull"
bot_key   = "XSP Live"  # IBKR always uses XSP Live
_M  = "font-family:'JetBrains Mono',ui-monospace,monospace;"
_S  = "font-family:Inter,system-ui,sans-serif;"
now_et = datetime.now(ET).strftime("%H:%M:%S  ET")

# ── Topbar ────────────────────────────────────────────────────────────────────

xsp_btn    = _seg("🏦 IBKR",    "?broker=ibkr",    not is_webull)
webull_btn = _seg("🔴 Webull",  "?broker=webull",   is_webull,     live=True)

if not is_webull:
    bot       = BOT_VIEWS[bot_key]
    status    = _rj(bot.status_file)
    state_raw = status.get("state", {}) if isinstance(status, dict) else {}
    if not isinstance(state_raw, dict): state_raw = {}
    refresh_href = "?broker=ibkr"
else:
    refresh_href = "?broker=webull"

# Navigation bar (static — broker toggle, clock, refresh)
st.html(f"""
<div style="{_S}display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-bottom:6px;">
  <div style="display:inline-flex;background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:3px;gap:2px;">{xsp_btn}{webull_btn}</div>
  <span style="{_M}font-size:11px;color:#57606a;background:#f6f8fa;border:1px solid #d0d7de;border-radius:5px;padding:4px 10px;letter-spacing:.4px;">{now_et}</span>
  <a href="{refresh_href}" target="_self" style="{_S}background:#1f2328;color:#fff;text-decoration:none;border-radius:6px;font-size:12px;font-weight:600;padding:6px 14px;border:1px solid #1f2328;white-space:nowrap;display:inline-block;">↻ Refresh</a>
</div>
""")

# Status card placeholder — filled by the live fragment (IBKR) or Webull fragment
_topbar_slot = st.empty()


# ══════════════════════════════════════════════════════════════════════════════
# WEBULL VIEW
# ══════════════════════════════════════════════════════════════════════════════

if is_webull:

    @st.fragment(run_every=30)
    def _webull_live() -> None:
        wb_state   = _rj(WEBULL_DATA / "state.json")
        wb_hb      = _rj(WEBULL_DATA / "heartbeat.json")
        wb_trades  = _rt_webull(WEBULL_DATA / "trades.csv")
        wb_stats   = _webull_stats(wb_trades)

        open_pos = wb_state.get("open_position")
        has_pos  = isinstance(open_pos, dict) and open_pos

        wins_n   = int(wb_state.get("wins", 0) or 0)
        losses_n = int(wb_state.get("losses", 0) or 0)

        # Heartbeat age
        _wb_ts  = wb_hb.get("ts", "")
        _wb_age = 9999.0
        if _wb_ts:
            try: _wb_age = (datetime.now(UTC) - datetime.fromisoformat(_wb_ts).astimezone(UTC)).total_seconds()
            except: pass
        hb_disp = _ts(_wb_ts)
        if _wb_age < 60:
            hb_rel = f"{int(_wb_age)}s ago"
        elif _wb_age < 3600:
            hb_rel = f"{int(_wb_age // 60)}m ago"
        else:
            hb_rel = f"{int(_wb_age // 3600)}h {int((_wb_age % 3600) // 60)}m ago"
        hb_color = "#1a7f37" if _wb_age < 120 else "#9a6700" if _wb_age < 900 else "#cf222e"

        # Live status card for Webull
        _wb_alv = (_b("● ALIVE","grn") if _wb_age < 120
                   else _b("● STALE","amb") if _wb_age < 900
                   else _b("● DOWN","red"))
        with _topbar_slot:
            st.html(f"""
<div style="{_S}display:flex;align-items:center;gap:10px;background:#ffffff;border:1px solid #d0d7de;border-radius:8px;padding:10px 16px;margin-bottom:10px;box-shadow:0 1px 2px rgba(31,35,40,.06);">
  <div style="width:32px;height:32px;border-radius:7px;flex-shrink:0;background:linear-gradient(135deg,#1a7f37,#2ea043);display:flex;align-items:center;justify-content:center;font-size:15px;">📈</div>
  <div>
    <div style="{_S}font-size:14px;font-weight:700;color:#1f2328;letter-spacing:-.2px;">Webull SPXW Bot&nbsp; {_b('LIVE','grn')}&nbsp; {_wb_alv}</div>
    <div style="{_S}font-size:11px;color:#8c959f;margin-top:1px;">SPXW · Bull Put Spread · 0DTE · Webull</div>
  </div>
</div>
""")

        # ── Chain source pill (added 2026-05-20) ─────────────────────────
        # Chain quote provider drives the LIMIT price → most consequential
        # for fill quality. SPX/VIX values displayed below in their own
        # cells; their sources matter less for execution so we don't
        # clutter the strip with them.
        _chain_src = (wb_hb.get("chain_source") or "unknown").lower()
        if _chain_src == "ibkr":
            _cs_bg, _cs_fg, _cs_txt = "#dafbe1", "#1a7f37", "IBKR"
        elif _chain_src == "yfinance":
            _cs_bg, _cs_fg, _cs_txt = "#fff8c5", "#9a6700", "yfinance · ~15min stale"
        else:
            _cs_bg, _cs_fg, _cs_txt = "#eaeef2", "#656d76", "—"
        st.markdown(
            f'<div style="margin:4px 0 10px 0;font-family:Inter,sans-serif;">'
            f'<span style="font-size:10px;color:#8c959f;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.4px;margin-right:8px;">data feed</span>'
            f'<span style="display:inline-flex;align-items:center;background:{_cs_bg};'
            f'color:{_cs_fg};padding:3px 10px;border-radius:12px;font-size:11px;'
            f'font-weight:600;font-family:Inter,sans-serif;">Chain: {_cs_txt}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Entry-gate GO/NO-GO lights (added 2026-06-01) ────────────────
        # Three lights show WHY an order is/!isn't placed, so a screenshot
        # answers "why no trade" without asking: Direction / VIX / Credit.
        def _light(label: str, ok, detail: str) -> str:
            if ok is True:
                bg, fg, tag = "#dafbe1", "#1a7f37", "GO"
            elif ok is False:
                bg, fg, tag = "#ffebe9", "#cf222e", "NO-GO"
            else:
                bg, fg, tag = "#eaeef2", "#656d76", "—"
            return (
                f'<span style="display:inline-flex;align-items:center;gap:6px;background:{bg};'
                f'color:{fg};padding:4px 11px;border-radius:12px;font-size:11px;font-weight:600;'
                f'font-family:Inter,sans-serif;flex-shrink:0;white-space:nowrap;">'
                f'<span style="font-weight:700;">{label}: {tag}</span>'
                f'<span style="color:{fg};opacity:.8;font-weight:500;">{detail}</span></span>'
            )

        _spx = wb_hb.get("live_spx"); _open = wb_hb.get("spx_open")
        _dir_ok = wb_hb.get("direction_ok")
        if _spx is not None and _open:
            _dir_detail = f"{_spx:,.0f} {'≥' if _dir_ok else '<'} open {_open:,.0f}"
        else:
            _dir_detail = "awaiting open"
        _vix = wb_hb.get("live_vix"); _vlo = wb_hb.get("vix_min", 12.0); _vhi = wb_hb.get("vix_max", 25.0)
        _vix_ok = (_vix is not None) and (_vlo <= _vix <= _vhi)
        _vix_detail = f"{_vix:.1f} in {_vlo:.0f}–{_vhi:.0f}" if _vix is not None else "no VIX"
        _bc = wb_hb.get("best_credit"); _mc = wb_hb.get("min_credit")
        if _bc is not None and _mc is not None:
            _cred_ok = _bc >= _mc
            _cred_detail = f"${_bc:.2f} {'≥' if _cred_ok else '<'} ${_mc:.2f}"
        else:
            _cred_ok = None
            _cred_detail = "no chain yet"
        st.markdown(
            '<div style="margin:2px 0 12px 0;font-family:Inter,sans-serif;display:flex;'
            'flex-wrap:nowrap;align-items:center;gap:8px;overflow-x:auto;">'
            '<span style="font-size:10px;color:#8c959f;font-weight:600;text-transform:uppercase;'
            'letter-spacing:.4px;flex-shrink:0;">entry gates</span>'
            + _light("Direction", None if _dir_ok is None else bool(_dir_ok), _dir_detail)
            + _light("VIX", bool(_vix_ok) if _vix is not None else None, _vix_detail)
            + _light("Credit", _cred_ok, _cred_detail)
            + '</div>',
            unsafe_allow_html=True,
        )

        # ── Live chain terminal: top-5 scanned spreads (added 2026-06-01) ──
        # Same data the bot polls every ~30s; shows the full band the GO/NO-GO
        # Credit light summarizes. White card to match the dashboard theme.
        _tbl = wb_hb.get("spread_table") or []
        _tgt = wb_hb.get("spread_target")
        _src = (wb_hb.get("chain_source") or "—").upper()
        _hdr_spx = f"{_spx:,.2f}" if _spx is not None else "—"
        _hdr_vix = f"{_vix:.2f}" if _vix is not None else "—"
        _hdr_tgt = f"{_tgt:,.0f}" if _tgt else "—"
        _hdr_min = f"${_mc:.2f}" if _mc is not None else "—"
        _live_dot = ('<span style="display:inline-flex;align-items:center;gap:6px;">'
                     '<span style="width:7px;height:7px;border-radius:50%;background:#2ea043;"></span>'
                     f'<span style="color:#1a7f37;font-size:10px;font-weight:600;">LIVE · {hb_rel}</span></span>') \
                    if _wb_age < 120 else \
                    f'<span style="color:#9a6700;font-size:10px;font-weight:600;">⏸ {hb_rel}</span>'
        _rows_html = ""
        for i, r in enumerate(_tbl[:5], 1):
            _mid = r.get("mid"); _is_best = (i == 1)
            _mid_style = ("color:#bc4c00;font-weight:700;" if _is_best else "color:#1f2328;")
            _otm = f'{r.get("otm")}%' if r.get("otm") is not None else "—"
            _rows_html += (
                f'<tr style="text-align:right;border-top:1px solid #eaeef2;color:#1f2328;">'
                f'<td style="text-align:left;padding:4px 0;color:#8c959f;">{i}</td>'
                f'<td style="text-align:left;">{r.get("short")} / {r.get("long")} P</td>'
                f'<td>{_otm}</td>'
                f'<td style="{_mid_style}">{_mid:.2f}</td>'
                f'<td style="color:#8c959f;">{r.get("bid"):.2f}</td>'
                f'<td style="color:#8c959f;">{r.get("ask"):.2f}</td></tr>'
            )
        if not _rows_html:
            _rows_html = ('<tr><td colspan="6" style="padding:10px 0;color:#8c959f;font-size:11px;'
                          'font-family:Inter,sans-serif;text-align:center;">no chain scan yet '
                          '(market closed or pre-window)</td></tr>')
        if _bc is not None and _mc is not None:
            _foot = (f'<span style="color:#1a7f37;">✓ best {_bc:.2f} ≥ min {_mc:.2f} — would place</span>'
                     if _bc >= _mc else
                     f'<span style="color:#cf222e;">⚠ best {_bc:.2f} &lt; min {_mc:.2f} — no qualifying spread</span>')
        else:
            _foot = '<span style="color:#8c959f;">awaiting chain</span>'
        st.markdown(
            f'<div style="background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;'
            f'margin-bottom:12px;box-shadow:0 1px 2px rgba(31,35,40,.06);">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'
            f'font-family:Inter,sans-serif;"><div style="color:#1f2328;font-size:12px;font-weight:700;">'
            f'LIVE CHAIN · TOP SPREADS</div>{_live_dot}</div>'
            f'<div style="color:#8c959f;font-size:11px;margin-bottom:6px;font-family:Inter,sans-serif;">'
            f'SPX <b style="color:#1f2328;">{_hdr_spx}</b> · VIX <b style="color:#1f2328;">{_hdr_vix}</b> · '
            f'target <b style="color:#1f2328;">{_hdr_tgt}</b> · min <b style="color:#1f2328;">{_hdr_min}</b></div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;'
            f'font-family:ui-monospace,Menlo,monospace;">'
            f'<tr style="color:#8c959f;font-size:10px;text-transform:uppercase;letter-spacing:.5px;text-align:right;">'
            f'<th style="text-align:left;">#</th><th style="text-align:left;">Strikes</th>'
            f'<th>OTM%</th><th>Mid</th><th>Bid</th><th>Ask</th></tr>{_rows_html}</table>'
            f'<div style="border-top:1px solid #eaeef2;margin-top:8px;padding-top:8px;font-size:11px;'
            f'font-family:Inter,sans-serif;">{_foot} · <span style="color:#8c959f;">data: {_src}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Bot-down alert
        if _wb_age > 900:
            _ac, _ab = "#cf222e", "rgba(207,34,46,0.07)"
            _am = f"Webull bot appears DOWN — last heartbeat {int(_wb_age//60)}m ago."
            if has_pos:
                _am += " <strong>Open position unmonitored — stop-loss not active.</strong>"
            st.markdown(
                f'<div style="border:1px solid {_ac};background:{_ab};border-radius:6px;'
                f'padding:8px 14px;margin-bottom:8px;font-size:12px;color:{_ac};font-family:Inter,sans-serif;">'
                f'🔴&nbsp; {_am}</div>', unsafe_allow_html=True)
        elif _wb_age > 120:
            st.markdown(
                '<div style="border:1px solid #9a6700;background:rgba(154,103,0,0.07);border-radius:6px;'
                'padding:8px 14px;margin-bottom:8px;font-size:12px;color:#9a6700;font-family:Inter,sans-serif;">'
                f'⚠️&nbsp; Webull bot heartbeat stale — last write {int(_wb_age)}s ago.</div>',
                unsafe_allow_html=True)

        # SPX/VIX prices — prefer LIVE IBKR values from heartbeat (written by
        # bot's market_data layer). Fall back to yfinance only when heartbeat
        # doesn't have them (e.g., paper bot in dry-run, or pre-first-scan).
        _live_spx = wb_hb.get("live_spx")
        _live_vix = wb_hb.get("live_vix")
        # Always pull yfinance quote for prev-close delta (heartbeat doesn't carry it).
        _spx_yf_price, _spx_abs, _spx_pct = _yf_quote_full("^GSPC")
        _vix_yf_price, _vix_abs, _vix_pct = _yf_quote_full("^VIX")
        spx_price = float(_live_spx) if _live_spx else _spx_yf_price
        vix_price = float(_live_vix) if _live_vix else _vix_yf_price
        spx_delta_html = _render_delta(_spx_abs, _spx_pct, "spx")
        vix_delta_html = _render_delta(_vix_abs, _vix_pct, "vix")
        vix_val = f"{vix_price:.2f}" if vix_price else "—"
        vix_color = "#cf222e" if vix_price > 25 else "#9a6700" if vix_price < 12 else "#1f2328"
        now_et_dt = datetime.now(ET)
        _is_mkt   = (now_et_dt.weekday() < 5 and dtime(9, 30) <= now_et_dt.time() <= dtime(16, 0))
        price_val = f"{spx_price:,.2f}" if spx_price else "—"
        price_badge = (
            '<span class="b b-grn" style="font-size:9px;padding:2px 6px;margin-left:6px">REAL-TIME</span>'
            if _is_mkt else
            '<span class="b b-amb" style="font-size:9px;padding:2px 6px;margin-left:6px">AFTER HRS</span>'
        )

        # Mark + unrealized PnL for open position
        mark     = None
        unreal   = 0.0
        mark_str = "—"
        if has_pos:
            mark = _webull_mark(open_pos)
            if mark is not None:
                cred = float(open_pos.get("entry_credit", 0) or 0)
                qty  = int(open_pos.get("quantity", 1) or 1)
                unreal   = (cred - mark) * 100 * qty
                mark_str = f"{mark:.2f}"

        net_pnl       = wb_stats["today"] + unreal
        net_pnl_color = "#1a7f37" if net_pnl > 0 else "#cf222e" if net_pnl < 0 else "#1f2328"

        trade_status_val = "Traded ✓" if wb_state.get("trade_taken_today") else "No trade yet"
        trade_status_col = "#1a7f37" if wb_state.get("trade_taken_today") else "#8c959f"
        net_pnl_disp = _cash(net_pnl, sign=True) if (has_pos or wb_stats["today"] != 0) else "$0.00"

        st.markdown(f"""
<div class="strip" style="grid-template-columns:repeat(6,1fr)">
  <div class="sc">
    <div class="sl">SPX Price {price_badge}</div>
    <div class="sv" style="font-size:18px;font-weight:700">{price_val}</div>
    {spx_delta_html}
  </div>
  <div class="sc">
    <div class="sl">VIX</div>
    <div class="sv" style="font-size:18px;font-weight:700;color:{vix_color}">{vix_val}</div>
    {vix_delta_html}
  </div>
  <div class="sc">
    <div class="sl">Bot Heartbeat</div>
    <div class="sv" style="font-size:14px;font-weight:600;color:{hb_color}">{hb_rel}</div>
    <div class="ss">{hb_disp}</div>
  </div>
  <div class="sc">
    <div class="sl">Net PnL Today</div>
    <div class="sv" style="color:{net_pnl_color};font-weight:700">{net_pnl_disp}</div>
    <div class="ss">Realized + unrealized</div>
  </div>
  <div class="sc">
    <div class="sl">Open Position</div>
    <div class="sv" style="font-size:16px;font-weight:700">{"1" if has_pos else "0"}</div>
    <div class="ss">{"Active trade" if has_pos else "No position"}</div>
  </div>
  <div class="sc">
    <div class="sl">W / L · {wb_state.get("trading_date","—")}</div>
    <div class="sv">
      <span style="color:#1a7f37;font-weight:700">{wins_n}W</span>
      <span style="color:#8c959f"> / </span>
      <span style="color:#cf222e;font-weight:700">{losses_n}L</span>
    </div>
    <div class="ss" style="color:{trade_status_col}">{trade_status_val}</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── KPI row ──────────────────────────────────────────────────────────
        _rule("Performance")
        total, avg, mdd = wb_stats["total"], wb_stats["avg"], wb_stats["mdd"]
        st.markdown('<div class="kpis">' + "".join([
            _kpi("Total PnL",    _cash(total, sign=True), f"Avg {_cash(avg)} per trade",                   _acc(total)),
            _kpi("Today PnL",    _cash(wb_stats["today"], sign=True), "Since midnight ET",                 _acc(wb_stats["today"])),
            _kpi("Win Rate",     _pct(wb_stats["wr"]),    f"{wins_n}W  {losses_n}L  of {int(wb_stats['n'])} trades", "t"),
            _kpi("Max Drawdown", _cash(mdd),               "Peak-to-trough",                              "r" if mdd < 0 else ""),
            _kpi("Total Trades", str(int(wb_stats["n"])), f"Avg {_cash(avg, sign=True)} each",             "b"),
        ]) + '</div>', unsafe_allow_html=True)

        # ── Open position card ───────────────────────────────────────────────
        _rule("Open Position")
        if not has_pos:
            st.markdown('<div class="nd">No open position</div>', unsafe_allow_html=True)
        else:
            cred  = float(open_pos.get("entry_credit", 0) or 0)
            stop  = float(open_pos.get("stop_price", 0) or 0)
            qty   = int(open_pos.get("quantity", 1) or 1)
            expiry_raw = str(open_pos.get("expiry", "—"))
            entry_ts_raw = open_pos.get("entry_ts", "")
            entry_time = "—"
            if entry_ts_raw:
                try:
                    _edt = datetime.fromisoformat(str(entry_ts_raw).replace("Z", "+00:00")).astimezone(ET)
                    entry_time = _edt.strftime("%Y-%m-%d  %H:%M ET")
                except: entry_time = str(entry_ts_raw)

            unreal_col = "#1a7f37" if unreal >= 0 else "#cf222e"
            unreal_str = f'{"+" if unreal>=0 else ""}${unreal:,.2f}' if mark is not None else "—"
            stop_pct_away = abs(mark - stop) / stop * 100 if (mark and stop) else None
            stop_note = f"{stop_pct_away:.1f}% away" if stop_pct_away is not None else ""

            st.markdown(f"""<div class="pc">
  <div class="pc-h">
    <div class="pc-n">{open_pos.get("symbol","SPXW")} Bull Put Spread</div>
    {_b("OPEN","grn")}
  </div>
  <div class="pf">
    <div><div class="pfl">Short Put</div><div class="pfv">{int(open_pos.get("short_strike",0))} P</div></div>
    <div><div class="pfl">Long Put</div><div class="pfv">{int(open_pos.get("long_strike",0))} P</div></div>
    <div><div class="pfl">Expiry</div><div class="pfv">{expiry_raw}</div></div>
    <div><div class="pfl">Quantity</div><div class="pfv">{qty} contract{"s" if qty!=1 else ""}</div></div>
    <div><div class="pfl">Entry Credit</div><div class="pfv">{cred:.2f}</div></div>
    <div><div class="pfl">Stop</div><div class="pfv" style="color:#cf222e">{stop:.2f} <span style="font-size:10px;color:#8c959f">{stop_note}</span></div></div>
    <div><div class="pfl">Mark</div><div class="pfv">{mark_str}</div></div>
    <div><div class="pfl">Unrealized P&amp;L</div><div class="pfv" style="color:{unreal_col};font-weight:600">{unreal_str}</div></div>
    <div><div class="pfl">Entry SPX</div><div class="pfv">{open_pos.get("entry_spx","—")}</div></div>
    <div><div class="pfl">Entry VIX</div><div class="pfv">{open_pos.get("entry_vix","—")}</div></div>
    <div><div class="pfl">Entry Time</div><div class="pfv" style="font-size:11px">{entry_time}</div></div>
    <div><div class="pfl">Order ID</div><div class="pfv" style="font-size:10px;color:#8c959f">{str(open_pos.get("client_order_id","—"))[:16]}…</div></div>
  </div>
</div>""", unsafe_allow_html=True)

    _webull_live()

    # ── Activity tabs ────────────────────────────────────────────────────────
    _rule("Activity Log")
    t_trades, t_sigs, t_orders, t_log = st.tabs([
        "  Trades  ", "  Signals  ", "  Orders  ", "  System Log  "
    ])

    with t_trades:
        wt = _rt_webull(WEBULL_DATA / "trades.csv")
        if wt.empty:
            st.markdown('<div class="nd">No closed trades recorded yet</div>', unsafe_allow_html=True)
        else:
            want = ["Date","Symbol","Expiry","Short Strike","Long Strike",
                    "Entry Credit","SPX at Entry","VIX at Entry",
                    "Exit Price","PnL pts","PnL USD","Exit Reason","Notes"]
            show = [c for c in want if c in wt.columns]
            disp = wt[show].tail(250).iloc[::-1].copy()
            if "PnL USD" in disp.columns:
                disp["PnL USD"] = disp["PnL USD"].apply(
                    lambda x: (f"+${x:,.2f}" if x > 0 else f"-${abs(x):,.2f}") if pd.notna(x) else "—")
            st.dataframe(disp, use_container_width=True, hide_index=True, height=390)

    with t_sigs:
        sigs = _rjl(WEBULL_LOGS / "signal_events.jsonl", 300)
        if not sigs:
            st.markdown('<div class="nd">No signal events yet</div>', unsafe_allow_html=True)
        else:
            sdf = pd.DataFrame(sigs)
            want = ["ts","event","reason","spx","vix","short_strike","long_strike","credit_mid","expiry"]
            st.dataframe(sdf[[c for c in want if c in sdf.columns]].tail(300).iloc[::-1],
                         use_container_width=True, hide_index=True, height=320)

    with t_orders:
        ords = _rjl(WEBULL_LOGS / "order_events.jsonl", 300)
        if not ords:
            st.markdown('<div class="nd">No order events yet</div>', unsafe_allow_html=True)
        else:
            odf = pd.DataFrame(ords)
            want = ["ts","event","symbol","expiry","short_strike","long_strike",
                    "mark","stop","entry_credit","filled","fill_price","detail"]
            st.dataframe(odf[[c for c in want if c in odf.columns]].tail(300).iloc[::-1],
                         use_container_width=True, hide_index=True, height=320)

    with t_log:
        lines = _rl(WEBULL_LOGS / "daily_summary.log", 200)
        if not lines:
            st.markdown('<div class="nd">No system logs yet</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="log-w"><div class="log-h">System Log</div>', unsafe_allow_html=True)
            st.code("\n".join(lines), language="text")
            st.markdown("</div>", unsafe_allow_html=True)

    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# IBKR VIEW — bot is currently disabled
# ══════════════════════════════════════════════════════════════════════════════

st.html(f"""
<div style="{_S}max-width:680px;margin:64px auto 0 auto;padding:32px 36px;
            background:#fff;border:1px solid #d0d7de;border-radius:10px;
            box-shadow:0 1px 3px rgba(0,0,0,0.04);text-align:center;">
  <div style="font-size:42px;line-height:1;margin-bottom:14px;">🏦</div>
  <div style="font-size:18px;font-weight:700;color:#1f2328;margin-bottom:6px;">
    IBKR / XSP Bot — Disabled
  </div>
  <div style="font-size:13px;color:#57606a;line-height:1.55;margin-bottom:22px;">
    The IBKR SPX-XSP bot has been turned off so attention stays on the
    Webull SPX BPS bot (one live strategy at a time).
    Services <code style="background:#f6f8fa;padding:1px 6px;border-radius:4px;font-size:12px;">xsp-spread-bot</code> and
    <code style="background:#f6f8fa;padding:1px 6px;border-radius:4px;font-size:12px;">xsp-ibgateway</code>
    are stopped and disabled. The 8&nbsp;AM&nbsp;ET auto-restart cron has been neutralized.
  </div>
  <a href="?broker=webull" target="_self"
     style="display:inline-block;background:#1f2328;color:#fff;text-decoration:none;
            padding:9px 20px;border-radius:6px;font-size:13px;font-weight:600;">
    → Go to Webull bot
  </a>
</div>
""")
st.stop()


# (IBKR view code below is preserved but never reached.)
trades   = _rt(bot.trades_file)
stats    = _stats(trades)
pos_lst  = _pos(state_raw)
last_sig = status.get("last_signal") if isinstance(status, dict) else None

connected   = bool(status.get("connected"))
paper_mode  = bool(status.get("paper_trading", True))
actual_mode = "PAPER" if paper_mode else "LIVE"
mode_match  = actual_mode == bot.expected_mode

_sys = status.get("system", {}) if isinstance(status, dict) else {}
def _sys_row() -> str:
    if not _sys: return ""
    cpu  = _sys.get("cpu_pct")
    ru   = _sys.get("ram_used_gb");  rt = _sys.get("ram_total_gb")
    rp   = _sys.get("ram_pct");      su = _sys.get("swap_used_mb", 0)
    du   = _sys.get("disk_used_gb"); dt = _sys.get("disk_total_gb")
    ram_col  = "#cf222e" if (rp or 0) > 85 else "#d4a72c" if (rp or 0) > 70 else "#8c959f"
    swap_col = "#cf222e" if (su or 0) > 200 else "#8c959f"
    cpu_col  = "#cf222e" if (cpu or 0) > 80 else "#d4a72c" if (cpu or 0) > 50 else "#8c959f"
    sep = '<span style="color:#d0d7de;margin:0 5px;">·</span>'
    parts = []
    if cpu is not None: parts.append(f'<span style="color:{cpu_col}">CPU {cpu}%</span>')
    if ru  is not None: parts.append(f'<span style="color:{ram_col}">RAM {ru}/{rt}GB</span>')
    if su  is not None: parts.append(f'<span style="color:{swap_col}">Swap {int(su)}MB</span>')
    if du  is not None: parts.append(f'<span style="color:#8c959f">Disk {du}/{dt}GB</span>')
    return sep.join(parts)

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

    _live_ts_raw = live_status.get("ts", "") if isinstance(live_status, dict) else ""
    _live_age_s  = None
    if _live_ts_raw:
        try: _live_age_s = (datetime.now(UTC) - datetime.fromisoformat(_live_ts_raw).astimezone(UTC)).total_seconds()
        except: pass

    # ── Live status card (refreshes every 2s — never frozen) ─────────────────
    _conn   = bool(live_status.get("connected"))
    _paper  = bool(live_status.get("paper_trading", True))
    _mode   = "PAPER" if _paper else "LIVE"
    _conn_b = _b("CONNECTED", "grn") if _conn else _b("OFFLINE", "red")
    _mode_b = _b(_mode, "blu" if _paper else "grn")
    _alv_b  = (_b("● ALIVE","grn") if _live_age_s is not None and _live_age_s < 30
               else _b("● STALE","amb") if _live_age_s is not None and _live_age_s < 300
               else _b("● DOWN","red"))
    _badges = f"{_conn_b}&nbsp; {_mode_b}&nbsp; {_alv_b}"
    _sub    = f"{bot.symbol} · {bot.strategy_scope}"
    with _topbar_slot:
        st.html(f"""
<div style="{_S}display:flex;align-items:center;gap:10px;background:#ffffff;border:1px solid #d0d7de;border-radius:8px;padding:10px 16px;margin-bottom:10px;box-shadow:0 1px 2px rgba(31,35,40,.06);">
  <div style="width:32px;height:32px;border-radius:7px;flex-shrink:0;background:linear-gradient(135deg,#0969da,#2196f3);display:flex;align-items:center;justify-content:center;font-size:15px;">📈</div>
  <div>
    <div style="{_S}font-size:14px;font-weight:700;color:#1f2328;letter-spacing:-.2px;">{bot.name}&nbsp; {_badges}</div>
    <div style="{_S}font-size:11px;color:#8c959f;margin-top:1px;">{_sub}</div>
  </div>
</div>
""")
    _has_open = len(live_pos) > 0
    if _live_age_s is None or _live_age_s > 300:
        if _live_age_s is None:
            _alert_msg = "Bot not running — no status written yet."
        else:
            _alert_msg = f"Bot process appears DOWN — last heartbeat {int(_live_age_s//60)}m ago."
        if _has_open:
            _alert_msg += " <strong>Open position unmonitored — stop-loss not active.</strong>"
        st.markdown(
            f'<div style="border:1px solid #cf222e;background:rgba(207,34,46,0.07);border-radius:6px;'
            f'padding:8px 14px;margin-bottom:8px;font-size:12px;color:#cf222e;font-family:Inter,sans-serif;">'
            f'🔴&nbsp; {_alert_msg}</div>', unsafe_allow_html=True)
    elif _live_age_s > 60:
        _alert_color, _alert_bg = "#9a6700", "rgba(154,103,0,0.07)"
        st.markdown(
            f'<div style="border:1px solid {_alert_color};background:{_alert_bg};border-radius:6px;'
            f'padding:8px 14px;margin-bottom:8px;font-size:12px;color:{_alert_color};font-family:Inter,sans-serif;">'
            f'⚠️&nbsp; Bot heartbeat stale — last write {int(_live_age_s)}s ago. May be reconnecting.</div>',
            unsafe_allow_html=True)

    net_liq     = live_status.get("net_liquidation")
    nliq_val    = _cash(net_liq) if net_liq is not None else "—"
    nliq_color  = "#1a7f37" if (net_liq or 0) > 0 else "#1f2328"
    mode_label  = "Paper" if bool(live_status.get("paper_trading", True)) else "Live"

    now_et_dt   = datetime.now(ET)
    _is_market  = (now_et_dt.weekday() < 5 and dtime(9, 30) <= now_et_dt.time() <= dtime(16, 0))
    raw_price   = live_status.get("underlying_price", 0) or 0
    price_source = "ib"
    if not raw_price:
        spx_yf    = _yf_spx_price()
        raw_price = spx_yf / 10 if bot.symbol == "XSP" else spx_yf
        price_source = "yf"
    if raw_price > 0:
        price_val = f"{raw_price:,.2f}"
        if _is_market and price_source == "ib":
            price_badge = '<span class="b b-grn" style="font-size:9px;padding:2px 6px;margin-left:6px"><span class="dot dg"></span>&nbsp;LIVE</span>'
            price_sub = "Real-time · IB"
        elif _is_market:
            price_badge = '<span class="b b-grn" style="font-size:9px;padding:2px 6px;margin-left:6px">REAL-TIME</span>'
            price_sub = "Real-time · Yahoo"
        else:
            price_badge = '<span class="b b-amb" style="font-size:9px;padding:2px 6px;margin-left:6px">AFTER HRS</span>'
            price_sub = "Last close"
    else:
        price_val = "—"; price_badge = ""; price_sub = ""

    spread_marks = live_status.get("spread_marks", {}) if isinstance(live_status, dict) else {}
    unrealized_pnl: float = 0.0
    for pos in live_pos:
        strat  = pos.get("strategy", "")
        credit = float(pos.get("entry_credit") or 0)
        contr  = int(pos.get("contracts") or 1)
        _sp    = pos.get("short_put_strike") or pos.get("short_call_strike") or ""
        _mkey  = f"{strat}_{_sp}" if _sp else strat
        mark   = float(spread_marks.get(_mkey, 0) or 0)
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

    _rule("Performance")
    total, avg, mdd = live_stats["total"], live_stats["avg"], live_stats["mdd"]
    st.markdown('<div class="kpis">' + "".join([
        _kpi("Total PnL",    _cash(total,sign=True),   f"Avg {_cash(avg)} per trade",                  _acc(total)),
        _kpi("Today PnL",    _cash(live_stats['today'],sign=True), "Since midnight ET",                 _acc(live_stats['today'])),
        _kpi("Win Rate",     _pct(live_stats['wr']),    f"{wins_n}W  {losses_n}L  of {int(live_stats['n'])} trades", "t"),
        _kpi("Max Drawdown", _cash(mdd),                "Peak-to-trough",                              "r" if mdd<0 else ""),
        _kpi("Total Trades", str(int(live_stats['n'])), f"Avg {_cash(avg,sign=True)} each",             "b"),
    ]) + '</div>', unsafe_allow_html=True)

    _rule("Open Positions")
    if not live_pos:
        st.markdown('<div class="nd">No open positions</div>', unsafe_allow_html=True)
    else:
        def _pos_ts(p):
            try: return datetime.fromisoformat(str(p.get("entry_ts","")).replace("Z","+00:00"))
            except: return datetime.min.replace(tzinfo=UTC)

        _POS_CAP = 30
        _all_pos = sorted(live_pos, key=_pos_ts, reverse=True)
        _sorted_pos = _all_pos[:_POS_CAP]
        _total_unreal = 0.0; _has_marks = False; _cards_html = ""
        for p in _sorted_pos:
            entry_ts_raw = p.get("entry_ts", "")
            if entry_ts_raw:
                try:
                    _et_dt = datetime.fromisoformat(str(entry_ts_raw).replace("Z","+00:00")).astimezone(ET)
                    entry_time_card = _et_dt.strftime("%Y-%m-%d  %H:%M:%S")
                except: entry_time_card = str(entry_ts_raw)
            else: entry_time_card = "—"

            strat_key  = p.get("strategy","")
            _sp_strike = p.get("short_put_strike") or p.get("short_call_strike") or ""
            _mark_key  = f"{strat_key}_{_sp_strike}" if _sp_strike else strat_key
            _cred      = float(p.get("entry_credit") or 0)
            _contr     = int(p.get("contracts") or 1)
            _mark      = float(spread_marks.get(_mark_key, 0) or 0)
            if _mark > 0 and _cred > 0:
                _pnl_val = (_cred - _mark) * 100 * _contr
                _pnl_col = "#1a7f37" if _pnl_val >= 0 else "#cf222e"
                _pnl_str = f'{"+" if _pnl_val>=0 else ""}${_pnl_val:,.2f}'
                _mark_str = f"{_mark:.2f}"; _total_unreal += _pnl_val; _has_marks = True
            else:
                _pnl_col, _pnl_str, _mark_str = "#8c959f", "—", "—"

            _exp_raw = str(p.get('expiry',''))
            _exp_fmt = datetime.strptime(_exp_raw,'%Y%m%d').strftime('%d %b %Y') if _exp_raw.isdigit() and len(_exp_raw)==8 else (_exp_raw or '—')
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

        _n_total = len(_all_pos); _n_shown = len(_sorted_pos)
        _unreal_col = "#1a7f37" if _total_unreal >= 0 else "#cf222e"
        _unreal_disp = (f'<span style="color:{_unreal_col};font-weight:600;">{"+" if _total_unreal>=0 else ""}${_total_unreal:,.2f}</span>' if _has_marks else '<span style="color:#8c959f;">—</span>')
        _cap_note = (f' &nbsp;<span style="color:#8c959f;font-weight:400;">· showing {_n_shown} of {_n_total} most recent</span>' if _n_total > _POS_CAP else "")
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:16px;margin-bottom:8px;">'
            f'<span style="font-family:Inter,sans-serif;font-size:11px;font-weight:700;color:#57606a;">'
            f'{_n_total} position{"s" if _n_total!=1 else ""} open{_cap_note}</span>'
            f'<span style="font-family:Inter,sans-serif;font-size:11px;color:#57606a;">Unrealized P&amp;L (shown): {_unreal_disp}</span>'
            f'</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="max-height:480px;overflow-y:auto;padding-right:4px;">{_cards_html}</div>', unsafe_allow_html=True)

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

if not is_webull:
    _live_section()

_rule("Activity Log")
t_trades, t_sigs, t_orders, t_log = st.tabs([
    "  Trades  ", "  Signals  ", "  Orders  ", "  System Log  "
])

with t_trades:
    _open_pos = pos_lst
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
        hdr = "".join(f'<th style="{_TH}">{c}</th>' for c in cols)

        def _pos_strike_str(legs_raw, direction, right):
            return [l["strike"] for l in legs_raw
                    if l.get("direction","").upper()==direction and l.get("right","").upper()==right]

        def _tab_pos_ts(p):
            try: return datetime.fromisoformat(str(p.get("entry_ts","")).replace("Z","+00:00"))
            except: return datetime.min.replace(tzinfo=UTC)

        _TAB_CAP = 30
        _open_pos_all   = sorted(_open_pos, key=_tab_pos_ts, reverse=True)
        _open_pos_shown = _open_pos_all[:_TAB_CAP]
        rows_html = ""
        for p in _open_pos_shown:
            strat    = p.get("strategy","—"); expiry = p.get("expiry","—")
            legs_raw = p.get("legs",[]) if isinstance(p.get("legs"),list) else []
            sp_put   = (_pos_strike_str(legs_raw,"SHORT","P") or [p.get("short_put_strike")])[0] or None
            lp_put   = (_pos_strike_str(legs_raw,"LONG", "P") or [p.get("long_put_strike")])[0]  or None
            sp_call  = (_pos_strike_str(legs_raw,"SHORT","C") or [p.get("short_call_strike")])[0] or None
            lp_call  = (_pos_strike_str(legs_raw,"LONG", "C") or [p.get("long_call_strike")])[0]  or None
            def _sk(put, call):
                parts = ([f"{put:.0f}P"] if put else []) + ([f"{call:.0f}C"] if call else [])
                return " / ".join(parts) or "—"
            contracts = p.get("contracts","—"); entry_cred = p.get("entry_credit")
            stop_p = p.get("stop_price"); target_p = p.get("profit_target_price")
            entry_ts_raw = p.get("entry_ts","")
            if entry_ts_raw:
                try:
                    _edt = datetime.fromisoformat(str(entry_ts_raw).replace("Z","+00:00")).astimezone(ET)
                    entry_time = _edt.strftime("%Y-%m-%d  %H:%M ET")
                except: entry_time = str(entry_ts_raw)
            else: entry_time = "—"
            _sp = p.get("short_put_strike") or p.get("short_call_strike") or ""
            _mk = f"{strat}_{_sp}" if _sp else strat
            mark = float(_sm.get(_mk, 0) or 0); mark_s = f"{mark:.2f}" if mark > 0 else "—"
            if entry_cred and mark > 0 and contracts:
                pv  = (float(entry_cred) - mark) * 100 * int(contracts)
                pc  = "#1a7f37" if pv >= 0 else "#cf222e"
                pnl = f'<span style="color:{pc};font-weight:600;">{"+" if pv>=0 else ""}${pv:,.2f}</span>'
            else: pnl = "—"
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
        _label_style = ("font-family:Inter,sans-serif;font-size:9px;font-weight:700;"
                        "letter-spacing:.8px;text-transform:uppercase;color:#8c959f;margin-bottom:6px;margin-top:2px;")
        _tab_total = len(_open_pos_all)
        _tab_cap_note = f" — showing {_TAB_CAP} of {_tab_total} most recent" if _tab_total > _TAB_CAP else ""
        st.markdown(
            f'<div style="{_label_style}">Open Positions ({_tab_total}){_tab_cap_note}</div>'
            f'<div style="overflow-x:auto;overflow-y:auto;max-height:320px;margin-bottom:16px;border:1px solid #d0d7de;border-radius:8px;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead style="position:sticky;top:0;z-index:1;"><tr>{hdr}</tr></thead><tbody>{rows_html}</tbody>'
            f'</table></div>', unsafe_allow_html=True)

    _label_style = ("font-family:Inter,sans-serif;font-size:9px;font-weight:700;"
                    "letter-spacing:.8px;text-transform:uppercase;color:#8c959f;margin-bottom:6px;")
    st.markdown(f'<div style="{_label_style}">Closed Trades</div>', unsafe_allow_html=True)
    if trades.empty:
        st.markdown('<div style="font-family:Inter,sans-serif;font-size:12px;color:#8c959f;padding:12px 4px;">No closed trades recorded yet</div>', unsafe_allow_html=True)
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
