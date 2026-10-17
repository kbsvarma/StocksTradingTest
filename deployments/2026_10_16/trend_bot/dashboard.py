"""trend_bot dashboard — the ONE screen (spec: trend_sleeve_go_parked.md).

Visual style mirrors the BPS SPX bot dashboard (webull_bot/dashboard.py):
light theme, grid strip, KPI tiles, badges, position cards.
Read-only — mode toggles happen via `python bot.py --mode ...` (CLI-logged).

Run: streamlit run dashboard.py --server.port 8503
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

ET = ZoneInfo("America/New_York")
BASE = Path(__file__).resolve().parent
_CFG = yaml.safe_load((BASE / "config.yaml").read_text())
SDIR = BASE / _CFG["ops"]["state_dir"]
LOOKBACK = _CFG["rotation"]["lookback_days"]

# backtest pace: plateau-average expectation for the deployed gear (NOT the
# best cell's own number) + gold sleeve historical average
BACKTEST_PACE_PER_DAY = {"core": 5400 / 365.0, "sleeve": 1270 / 365.0}
SYMS = ("NQ", "ES", "YM", "GC")
SAT_SYMS = tuple(_CFG.get("satellite_instruments") or {})
SYM_NAMES = {"NQ": "Nasdaq · MNQ", "ES": "S&P 500 · MES",
             "YM": "Dow · MYM", "GC": "Gold · MGC (sleeve)",
             "NQ_S": "Nasdaq · MNQ (satellite)",
             "ES_S": "S&P 500 · MES (satellite)",
             "YM_S": "Dow · MYM (satellite)"}
MULTS = {sym: float(spec["mult"])
         for grp in ("core_instruments", "sleeve_instruments",
                     "satellite_instruments")
         for sym, spec in (_CFG.get(grp) or {}).items()}

st.set_page_config(page_title="Trend Bot", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

# ── Light-theme CSS (BPS dashboard family) ───────────────────────────────────
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

.topnav { display:flex; align-items:center; justify-content:flex-end; gap:8px; margin-bottom:8px; }
.bot-card {
  display:flex; align-items:center; gap:12px;
  background:#fff; border:1px solid var(--bd); border-radius:8px;
  padding:12px 18px; margin-bottom:10px; box-shadow:var(--shadow-sm);
}
.tb-icon {
  width:32px; height:32px; border-radius:7px; flex-shrink:0;
  background:linear-gradient(135deg,#0969da 0%,#2da44e 100%);
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
  background:var(--p1); color:var(--t1) !important; text-decoration:none !important;
  border-radius:6px; font-size:12px; font-weight:600;
  padding:6px 14px; border:1px solid var(--bd2); display:inline-block;
}
.ref-btn:hover { background:var(--p3); }

.strip {
  display:flex; flex-wrap:nowrap; align-items:stretch;
  gap:1px; background:var(--bd);
  border:1px solid var(--bd); border-radius:8px;
  overflow:hidden; margin-bottom:12px; box-shadow:var(--shadow-sm);
}
.sc { background:var(--p1); padding:10px 14px; flex:1 1 auto; min-width:0; }
.strip-top { margin-bottom:6px; }
.sl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:4px; }
.sv { font-family:var(--mono); font-size:13px; font-weight:500; color:var(--t1); }
.sv-big { font-family:var(--mono); font-size:18px; font-weight:700; color:var(--t1); line-height:1.1; }
.ss { font-size:10px; color:var(--t3); margin-top:3px; }
.sv.vg { color:var(--grn); } .sv.vr { color:var(--red); } .sv.va { color:var(--amb); } .sv.vb { color:var(--blu); }
.sv-big.vg { color:var(--grn); } .sv-big.vr { color:var(--red); }

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
.kl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:8px; }
.kv { font-family:var(--mono); font-size:20px; font-weight:500; color:var(--t1); line-height:1; }
.kv.vg { color:var(--grn); } .kv.vr { color:var(--red); } .kv.vb { color:var(--blu); } .kv.va { color:var(--amb); }
.ks { font-size:10px; color:var(--t3); margin-top:5px; }

.pc {
  background:var(--p1); border:1px solid var(--bd);
  border-left:3px solid var(--bd2); border-radius:8px;
  padding:12px 16px; margin-bottom:8px; box-shadow:var(--shadow-sm);
}
.pc.pos { border-left-color:var(--grn); }
.pc-h { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.pc-n { font-size:13px; font-weight:700; color:var(--t1); }
.pf { display:grid; grid-template-columns:repeat(6,1fr); gap:10px; }
.pfl { font-size:9px; font-weight:700; letter-spacing:.6px; text-transform:uppercase; color:var(--t3); margin-bottom:3px; }
.pfv { font-family:var(--mono); font-size:12px; color:var(--t1); }
.pfv.vg { color:var(--grn); } .pfv.vr { color:var(--red); }

.empty {
  background:var(--p1); border:1px dashed var(--bd); border-radius:8px;
  padding:18px; text-align:center; color:var(--t3); font-size:12px;
}
.story {
  background:var(--p1); border:1px solid var(--bd); border-left:3px solid var(--blu);
  border-radius:8px; padding:11px 15px; margin-bottom:10px;
  font-size:12.5px; color:var(--t1); line-height:1.6; box-shadow:var(--shadow-sm);
}
.story b { font-weight:600; }
.story .when { color:var(--t3); font-size:11px; display:block; margin-top:6px; }
.pfs {
  border-top:1px dashed var(--bd); margin-top:10px; padding-top:9px;
  font-size:11.5px; color:var(--t2); line-height:1.55;
}
.evt {
  font-family:var(--mono); font-size:11px; color:var(--t2);
  background:var(--p1); border:1px solid var(--bd); border-radius:8px;
  padding:10px 14px; box-shadow:var(--shadow-sm); line-height:1.7;
  white-space:pre-wrap; word-break:break-all;
}
.stDataFrame { border:1px solid var(--bd) !important; border-radius:6px !important; }
</style>
""", unsafe_allow_html=True)


