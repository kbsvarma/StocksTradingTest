"""ADVISOR TERMINAL — Bloomberg-style research console (read-only).

Renders the advisor system's exhaust: live tape, research feed with
evidence + timestamps, trade-plan charts with entry/stop/target bands,
full-market factor sheets with attribution, portfolio/risk, scorecard,
doctrine. NO execution code paths — Tier-0 guarantee.

Run:  streamlit run advisor/terminal.py --server.port 8505
launchd: com.stockstest.advisor-terminal
"""
from __future__ import annotations

import json
import re
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
CTX = DATA / "context"
RESEARCH = DATA / "research"

AMBER = "#ff9f0a"
GREEN = "#33d17a"
RED = "#ff5c57"
DIM = "#8a8f98"

st.set_page_config(page_title="ADVISOR TERMINAL", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  .stApp { background-color: #0b0e11; }
  /* kill Streamlit's floating chrome — it was hiding the ADVISOR TERMINAL title */
  header[data-testid="stHeader"] { display: none !important; }
  #MainMenu, footer { visibility: hidden; }
  html, body, [class*="css"] { font-family: "SF Mono", Menlo, monospace; }
  h1,h2,h3 { color: #ff9f0a !important; font-family: "SF Mono", Menlo, monospace !important;
             letter-spacing: 1px; }
  .block-container { padding-top: 0.6rem; padding-bottom: 1rem; max-width: 100% !important; }
  div[data-testid="stMetricValue"] { font-size: 1.05rem; color: #e8e6e3; }
  div[data-testid="stMetricLabel"] { color: #8a8f98; }
  .stTabs [data-baseweb="tab"] { color: #8a8f98; font-family: Menlo, monospace; }
  .stTabs [aria-selected="true"] { color: #ff9f0a !important; }
  thead tr th { background-color: #11151a !important; color: #ff9f0a !important; }
</style>
""", unsafe_allow_html=True)


# ── helpers ──────────────────────────────────────────────────────────────────

def chip(text: str, color: str = DIM) -> str:
    text = text.replace("$", "&#36;")   # avoid Streamlit LaTeX interpretation
    return (f'<span style="border:1px solid {color}; color:{color}; '
            f'border-radius:3px; padding:0px 6px; font-size:11px; '
            f'font-family:Menlo,monospace; margin-right:6px;">{text}</span>')


def panel_header(title: str, sub: str = "") -> None:
    st.markdown(
        f'<div style="border-bottom:1px solid #2a2f36; margin:4px 0 10px 0;">'
        f'<span style="color:{AMBER}; font-family:Menlo,monospace; font-size:15px; '
        f'font-weight:700; letter-spacing:2px;">{title}</span>'
        f'<span style="color:{DIM}; font-size:11px; margin-left:12px;">{sub}</span></div>',
        unsafe_allow_html=True)


@st.cache_data(ttl=55)
def tape_quotes(tickers: tuple) -> list[dict]:
    import yfinance as yf
    out = []
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            fi = tk.fast_info
            px = fi.last_price
            prev = fi.previous_close
            out.append({"t": t, "px": px, "chg": (px / prev - 1) * 100 if prev else 0,
                        "ts": datetime.now(ET).strftime("%H:%M:%S")})
        except Exception:
            out.append({"t": t, "px": None, "chg": 0, "ts": "—"})
    return out


def journal_effective() -> dict:
    p = DATA / "decision_journal.jsonl"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = e.get("id", "?")
        out[eid] = {**out.get(eid, {}), **e}
    return out


def load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── TAPE ─────────────────────────────────────────────────────────────────────

@st.fragment(run_every="60s")
def tape():
    calls = journal_effective()
    open_tkrs = [e.get("yf_ticker") for e in calls.values()
                 if e.get("status") == "open" and e.get("yf_ticker")]
    base = ["^GSPC", "^NDX", "IWM", "^VIX", "^TNX", "CL=F", "GLD", "BTC-USD"]
    tickers = tuple(dict.fromkeys(open_tkrs + base))[:14]
    q = tape_quotes(tickers)
    cells = []
    for x in q:
        if x["px"] is None:
            continue
        col = GREEN if x["chg"] >= 0 else RED
        cells.append(
            f'<td style="padding:2px 14px 2px 0; white-space:nowrap;">'
            f'<span style="color:{AMBER};">{x["t"]}</span> '
            f'<span style="color:#e8e6e3;">{x["px"]:,.2f}</span> '
            f'<span style="color:{col};">{x["chg"]:+.2f}%</span></td>')
    st.markdown(
        f'<table><tr>{"".join(cells)}</tr></table>'
        f'<div style="color:{DIM}; font-size:10px;">source: yfinance (delayed ~15min) '
        f'· as of {datetime.now(ET).strftime("%H:%M:%S ET %Y-%m-%d")} · auto-refresh 60s</div>',
        unsafe_allow_html=True)


# ── PANELS ───────────────────────────────────────────────────────────────────

def research_feed():
    days = sorted([d.name for d in CTX.iterdir() if d.is_dir()], reverse=True) \
        if CTX.exists() else []
    if not days:
        st.info("No briefs yet.")
        return
    day = st.selectbox("brief date", days, index=0, label_visibility="collapsed")
    brief = CTX / day / "brief.md"
    bj = load_json(CTX / day / "brief.json")  # structured (newer briefs)
    panel_header("RESEARCH FEED", f"{day} · generated by the morning loop engine")
    if bj and bj.get("views"):
        for v in bj["views"]:
            _view_card(v)
    if brief.exists():
        mtime = datetime.fromtimestamp(brief.stat().st_mtime, ET)
        st.markdown(chip(f"brief.md · written {mtime.strftime('%H:%M ET')}", DIM),
                    unsafe_allow_html=True)
        import html as _html
        body = _html.escape(brief.read_text()).replace("$", "&#36;")
        st.markdown(f'<div style="color:#c9c7c2; font-size:13px; white-space:pre-wrap; '
                    f'font-family:Menlo,monospace; background:#11151a; padding:14px; '
                    f'border:1px solid #2a2f36; border-radius:4px;">'
                    f'{body}</div>', unsafe_allow_html=True)


def _view_card(v: dict):
    conv = (v.get("conviction") or "?").upper()
    cc = GREEN if conv == "HIGH" else AMBER
    rows = ""
    for ev in v.get("evidence", []):
        link = (f' <a href="{ev["url"]}" target="_self" style="color:{AMBER};">[src]</a>'
                if ev.get("url") else "")
        ts = chip(ev.get("retrieved", ""), DIM) if ev.get("retrieved") else ""
        rows += f'<div style="color:#c9c7c2; font-size:12px; margin:2px 0;">• {ev.get("claim","")}{link} {ts}</div>'
    st.markdown(
        f'<div style="border:1px solid #2a2f36; border-left:3px solid {cc}; '
        f'background:#11151a; padding:12px; margin-bottom:10px; border-radius:4px;">'
        f'<span style="color:{cc}; font-weight:700;">{v.get("instrument","?")} — '
        f'{v.get("direction","")} — {conv}</span><br>'
        f'<span style="color:#e8e6e3; font-size:13px;">{v.get("thesis","")}</span>'
        f'{rows}'
        f'<div style="margin-top:6px;">{chip("ENTRY " + str(v.get("entry","—")), "#e8e6e3")}'
        f'{chip("TARGET " + str(v.get("target","—")), GREEN)}'
        f'{chip("STOP " + str(v.get("stop","—")), RED)}'
        f'{chip("TIME " + str(v.get("time_stop","—")), DIM)}</div></div>',
        unsafe_allow_html=True)


def open_calls_and_charts():
    panel_header("OPEN CALLS · TRADE PLANS", "journal + exit-watcher levels, live chart bands")
    calls = {k: v for k, v in journal_effective().items() if v.get("status") == "open"}
    if not calls:
        st.info("No open calls.")
        return
    import plotly.graph_objects as go
    import yfinance as yf
    for eid, e in calls.items():
        tkr = e.get("yf_ticker")
        cols = st.columns([2, 3])
        with cols[0]:
            _view_card({**e, "evidence": []})
            st.markdown(chip(f"journaled {e.get('ts','')[:16]}", DIM)
                        + chip(f"id {eid}", DIM), unsafe_allow_html=True)
        with cols[1]:
            if not tkr:
                continue
            try:
                h = yf.Ticker(tkr).history(period="6mo")
                fig = go.Figure(go.Candlestick(
                    x=h.index, open=h.Open, high=h.High, low=h.Low, close=h.Close,
                    increasing_line_color=GREEN, decreasing_line_color=RED))
                for key, col, lbl in (("target_px", GREEN, "target"),
                                      ("stop_px", RED, "stop")):
                    if isinstance(e.get(key), (int, float)):
                        fig.add_hline(y=e[key], line_color=col, line_dash="dot",
                                      annotation_text=f"{lbl} {e[key]}",
                                      annotation_font_color=col)
                lo, hi = e.get("entry_px_low"), e.get("entry_px_high")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    fig.add_hrect(y0=lo, y1=hi, fillcolor=AMBER, opacity=0.15,
                                  line_width=0, annotation_text="entry zone",
                                  annotation_font_color=AMBER)
                fig.update_layout(template="plotly_dark", height=320,
                                  margin=dict(l=10, r=10, t=10, b=10),
                                  paper_bgcolor="#0b0e11", plot_bgcolor="#11151a",
                                  xaxis_rangeslider_visible=False, showlegend=False)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"chart_{eid}")
            except Exception as exc:
                st.warning(f"{tkr}: chart unavailable ({exc})")


def factor_sheets():
    s = load_json(RESEARCH / "signals_latest.json")
    if not s:
        st.info("No factor sheet yet — nightly research job hasn't run.")
        return
    reg = s["regime"]
    panel_header("FACTOR CROSS-SECTION",
                 f"{s['as_of'][:16]} · {s['n_liquid']} liquid of {s['n_universe']} names · "
                 f"regime {reg['name'].upper()} (VIX {reg['vix']} term {reg['vix_term']}) · "
                 f"panel age {s['panel_age_hours']}h")
    st.markdown(chip(s["method"], DIM), unsafe_allow_html=True)
    tabs = st.tabs(["LONGS", "SHORTS", "SHOCK (REVERSION CANDIDATES)"])
    for tab, key in zip(tabs, ("longs", "shorts", "shock_candidates")):
        with tab:
            rows = []
            for x in s.get(key, []):
                rows.append({
                    "tkr": x["ticker"], "px": x["px"], "score": x["score"],
                    "sector": x["sector"],
                    "mom12%": x["raw"]["mom_12_1_pct"], "r1m%": x["raw"]["ret_1m_pct"],
                    "%52wH": x["raw"]["pct_of_52w_high"],
                    "rv60": x["raw"]["rv60_ann_pct"],
                    "$vol(M)": x["raw"]["dollar_vol_21d_m"],
                    "z: " + " ".join(f"{k.split('_')[0]}" for k in x["attribution"]):
                        " ".join(f"{v:+.1f}" if v is not None else "  — "
                                 for v in x["attribution"].values()),
                    "⚡": "⚡" if x.get("shock") else "",
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True,
                             hide_index=True, height=420)


def portfolio_risk():
    # ADVISOR BOOK ONLY — the legacy bot's pre-advisor record is a different
    # strategy and is deliberately not shown here (user rule 2026-06-12).
    panel_header("PORTFOLIO / RISK", "advisor book only — strategy started 2026-06-12")
    calls = journal_effective()
    open_calls = {k: v for k, v in calls.items() if v.get("status") == "open"}
    resolved = {k: v for k, v in calls.items()
                if v.get("status") in ("hit_target", "stopped", "time_stop", "closed")}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BUDGET", "$25,000", "advisor mandate")
    c2.metric("OPEN CALLS", len(open_calls))
    c3.metric("RESOLVED CALLS", len(resolved),
              f"{sum(1 for v in resolved.values() if v.get('status')=='hit_target')} hit target")
    # live executions via Tier 1 land in bot state — show only if one is open
    days = sorted([d.name for d in CTX.iterdir() if d.is_dir()], reverse=True) \
        if CTX.exists() else []
    snap = load_json(CTX / days[0] / "portfolio.json") if days else None
    op = (snap or {}).get("state", {}).get("open_position")
    c4.metric("TIER-1 POSITION", f"{op.get('short_strike')}/{op.get('long_strike')}P"
              if op else "NONE")
    if open_calls:
        rows = [{"id": k, "instrument": v.get("instrument"), "dir": v.get("direction"),
                 "conviction": v.get("conviction"), "entry": v.get("entry"),
                 "target": v.get("target"), "stop": v.get("stop"),
                 "time_stop": v.get("time_stop"), "since": (v.get("ts") or "")[:16]}
                for k, v in open_calls.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    alerts = {k: v for k, v in (load_json(DATA / "watcher_alerts.json") or {}).items() if v}
    if alerts:
        st.markdown(f'<div style="color:{DIM}; font-size:12px;">exit-watcher alerts fired: '
                    + ", ".join(f"{k} ({','.join(v.keys())})" for k, v in alerts.items())
                    + "</div>", unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="color:{DIM}; font-size:12px;">exit-watcher: armed on all '
                    f'open calls — no levels hit yet</div>', unsafe_allow_html=True)


def scorecard_doctrine():
    panel_header("SCORECARD · DOCTRINE", "every call, accountable; the engine's rules")
    calls = journal_effective()
    rows = [{"id": k, "status": v.get("status"), "type": v.get("type"),
             "instrument": v.get("instrument"), "dir": v.get("direction"),
             "entry": v.get("entry"), "target": v.get("target"),
             "stop": v.get("stop"), "ts": (v.get("ts") or "")[:16],
             "note": v.get("note", "")}
            for k, v in calls.items()]
    if rows:
        df = pd.DataFrame(rows)
        resolved = df[df.status.isin(["hit_target", "stopped", "time_stop", "closed"])]
        wins = (resolved.status == "hit_target").sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("CALLS (ALL)", len(df))
        c2.metric("RESOLVED", len(resolved))
        c3.metric("HIT RATE", f"{wins/len(resolved)*100:.0f}%" if len(resolved) else "—")
        st.dataframe(df, use_container_width=True, hide_index=True, height=260)
    val = load_json(RESEARCH / "ic_validation.json")
    if val:
        wf = val.get("walk_forward_top20") or {}
        d = val.get("decay") or {}
        st.markdown(
            f'<div style="border:1px solid #2a2f36; background:#11151a; padding:10px; '
            f'border-radius:4px; margin:8px 0;">'
            f'<span style="color:{AMBER}; font-weight:700;">SIGNAL VALIDATION</span> '
            + chip(f"as of {val.get('as_of','')[:16]}", DIM)
            + f'<div style="color:#c9c7c2; font-size:12px; margin-top:6px;">'
            f'walk-forward top-20: <span style="color:{GREEN};">'
            f'{wf.get("total_return_pct","?"):+.1f}%</span> vs SPY '
            f'{wf.get("spy_total_pct","?"):+.1f}% ({wf.get("n_periods","?")} periods) · '
            f'beta {wf.get("beta_vs_spy","?")} → alpha '
            f'<span style="color:{GREEN};">{wf.get("alpha_per_21d_pct","?"):+.2f}%/21d</span> · '
            f'worst {wf.get("worst_period_pct","?"):+.1f}%<br>'
            + " · ".join(f'{k} IC {s["mean_ic"]:+.3f} (t {s["t_stat"]})'
                         for k, s in (val.get("factors") or {}).items() if s)
            + f'<br>decay: rank autocorr {d.get("rank_autocorr_21d","?")} · '
            f'top-decile retention {d.get("top_decile_retention_21d","?")}'
            + (f' · shock drift {val["pead_shock_drift"]["mean_signed_drift_excess_21d_pct"]:+.2f}%/21d '
               f'(t {val["pead_shock_drift"]["t_stat"]}) → REVERSION'
               if val.get("pead_shock_drift") else "")
            f'</div><div style="color:{DIM}; font-size:10px; margin-top:4px;">'
            + " · ".join(val.get("caveats", [])) + "</div></div>",
            unsafe_allow_html=True)
    with st.expander("METHODOLOGY (loop doctrine + lessons log)"):
        st.markdown((REPO / "advisor" / "METHODOLOGY.md").read_text())
    with st.expander("IPS (investment policy)"):
        st.markdown((REPO / "advisor" / "IPS.md").read_text())


# ── LAYOUT ───────────────────────────────────────────────────────────────────

st.markdown(f'<div style="color:{AMBER}; font-size:22px; font-weight:800; '
            f'letter-spacing:3px; font-family:Menlo,monospace;">ADVISOR TERMINAL'
            f'<span style="color:{DIM}; font-size:12px; letter-spacing:1px;"> '
            f'· read-only research console · no execution paths</span></div>',
            unsafe_allow_html=True)
tape()
st.markdown("<hr style='border-color:#2a2f36; margin:8px 0;'>", unsafe_allow_html=True)

t1, t2, t3, t4, t5 = st.tabs(
    ["📡 RESEARCH", "🎯 OPEN CALLS", "🧮 FACTOR SHEETS", "💼 PORTFOLIO", "📊 SCORECARD"])
with t1:
    research_feed()
with t2:
    open_calls_and_charts()
with t3:
    factor_sheets()
with t4:
    portfolio_risk()
with t5:
    scorecard_doctrine()