# ── helpers ──────────────────────────────────────────────────────────────────
def _badge(text: str, color: str = "blu", dot: bool = False) -> str:
    dot_html = f'<span class="dot d{color[0]}"></span>' if dot else ''
    return f'<span class="b b-{color}">{dot_html}{text}</span>'


def _strip_cell(label: str, value: str, sub: str = "", color: str = "",
                big: bool = False, glow: str = "") -> str:
    cls = ("sv-big" if big else "sv") + (f" {color}" if color else "")
    sub_html = f'<div class="ss">{sub}</div>' if sub else ""
    style = (f' style="box-shadow:inset 0 0 0 2px var(--{glow})"' if glow else "")
    return (f'<div class="sc"{style}><div class="sl">{label}</div>'
            f'<div class="{cls}">{value}</div>{sub_html}</div>')


def _kpi(label: str, value: str, sub: str = "", color: str = "") -> str:
    kb = f" k{color[1]}" if color else ""
    kv = f" {color}" if color else ""
    sub_html = f'<div class="ks">{sub}</div>' if sub else ""
    return (f'<div class="kpi{kb}"><div class="kl">{label}</div>'
            f'<div class="kv{kv}">{value}</div>{sub_html}</div>')


def _section(title: str):
    st.markdown(f'<div class="sr"><span>{title}</span><hr></div>',
                unsafe_allow_html=True)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _read_csv(name: str) -> pd.DataFrame:
    p = SDIR / name
    try:
        return pd.read_csv(p) if p.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=55)
def _closes(sym: str) -> pd.Series:
    try:
        b = pd.read_parquet(SDIR / f"bars_{sym}.parquet", columns=["close"])
        return b["close"]
    except Exception:
        return pd.Series(dtype=float)


def _last_close(sym: str) -> float | None:
    c = _closes(sym)
    return float(c.iloc[-1]) if len(c) else None


def _recent_form(sym: str, n: int = 8) -> tuple[int, int]:
    """(up_days, total) over the last n UTC-day closes."""
    c = _closes(sym)
    if not len(c):
        return 0, 0
    d = c.resample("1D").last().dropna().tail(n + 1).diff().dropna()
    return int((d > 0).sum()), len(d)


IDX_NAME = {"NQ": "the Nasdaq", "ES": "the S&P 500", "YM": "the Dow",
            "GC": "gold", "NQ_S": "the Nasdaq (satellite)",
            "ES_S": "the S&P 500 (satellite)", "YM_S": "the Dow (satellite)"}


def _form_words(up: int, total: int) -> str:
    if total == 0:
        return "no recent data"
    if up >= total - 1:
        return f"{up} of the last {total} sessions closed higher — clearly trending up"
    if up >= total * 0.6:
        return f"{up} of the last {total} sessions closed higher — leaning higher"
    if up >= total * 0.4:
        return f"{up} of the last {total} sessions closed higher — sideways, no clear direction"
    return f"only {up} of the last {total} sessions closed higher — trending down"


def _et_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(ET).strftime("%a %b %d, %-I:%M %p ET")
    except Exception:
        return iso[:16]


def _rotation_story(rot: dict, mode: str) -> str:
    if not rot:
        return "No rebalance has run yet — the first Monday check will rank the three indices."
    rets = rot.get("returns") or {}
    rank = rot.get("ranking") or []
    if len(rank) < 3:
        return "Ranking incomplete — waiting on data."
    leader_sym, s2, s3 = rank[0], rank[1], rank[2]
    lb = rot.get("lookback", LOOKBACK)
    lead_txt = (f"Over the last {lb} sessions <b>{IDX_NAME[leader_sym]} is up "
                f"{rets[leader_sym]*100:+.1f}%</b>, ahead of {IDX_NAME[s2]} "
                f"({rets[s2]*100:+.1f}%) and {IDX_NAME[s3]} ({rets[s3]*100:+.1f}%). ")
    spread = rot.get("spread_pp")
    gear = {"aggressive": "AGGRESSIVE", "default": "DEFAULT",
            "defensive": "DEFENSIVE"}.get(mode, mode)
    if rot.get("unsure"):
        why = (f"The leaders are bunched within {spread}pp — below the 1.0pp "
               f"conviction bar, so despite your {gear} gear the organism "
               f"declared itself <b>unsure and spread out 1-1-1</b> defensively.")
    elif rot.get("applied") == "3-0-0":
        why = (f"The lead is decisive ({spread}pp vs the 1.0pp 'unsure' bar), so "
               f"in your {gear} gear the organism <b>concentrated all 3 contracts "
               f"on {IDX_NAME[leader_sym]}</b>.")
    elif rot.get("applied") == "2-1-0":
        why = (f"The lead is clear ({spread}pp vs the 1.0pp bar), so in your "
               f"{gear} gear it <b>tilted 2-1-0 toward "
               f"{IDX_NAME[leader_sym]}</b>, keeping "
               f"a foot in {IDX_NAME[s2]}.")
    else:
        why = (f"Your {gear} gear holds the book at <b>1-1-1 equal weight</b> "
               f"regardless of the ranking.")
    week = pd.Timestamp(rot.get("week"))
    nxt = (week + pd.Timedelta(days=7)).strftime("%a %b %d")
    when = (f"decided {_et_time(rot.get('computed_at'))} · next re-think "
            f"<b>{nxt}</b> (first wake after Sun 8:00 PM ET)")
    return lead_txt + why + f'<span class="when">{when}</span>'


def _instr_story(sym: str, p: dict, d, lc: float | None, weight) -> str:
    name = IDX_NAME[sym] if sym != "GC" else "gold"
    up, tot = _recent_form(sym)
    form = _form_words(up, tot)
    if p.get("position"):
        mult, qty = MULTS[sym], p["qty"]
        locked = (p["stop_px"] - p["entry_px"]) * mult * qty
        stop_txt = (f"the stop trigger has ratcheted up to "
                    f"<b>{p['stop_px']:,.2f}</b>, implying about "
                    f"<b>{_fmt_usd(locked)}</b> if execution occurs near "
                    "that trigger"
                    if locked > 0 else
                    f"the stop trigger sits at <b>{p['stop_px']:,.2f}</b> — "
                    f"about <b>{_fmt_usd(locked)}</b> if execution occurs "
                    "near that price")
        form_s = form[0].upper() + form[1:]
        return (f"Bought {qty}× at <b>{p['entry_px']:,.2f}</b>; price has reached "
                f"{p['hw']:,.2f} at best, and {stop_txt}. Stop-market fills "
                f"can be worse in gaps or illiquid conditions. {form_s}.")
    if d is None:
        return form[0].upper() + form[1:] + "."
    close, donch = d.get("close"), d.get("donchian")
    parts = []
    if pd.notna(donch) and pd.notna(close) and close and donch:
        gap_pct = (donch - close) / close * 100
        if gap_pct > 0:
            parts.append(f"{name} needs a close above <b>{donch:,.2f}</b> to "
                         f"trigger a buy (currently {close:,.2f}, about "
                         f"{gap_pct:.1f}% away)")
        else:
            parts.append(f"{name} closed above its breakout level "
                         f"({close:,.2f} vs {donch:,.2f}) — entry pending gates "
                         f"and the next bar")
    if d.get("vol_ok") is True and pd.notna(d.get("atr_pct")) and pd.notna(d.get("vol_thresh")):
        parts.append(f"volatility is calm ({d['atr_pct']*100:.2f}% vs the "
                     f"{d['vol_thresh']*100:.2f}% panic threshold), so entries "
                     f"are allowed")
    elif d.get("vol_ok") is False:
        parts.append("volatility is <b>elevated</b> — in the top 5% of the last "
                     "~1.5 years, so new entries are blocked until it cools")
    if d.get("session_ok") is False:
        parts.append("the overnight dead-zone gate (2–6 AM ET) is blocking entries")
    if sym != "GC" and weight == 0:
        parts.append("this week's rotation gives it <b>no allocation</b> — a "
                     "breakout here would be noted but not traded")
    parts.append(form)
    return (". ".join(s[0].upper() + s[1:] for s in parts) + ".")


def _fmt_usd(v: float) -> str:
    return f"${v:+,.2f}"


def _pnl_color(v: float) -> str:
    return "vg" if v > 0 else ("vr" if v < 0 else "")


state = _read_json(SDIR / "state.json")
trades = _read_csv("trades.csv")
equity = _read_csv("equity.csv")
decisions = _read_csv("decisions.csv")

# ── heartbeat / status ───────────────────────────────────────────────────────
hb_p = SDIR / "heartbeat"
hb_age_min = float("inf")
if hb_p.exists():
    try:
        hb = datetime.fromisoformat(hb_p.read_text().strip())
        hb_age_min = (datetime.now(UTC) - hb).total_seconds() / 60
    except Exception:
        pass
bot_alive = hb_age_min < 12
last_bars = state.get("last_bar") or {}
newest_bar = max(last_bars.values()) if last_bars else None
bar_txt = newest_bar[11:16] + " UTC" if newest_bar else "—"
halted = bool(state.get("halt_entries"))

# ── topbar ───────────────────────────────────────────────────────────────────
now_et = datetime.now(ET).strftime("%H:%M:%S  ET")
status_badge = _badge("ACTIVE", "grn", dot=True) if bot_alive else _badge("DOWN", "red", dot=True)
mode = state.get("mode", "?")
mode_badge = _badge(mode.upper(), {"defensive": "blu", "default": "grn",
                                   "aggressive": "amb"}.get(mode, "dim"))
paper_start = (state.get("paper_start") or "")[:10]
soak_start = (state.get("soak_start") or "")[:10]
soak_status = (f"qualifying soak since {soak_start}"
               if soak_start and state.get("soak_release_valid") is True
               else "qualification soak NOT ACTIVE")

_lw = state.get("last_wake")
_lws = state.get("last_wake_status", "ok")
if _lw:
    _lw_dt = datetime.fromisoformat(_lw).astimezone(ET)
    _nx_dt = (_lw_dt.replace(minute=0, second=25) + pd.Timedelta(hours=1))
    if _lws and _lws != "ok":
        check_chip = (f'<span class="et-clock" style="background:var(--red-bg);'
                      f'color:var(--red);border:1.5px solid var(--red);font-weight:700">'
                      f'⚠ check {_lw_dt.strftime("%-I:%M %p")} {_lws}'
                      f' · next {_nx_dt.strftime("%-I:%M %p")} ET</span>')
    else:
        check_chip = (f'<span class="et-clock" style="border:1.5px solid var(--grn);'
                      f'color:var(--grn);background:var(--grn-bg)">checked ✓ '
                      f'{_lw_dt.strftime("%-I:%M:%S %p")}'
                      f' · next {_nx_dt.strftime("%-I:%M %p")} ET</span>')
else:
    check_chip = '<span class="et-clock">no check yet</span>'

st.markdown(f"""
<div class="topnav">
  {check_chip}
  <span class="et-clock">bar {bar_txt} · {'hb ' + format(hb_age_min, '.0f') + 'm' if hb_age_min < 1e9 else 'no heartbeat'}</span>
  <span class="et-clock">{now_et}</span>
  <a href="?_={now_et}" target="_self" class="ref-btn">↻ Refresh</a>
</div>
<div class="bot-card">
  <div class="tb-icon">📈</div>
  <div>
    <div class="tb-name">Trend Bot — 60m Donchian Basket &nbsp; {_badge("PAPER", "amb")} &nbsp; {status_badge} &nbsp; {mode_badge} <span style="font-size:10px;color:var(--t3);font-weight:500;">gear set by you → applied as <b>{(state.get("rotation") or {}).get("applied", "—")}</b> this week by the spread test</span></div>
    <div class="tb-sub">MNQ · MES · MYM core (weekly rotation, {LOOKBACK}-day ranking) + MGC sleeve · IBKR data → IBKR paper · paper activity since {paper_start or "—"} · {soak_status} · Sep contracts, roll ≈ Sep 18</div>
  </div>
</div>
""", unsafe_allow_html=True)

if halted:
    st.markdown(
        f'<div style="background:var(--red-bg);border:1px solid var(--red);'
        f'color:var(--red);padding:8px 12px;border-radius:6px;margin:6px 0;'
        f'font-size:13px;font-weight:700;">🚨 ENTRIES HALTED — '
        f'{state.get("halt_reason") or "see events.log"}</div>',
        unsafe_allow_html=True)
if not bot_alive:
    st.markdown(
        '<div style="background:var(--red-bg);border:1px solid var(--red);'
        'color:var(--red);padding:8px 12px;border-radius:6px;margin:6px 0;'
        'font-size:13px;font-weight:700;">🚨 NO RECENT HEARTBEAT — bot process '
        'down or wedged. <span style="font-family:var(--mono)">launchctl list '
        '| grep trendbot</span></div>', unsafe_allow_html=True)

# ── strip: P&L + rotation at a glance ────────────────────────────────────────
# NET-of-fees from the authoritative journal (corrections included) —
# gross CSV overstated the record by $143.64 (review r6)
closed_core = closed_slv = closed_sat = 0.0
if len(trades):
    g = trades.groupby("book")["pnl_usd"].sum()
    closed_core = float(g.get("core", 0.0))
    closed_slv = float(g.get("sleeve", 0.0))
    closed_sat = float(g.get("satellite", 0.0))
ledger_net = None
ledger_books = None
ledger_corrections = 0.0
try:
    import sqlite3 as _sq
    _con = _sq.connect(SDIR / "trendbot.db")
    ledger_net = float(_con.execute(
        "SELECT net_usd FROM v_net_pnl").fetchone()[0])
    ledger_books = {r[0]: float(r[1] or 0) for r in _con.execute(
        "SELECT book,SUM(calculated_net) FROM v_trade_net GROUP BY book")}
    ledger_corrections = float(_con.execute(
        "SELECT COALESCE(SUM(delta_usd),0) FROM corrections").fetchone()[0])
    _con.close()
except Exception as e:
    st.warning(f"Accounting journal unavailable; showing legacy projection: {e}")

if ledger_books is not None:
    closed_core = ledger_books.get("core", 0.0)
    closed_slv = ledger_books.get("sleeve", 0.0)
    closed_sat = ledger_books.get("satellite", 0.0)

open_core = open_slv = open_sat = 0.0
open_fee_core = open_fee_slv = open_fee_sat = 0.0
positions = state.get("positions") or {}
for sym, p in positions.items():
    if p.get("position"):
        lc = _last_close(sym)
        if lc is not None:
            up = (lc - p["entry_px"]) * MULTS[sym] * p["qty"]
            fee = (float(p.get("entry_commission") or 0)
                   if not p.get("entry_fees_allocated", False) else 0.0)
            if sym == "GC":
                open_slv += up
                open_fee_slv += fee
            elif sym in SAT_SYMS:
                open_sat += up
                open_fee_sat += fee
            else:
                open_core += up
                open_fee_core += fee

open_all = open_core + open_slv + open_sat
total = closed_core + closed_slv + closed_sat + open_all
book_core_total = closed_core + open_core - open_fee_core
book_slv_total = closed_slv + open_slv - open_fee_slv
book_sat_total = closed_sat + open_sat - open_fee_sat
rot = state.get("rotation") or {}
leader = (rot.get("ranking") or ["—"])[0]
spread = rot.get("spread_pp")
n_open = sum(1 for p in positions.values() if p.get("position"))
# Locked floor: what the book banks if every stop fires AT its trigger
# (net of $3.50 RT/contract; stop-markets can fill worse through gaps).
_floors = {s: (p["stop_px"] - p["entry_px"]) * MULTS[s] * p["qty"]
              - 3.50 * p["qty"]
           for s, p in positions.items() if p.get("position")}
floor_total = sum(_floors.values())
floor_sub = (" · ".join(f"{s} {_fmt_usd(v)}" for s, v in _floors.items())
             + " · " if len(_floors) > 1 else "") + \
    "if stops fire at trigger · gaps can fill worse"

_row1 = (_strip_cell("Paper P&L (net)",
                     _fmt_usd((ledger_net or 0) + open_all
                              if ledger_net is not None else total),
                     f"net of known fees+corrections · since "
                     f"{paper_start or '—'}",
                     _pnl_color((ledger_net or 0) + open_all
                                if ledger_net is not None else total),
                     big=True)
         + _strip_cell("Core book", _fmt_usd(book_core_total),
                       f"open {_fmt_usd(open_core - open_fee_core)} net",
                       _pnl_color(book_core_total))
         + _strip_cell("Gold sleeve", _fmt_usd(book_slv_total),
                       f"open {_fmt_usd(open_slv - open_fee_slv)} net",
                       _pnl_color(book_slv_total))
         + _strip_cell("Satellite", _fmt_usd(book_sat_total),
                       f"open {_fmt_usd(open_sat - open_fee_sat)} net",
                       _pnl_color(book_sat_total))
         + _strip_cell("Rotation leader", leader,
                       f"spread {spread}pp" if spread is not None else "unsure → 1-1-1",
                       "vb"))
_live_sub = " · ".join(f"in {p['entry_px']:,.1f} stop {p['stop_px']:,.1f}"
                       for s, p in positions.items() if p.get("position"))
_row2 = ((_strip_cell("Live position" + ("s" if n_open > 1 else ""),
                      " · ".join(f"{s} {p['qty']}×"
                                 for s, p in positions.items()
                                 if p.get("position")),
                      _live_sub, "vg", glow="grn")
          if n_open else
          _strip_cell("Open positions", f"0 / {len(MULTS)}",
                      f"{len(trades)} closed trades"))
         + (_strip_cell("Min outcome", _fmt_usd(floor_total),
                        floor_sub, _pnl_color(floor_total),
                        glow="grn" if floor_total > 0 else None)
            if n_open else ""))
# short position detail rides in the top row; long detail gets its own row
if n_open == 0 or len(_live_sub) + len(floor_sub) <= 100:
    st.markdown(f'<div class="strip">{_row1}{_row2}</div>',
                unsafe_allow_html=True)
else:
    st.markdown(f'<div class="strip strip-top">{_row1}</div>'
                f'<div class="strip">{_row2}</div>',
                unsafe_allow_html=True)

# ---- market-energy lamp (observation only; PREREG_REGIME_FORWARD.md) -------
_vix = state.get("vix")
_reg = state.get("vix_regime")
if _vix:
    _lamp = {"asleep": ("#8c959f", "😴 ASLEEP", "historically ≈ breakeven — patience mode"),
             "normal": ("#0969da", "🌊 NORMAL", "historically moderate expectancy"),
             "energetic": ("#d97706", "⚡ ENERGETIC", "historically the paying regime")}.get(
                 _reg, ("#8c959f", "—", ""))
    st.markdown(
        f'<div style="margin:2px 0 8px 0;font-size:11px;color:var(--t3);">'
        f'MARKET ENERGY&nbsp; <span style="background:{_lamp[0]};color:#fff;'
        f'padding:2px 10px;border-radius:10px;font-weight:700;font-size:10px;">'
        f'{_lamp[1]} · VIX {_vix:.1f}</span>&nbsp; {_lamp[2]} · forward trial '
        f'running, no trading impact</div>', unsafe_allow_html=True)

# ---- portfolio story -------------------------------------------------------
if n_open == 0:
    port_story = ("The book is <b>fully flat and waiting</b> — no index has "
                  "closed above its 20-hour breakout channel. That is normal "
                  "behavior for this system: it does nothing until a market "
                  "proves it is moving, then rides it with a trailing stop.")
else:
    held = []
    for s, p in positions.items():
        if p.get("position"):
            locked = (p["stop_px"] - p["entry_px"]) * MULTS[s] * p["qty"]
            held.append(
                f"<b>{p['qty']}× {SYM_NAMES[s].split(' ·')[0]}</b> in at "
                f"<b>{p['entry_px']:,.2f}</b>, stop <b>{p['stop_px']:,.2f}</b> "
                f"({'locking in ~' + _fmt_usd(locked) if locked > 0 else 'modeled risk ~' + _fmt_usd(locked)})")
    port_story = (f"Holding {'; '.join(held)} — the stop is a GTC order "
                  f"held on IB's servers (it survives this machine going "
                  f"dark, but it is NOT an exchange guarantee: it triggers "
                  f"as a market order, so gaps can fill worse than the "
                  f"stop price). Open P&L {_fmt_usd(open_all)}.")
if halted:
    port_story += " <b>⚠ Entries are currently halted</b> — see the banner above."
st.markdown(f'<div class="story">{port_story}</div>', unsafe_allow_html=True)
last_run_slot = st.container()   # filled by the report-card summary below

# ── rotation (the week's headline decision — stays above the fold) ──────────
_section(f"WEEKLY ROTATION — THE ORGANISM ({LOOKBACK}-DAY RANKING)")
st.markdown(f'<div class="story">{_rotation_story(rot, mode)}</div>',
            unsafe_allow_html=True)
if rot:
    rets = rot.get("returns") or {}
    kpis = "".join(
        _kpi(f"#{i+1} {s}", f"{rets.get(s, 0)*100:+.2f}%",
             f"{rot.get('lookback', LOOKBACK)}-day return",
             "vg" if i == 0 else "") for i, s in enumerate(rot.get("ranking", [])))
    kpis += _kpi("Applied", rot.get("applied", "—"),
                 f"week of {str(rot.get('week',''))[:10]} · "
                 + ("UNSURE → equal weight" if rot.get("unsure") else
                    f"spread {rot.get('spread_pp')}pp"),
                 "va" if rot.get("unsure") else "vb")
    st.markdown(f'<div class="kpis">{kpis}</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="empty">no rebalance computed yet</div>',
                unsafe_allow_html=True)

# ── positions ────────────────────────────────────────────────────────────────
_section("POSITIONS & STOPS")
w = rot.get("weights") or {}
# satellite cards only while a satellite position is open (intraday lane)
_show_syms = list(SYMS) + [s for s in SAT_SYMS
                           if (positions.get(s) or {}).get("position")]
for sym in _show_syms:
    p = positions.get(sym) or {}
    is_open = bool(p.get("position"))
    d = decisions[decisions["sym"] == sym].tail(1) if len(decisions) else pd.DataFrame()
    gates = ""
    if len(d):
        r = d.iloc[0]
        gates = (_badge("BREAKOUT" if r["breakout"] else "NO BREAKOUT",
                        "grn" if r["breakout"] else "dim")
                 + " " + _badge("VOL OK" if r["vol_ok"] else "VOL BLOCKED",
                                "grn" if r["vol_ok"] else "red")
                 + " " + _badge("SESSION OK" if r["session_ok"] else "SESSION BLOCKED",
                                "grn" if r["session_ok"] else "red"))
    weight = 1 if sym == "GC" or sym in SAT_SYMS else w.get(sym, "—")
    lc = _last_close(sym)
    if is_open:
        upnl = (lc - p["entry_px"]) * MULTS[sym] * p["qty"] if lc else None
        fields = [
            ("status", f"LONG {p['qty']}×", "vg"),
            ("contract", p.get("local_symbol") or "—", ""),
            ("entry", f"{p['entry_px']:,.2f}", ""),
            ("stop", f"{p['stop_px']:,.2f}", ""),
            ("high-water", f"{p['hw']:,.2f}", ""),
            ("unrealized", _fmt_usd(upnl) if upnl is not None else "—",
             _pnl_color(upnl or 0)),
        ]
    else:
        fields = [
            ("status", "FLAT", ""),
            ("weight", f"{weight}×", ""),
            ("last close", f"{lc:,.2f}" if lc else "—", ""),
            ("donchian", (f"{d.iloc[0]['donchian']:,.2f}"
                          if len(d) and pd.notna(d.iloc[0]["donchian"]) else "—"), ""),
            ("atr", (f"{d.iloc[0]['atr']:,.2f}"
                     if len(d) and pd.notna(d.iloc[0]["atr"]) else "—"), ""),
            ("", "", ""),
        ]
    cells = "".join(
        f'<div><div class="pfl">{fl}</div><div class="pfv {fc}">{fv}</div></div>'
        for fl, fv, fc in fields if fl or fv)
    d_row = d.iloc[0].to_dict() if len(d) else None
    story = _instr_story(sym, p, d_row, lc, weight)
    st.markdown(
        f'<div class="pc{" pos" if is_open else ""}">'
        f'<div class="pc-h"><div class="pc-n">{SYM_NAMES[sym]}</div>'
        f'<div>{gates}</div></div>'
        f'<div class="pf">{cells}</div>'
        f'<div class="pfs">{story}</div></div>', unsafe_allow_html=True)

# ── equity curve ─────────────────────────────────────────────────────────────
_section("EQUITY vs BACKTEST PACE")
if len(equity):
    equity["ts"] = pd.to_datetime(equity["ts"])
    piv = equity.pivot_table(index="ts", columns="book", values="total_usd",
                             aggfunc="last").ffill().fillna(0)
    piv["total"] = piv.sum(axis=1)
    days = (piv.index - piv.index[0]).total_seconds() / 86400
    piv["backtest pace"] = days * sum(BACKTEST_PACE_PER_DAY.values())
    st.line_chart(piv[[c for c in ("total", "core", "sleeve", "satellite",
                                   "backtest pace")
                       if c in piv.columns]], height=260)
else:
    st.markdown('<div class="empty">no equity marks yet — first wake pending</div>',
                unsafe_allow_html=True)

# ── trades ───────────────────────────────────────────────────────────────────
_section("CLOSED TRADES")
if len(trades):
    show = trades.tail(25)[["exit_time", "book", "sym", "qty", "entry_px",
                            "exit_px", "reason", "pnl_usd", "commission"]]
    st.dataframe(show, use_container_width=True, hide_index=True)
else:
    st.markdown('<div class="empty">no closed trades yet — the system waits '
                'for a breakout; days of flat is normal behavior</div>',
                unsafe_allow_html=True)

# ── hourly report card ───────────────────────────────────────────────────────
def _read_jsonl(stream: str, days: int = 3) -> list[dict]:
    rows = []
    for d in range(days):
        day = (datetime.now(UTC) - pd.Timedelta(days=d)).strftime("%Y%m%d")
        p = SDIR / "logs" / f"{stream}-{day}.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def _mkt_open(ts_utc) -> bool:
    et = ts_utc.tz_convert("America/New_York")
    wd, h = et.weekday(), et.hour
    if wd == 5:
        return False
    if wd == 6:
        return h >= 18
    if wd == 4 and h >= 17:
        return False
    return h != 17


def _fmt_et(ts) -> str:
    return ts.tz_convert("America/New_York").strftime("%a %-I:%M %p")


_section("HOURLY REPORT CARD (LAST 12 CHECKS)")
if len(decisions):
    dec = decisions.copy()
    dec["ts_dt"] = pd.to_datetime(dec["ts"], utc=True)
    dec["wake_h"] = dec["ts_dt"].dt.floor("h")
    wakes_log = {pd.Timestamp(w["ts"]).floor("h"): w
                 for w in _read_jsonl("wakes") if w.get("event") == "wake"}
    stop_evts = [s for s in _read_jsonl("stops")
                 if s.get("event") in ("armed", "modified", "replaced", "filled")]
    tr = trades.copy()
    if len(tr):
        tr["exit_dt"] = pd.to_datetime(tr["exit_time"], utc=True)

    # failed wakes (no decisions written) get their own red rows —
    # sourced from BOTH the wakes deep-log and events.log (covers history
    # from before wake_failed instrumentation existed)
    failed = {pd.Timestamp(w["ts"]).floor("h"): w.get("error", "")
              for w in _read_jsonl("wakes") if w.get("event") == "wake_failed"}
    ev_p2 = SDIR / "events.log"
    if ev_p2.exists():
        for line in ev_p2.read_text().splitlines():
            if "CONNECT FAILED" in line:
                try:
                    ts = pd.Timestamp(line.split(" ", 1)[0])
                    failed.setdefault(ts.floor("h"), line.split(":", 3)[-1][:80])
                except Exception:
                    pass
    groups = sorted(set(dec["wake_h"].unique()) | set(failed))[-12:][::-1]
    latest_bits = None
    rows_html = []
    for h in groups:
        h = pd.Timestamp(h)
        if h in failed and h not in set(dec["wake_h"]):
            rows_html.append(
                f"<tr style='border-top:1px solid var(--bd);background:var(--red-bg)'>"
                f"<td style='white-space:nowrap;font-family:var(--mono);padding:5px 10px;"
                f"color:var(--red);font-weight:700'>{_fmt_et(h)}</td>"
                f"<td>{'🟢' if _mkt_open(h) else '⚪'}</td><td>—</td>"
                f"<td style='color:var(--red);font-weight:600'>CHECK FAILED — "
                f"could not reach gateway ({str(failed[h])[:60]}…) — entries "
                f"paused, resting stops unaffected</td><td>—</td></tr>")
            if latest_bits is None:
                latest_bits = (h, None, ["CHECK FAILED — gateway unreachable"], "—")
            continue
        g = dec[dec["wake_h"] == h]
        bits = []
        for _, r in g[g["acted"] == True].iterrows():          # noqa: E712
            bits.append(f"<b>{r['sym']} ENTRY</b>")
        for _, r in g[(g["signal"] == True) & (g["acted"] != True)].iterrows():  # noqa: E712
            bits.append(f"{r['sym']} signal blocked ({r['note'] or 'weight 0'})")
        for s in stop_evts:
            sts = pd.Timestamp(s["ts"])
            if h <= sts < h + pd.Timedelta(hours=1):
                if s["event"] in ("modified", "replaced"):
                    bits.append(f"{s['sym']} trail {s.get('old_px')}→{s.get('new_px')}")
                elif s["event"] == "filled":
                    bits.append(f"<b>{s['sym']} STOP FILLED @ {s.get('px')}</b>")
        if len(tr):
            in_h = tr[(tr["exit_dt"] >= h) & (tr["exit_dt"] < h + pd.Timedelta(hours=1))]
            for _, t in in_h.iterrows():
                bits.append(f"<b>{t['sym']} exit ({t['reason']}) ${t['pnl_usd']:+,.0f}</b>")
        if not bits:
            gaps = []
            for _, r in g.iterrows():
                w = r["weight"]
                if w and w > 0 and pd.notna(r["donchian"]) and pd.notna(r["close"]) and r["close"]:
                    gp = (r["donchian"] - r["close"]) / r["close"] * 100
                    if gp > 0:
                        gaps.append(f"{r['sym']} {gp:.2f}% away")
            if _mkt_open(h):
                bits.append("no breakouts — " + ", ".join(gaps) + " from trigger"
                            if gaps else "no breakouts")
            else:
                bits.append("market closed — watching only")
        wl = wakes_log.get(h, {})
        dur = f"{wl.get('total_ms', 0)/1000:.1f}s" if wl else "—"
        rows_html.append(
            f"<tr style='border-top:1px solid var(--bd)'>"
            f"<td style='white-space:nowrap;font-family:var(--mono);padding:5px 10px'>{_fmt_et(h)}</td>"
            f"<td>{'🟢' if _mkt_open(h) else '⚪'}</td>"
            f"<td style='font-family:var(--mono)'>{g['sym'].nunique()}/4</td>"
            f"<td>{' · '.join(bits)}</td>"
            f"<td style='color:var(--t3);font-family:var(--mono)'>{dur}</td></tr>")
        if latest_bits is None:
            latest_bits = (h, g, bits, dur)

    if latest_bits:
        h, g, bits, dur = latest_bits
        pos_syms = [s for s, p in (state.get("positions") or {}).items()
                    if p.get("position")]
        pos_txt = (f"holding {', '.join(pos_syms)} with broker-held GTC "
                   "stop orders (trigger/execution not guaranteed)"
                   if pos_syms else "no positions to manage")
        plain = "; ".join(b.replace("<b>", "").replace("</b>", "") for b in bits)
        with last_run_slot:
            st.markdown(
                f'<div class="story" style="border-left-color:var(--grn)">'
                f'Last run <b>{_fmt_et(h)} ET</b>: checked '
                f'{g["sym"].nunique() if g is not None else 0}/4 markets in {dur} — {plain}. Currently '
                f'{pos_txt}. Next check at the top of the hour (+25s).</div>',
                unsafe_allow_html=True)

    st.markdown(
        "<table style='width:100%;font-size:12px;border-collapse:collapse;"
        "background:var(--p1);border:1px solid var(--bd);border-radius:8px'>"
        "<tr style='color:var(--t3);text-align:left;font-size:10px;"
        "text-transform:uppercase'><th style='padding:6px 10px'>check</th>"
        "<th>mkt</th><th>scanned</th><th>what happened</th><th>took</th></tr>"
        + "".join(rows_html) + "</table>", unsafe_allow_html=True)
else:
    st.markdown('<div class="empty">no checks logged yet</div>',
                unsafe_allow_html=True)

# ── recent events ────────────────────────────────────────────────────────────
_section("EVENT LOG (LAST 12)")
ev_p = SDIR / "events.log"
if ev_p.exists():
    lines = ev_p.read_text().splitlines()[-12:]
    st.markdown(f'<div class="evt">{chr(10).join(lines)}</div>',
                unsafe_allow_html=True)
else:
    st.markdown('<div class="empty">no events yet</div>', unsafe_allow_html=True)

st.markdown(
    '<div style="color:var(--t3);font-size:11px;padding-top:12px;">'
    'data: IBKR live gateway (4001) · orders: IBKR paper (7497) · '
    'mode toggle: <span style="font-family:var(--mono)">python bot.py --mode '
    'defensive|default|aggressive</span> · auto-refresh 60s</div>',
    unsafe_allow_html=True)
st.markdown("<meta http-equiv='refresh' content='60'>", unsafe_allow_html=True)
