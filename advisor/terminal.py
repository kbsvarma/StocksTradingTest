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
  .stApp { background-color: #000000; }
  /* kill Streamlit's floating chrome — it was hiding the ADVISOR TERMINAL title */
  header[data-testid="stHeader"] { display: none !important; }
  #MainMenu, footer { visibility: hidden; }
  html, body, [class*="css"] { font-family: "SF Mono", Menlo, monospace; }
  h1,h2,h3 { color: #ff9f0a !important; font-family: "SF Mono", Menlo, monospace !important;
             letter-spacing: 1px; }
  /* Bloomberg density: kill streamlit's airy spacing */
  .block-container { padding: 0.3rem 0.8rem 0.4rem 0.8rem !important; max-width: 100% !important; }
  div[data-testid="stVerticalBlock"] { gap: 0.4rem; }
  div[data-testid="stMetric"] { background: #0d1117; border: 1px solid #21262d;
      padding: 4px 10px; border-radius: 2px; }
  div[data-testid="stMetricValue"] { font-size: 1.0rem; color: #e8e6e3;
      font-family: Menlo, monospace; }
  div[data-testid="stMetricLabel"] { color: #8a8f98; font-size: 0.65rem;
      letter-spacing: 1px; }
  .stTabs [data-baseweb="tab-list"] { background: #0d1117; border: 1px solid #21262d;
      gap: 0; padding: 0 4px; }
  .stTabs [data-baseweb="tab"] { color: #8a8f98; font-family: Menlo, monospace;
      font-size: 0.78rem; padding: 4px 14px; letter-spacing: 1px; }
  .stTabs [aria-selected="true"] { color: #000 !important; background: #ff9f0a !important;
      font-weight: 700; }
  thead tr th { background-color: #0d1117 !important; color: #ff9f0a !important; }
  div[data-testid="stExpander"] details { background: #0d1117; border: 1px solid #21262d; }
  div[data-testid="stSelectbox"] * { font-family: Menlo, monospace; font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)

PANEL_BG = "#0d1117"
PANEL_BORDER = "#21262d"


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
            try:
                dlo, dhi = fi.day_low, fi.day_high
            except Exception:
                dlo = dhi = None
            out.append({"t": t, "px": px, "chg": (px / prev - 1) * 100 if prev else 0,
                        "dlo": dlo, "dhi": dhi})
        except Exception:
            out.append({"t": t, "px": None, "chg": 0, "dlo": None, "dhi": None})
    return out


def _range_bar(px, lo, hi, width=44) -> str:
    """Tiny day-range bar: where price sits between day low and high."""
    if not all(isinstance(x, (int, float)) for x in (px, lo, hi)) or hi <= lo:
        return ""
    pos = max(0.0, min(1.0, (px - lo) / (hi - lo)))
    return (f'<span style="display:inline-block; width:{width}px; height:7px; '
            f'background:#21262d; border-radius:1px; position:relative; '
            f'vertical-align:middle; margin-left:5px;">'
            f'<span style="position:absolute; left:{pos*100:.0f}%; top:-1px; '
            f'width:2px; height:9px; background:{AMBER};"></span></span>')


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
    row1 = list(dict.fromkeys(open_tkrs + ["^GSPC", "^NDX", "IWM", "^VIX"]))[:8]
    row2 = ["^TNX", "CL=F", "GC=F", "SI=F", "EURUSD=X", "BTC-USD", "SMH", "TLT"]
    rows_html = []
    for tickers in (row1, row2):
        q = tape_quotes(tuple(tickers))
        cells = []
        for x in q:
            if x["px"] is None:
                continue
            col = GREEN if x["chg"] >= 0 else RED
            arrow = "▲" if x["chg"] >= 0 else "▼"
            cells.append(
                f'<td style="padding:1px 16px 1px 0; white-space:nowrap; '
                f'border-right:1px solid #161b22;">'
                f'<span style="color:{AMBER}; font-weight:700;">{x["t"].replace("=X","").replace("=F","")}</span> '
                f'<span style="color:#e8e6e3;">{x["px"]:,.2f}</span> '
                f'<span style="color:{col}; font-size:11px;">{arrow}{abs(x["chg"]):.2f}%</span>'
                f'{_range_bar(x["px"], x["dlo"], x["dhi"])}</td>')
        rows_html.append(f'<tr>{"".join(cells)}</tr>')
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
        f'padding:4px 10px; border-radius:2px;">'
        f'<table style="font-size:13px;">{"".join(rows_html)}</table>'
        f'<div style="color:{DIM}; font-size:9px; letter-spacing:1px;">'
        f'YFINANCE DELAYED ~15MIN · DAY-RANGE BARS LOW→HIGH · AS OF '
        f'{datetime.now(ET).strftime("%H:%M:%S ET %Y-%m-%d")} · REFRESH 60S</div></div>',
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
                from plotly.subplots import make_subplots
                h = yf.Ticker(tkr).history(period="1y")
                # technicals
                sma20 = h.Close.rolling(20).mean()
                sma50 = h.Close.rolling(50).mean()
                sma200 = h.Close.rolling(200).mean()
                delta = h.Close.diff()
                up = delta.clip(lower=0).ewm(alpha=1/14).mean()
                dn = (-delta.clip(upper=0)).ewm(alpha=1/14).mean()
                rsi = 100 - 100 / (1 + up / dn)
                h6 = h.tail(126); i6 = h6.index   # show 6mo, indicators warmed on 1y

                fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                                    row_heights=[0.62, 0.18, 0.20],
                                    vertical_spacing=0.02)
                fig.add_trace(go.Candlestick(
                    x=i6, open=h6.Open, high=h6.High, low=h6.Low, close=h6.Close,
                    increasing_line_color=GREEN, decreasing_line_color=RED,
                    name=tkr), row=1, col=1)
                for sma, col, nm in ((sma20, "#58a6ff", "SMA20"),
                                     (sma50, AMBER, "SMA50"),
                                     (sma200, "#bc8cff", "SMA200")):
                    fig.add_trace(go.Scatter(x=i6, y=sma.tail(126), name=nm,
                                             line=dict(color=col, width=1)),
                                  row=1, col=1)
                vol_col = [GREEN if c >= o else RED
                           for c, o in zip(h6.Close, h6.Open)]
                fig.add_trace(go.Bar(x=i6, y=h6.Volume, marker_color=vol_col,
                                     opacity=0.55, name="vol"), row=2, col=1)
                fig.add_trace(go.Scatter(x=i6, y=rsi.tail(126), name="RSI14",
                                         line=dict(color="#e8e6e3", width=1)),
                              row=3, col=1)
                for lvl, lc in ((70, RED), (30, GREEN)):
                    fig.add_hline(y=lvl, line_color=lc, line_dash="dot",
                                  line_width=1, row=3, col=1)
                for key, col, lbl in (("target_px", GREEN, "TGT"),
                                      ("stop_px", RED, "STP")):
                    if isinstance(e.get(key), (int, float)):
                        fig.add_hline(y=e[key], line_color=col, line_dash="dot",
                                      annotation_text=f"{lbl} {e[key]}",
                                      annotation_font_color=col, row=1, col=1)
                lo, hi = e.get("entry_px_low"), e.get("entry_px_high")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    fig.add_hrect(y0=lo, y1=hi, fillcolor=AMBER, opacity=0.15,
                                  line_width=0, annotation_text="ENTRY",
                                  annotation_font_color=AMBER, row=1, col=1)
                fig.update_layout(
                    template="plotly_dark", height=430,
                    margin=dict(l=10, r=10, t=22, b=10),
                    paper_bgcolor="#000000", plot_bgcolor=PANEL_BG,
                    xaxis_rangeslider_visible=False,
                    hovermode="x unified",
                    legend=dict(orientation="h", y=1.06, font=dict(size=9)),
                    font=dict(family="Menlo, monospace", size=10))
                fig.update_xaxes(showspikes=True, spikecolor=DIM, spikemode="across",
                                 spikethickness=1, spikedash="dot")
                fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])
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
            items = s.get(key, [])
            if items:
                st.markdown(_heat_table(items), unsafe_allow_html=True)


def _heat_cell(v) -> str:
    """z-score cell with green/red intensity background — the heatmap look."""
    if v is None:
        return f'<td style="text-align:center; color:{DIM};">—</td>'
    a = max(-3.0, min(3.0, float(v)))
    if a >= 0:
        bg = f"rgba(51,209,122,{0.08 + 0.22 * a / 3:.2f})"
    else:
        bg = f"rgba(255,92,87,{0.08 + 0.22 * -a / 3:.2f})"
    return (f'<td style="text-align:center; background:{bg}; color:#e8e6e3; '
            f'padding:2px 8px;">{a:+.1f}</td>')


def _heat_table(items: list[dict]) -> str:
    zkeys = list(items[0]["attribution"].keys())
    head = "".join(f'<th style="padding:3px 8px; color:{AMBER}; text-align:center; '
                   f'border-bottom:1px solid {PANEL_BORDER};">{h}</th>'
                   for h in (["TKR", "PX", "SCORE", "SECTOR", "MOM12%", "R1M%",
                              "%52WH", "RV60", "$VOL(M)"]
                             + [k.split("_")[0].upper() for k in zkeys] + ["", "NEW"]))
    rows = []
    max_score = max(abs(x["score"]) for x in items) or 1
    for x in items:
        bar_w = int(abs(x["score"]) / max_score * 46)
        bar_col = GREEN if x["score"] >= 0 else RED
        score_cell = (f'<td style="padding:2px 8px; white-space:nowrap;">'
                      f'<span style="display:inline-block; width:48px;">{x["score"]:+.2f}</span>'
                      f'<span style="display:inline-block; width:{bar_w}px; height:8px; '
                      f'background:{bar_col}; opacity:0.7; vertical-align:middle;"></span></td>')
        cells = (
            f'<td style="padding:2px 8px; color:{AMBER}; font-weight:700;">{x["ticker"]}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["px"]:,.2f}</td>'
            f'{score_cell}'
            f'<td style="padding:2px 8px; color:{DIM}; font-size:11px;">{x["sector"][:18]}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["raw"]["mom_12_1_pct"]:+.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["raw"]["ret_1m_pct"]:+.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["raw"]["pct_of_52w_high"]:.0f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["raw"]["rv60_ann_pct"]:.0f}</td>'
            f'<td style="padding:2px 8px; text-align:right; color:{DIM};">{x["raw"]["dollar_vol_21d_m"]:,.0f}</td>'
            + "".join(_heat_cell(x["attribution"].get(k)) for k in zkeys)
            + f'<td style="text-align:center;">{"⚡" if x.get("shock") else ""}</td>'
            + f'<td style="text-align:center; color:{GREEN}; font-weight:700;">'
              f'{"NEW" if x.get("new_entrant") else ""}</td>')
        rows.append(f'<tr style="border-bottom:1px solid #161b22;">{cells}</tr>')
    return (f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'border-radius:2px; padding:4px; overflow-x:auto;">'
            f'<table style="font-size:12px; font-family:Menlo,monospace; color:#e8e6e3; '
            f'border-collapse:collapse; width:100%;">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


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
            + f'</div><div style="color:{DIM}; font-size:10px; margin-top:4px;">'
            + " · ".join(val.get("caveats", [])) + "</div></div>",
            unsafe_allow_html=True)
    with st.expander("METHODOLOGY (loop doctrine + lessons log)"):
        st.markdown((REPO / "advisor" / "METHODOLOGY.md").read_text())
    with st.expander("IPS (investment policy)"):
        st.markdown((REPO / "advisor" / "IPS.md").read_text())


# ── LAYOUT ───────────────────────────────────────────────────────────────────

def _regime_chip() -> str:
    s = load_json(RESEARCH / "signals_latest.json") or {}
    reg = (s.get("regime") or {})
    name = (reg.get("name") or "?").upper()
    col = {"RISK_ON": GREEN, "NEUTRAL": AMBER, "STRESS": RED}.get(name, DIM)
    return (f'<span style="color:{col}; border:1px solid {col}; padding:1px 8px; '
            f'border-radius:2px; font-size:11px; font-weight:700;">REGIME {name}'
            f' · VIX {reg.get("vix","?")} · TERM {reg.get("vix_term","?")}</span>')


st.markdown(
    f'<div style="display:flex; justify-content:space-between; align-items:center; '
    f'background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; padding:4px 12px; '
    f'border-radius:2px; margin-bottom:4px;">'
    f'<span style="color:{AMBER}; font-size:19px; font-weight:800; '
    f'letter-spacing:3px; font-family:Menlo,monospace;">ADVISOR TERMINAL'
    f'<span style="color:{DIM}; font-size:10px; letter-spacing:1px;"> '
    f'READ-ONLY RESEARCH CONSOLE · NO EXECUTION PATHS</span></span>'
    f'{_regime_chip()}</div>',
    unsafe_allow_html=True)
tape()

t1, t2, t3, t4, t5 = st.tabs(
    ["RSCH ▸ RESEARCH", "CALL ▸ OPEN CALLS", "FCTR ▸ FACTOR SHEETS",
     "PORT ▸ PORTFOLIO", "SCOR ▸ SCORECARD"])
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


# ── STATUS FOOTER (service health + data ages) ───────────────────────────────

@st.cache_data(ttl=60)
def _service_status() -> list[tuple]:
    import subprocess
    out = []
    try:
        listing = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=5).stdout
        for svc, label in (("advisor-approvals", "APPROVALS"),
                           ("advisor-exitwatch", "EXITWATCH"),
                           ("advisor-terminal", "TERMINAL"),
                           ("advisor-brief", "BRIEF.SCHED"),
                           ("advisor-research", "RSCH.SCHED")):
            line = [l for l in listing.splitlines() if svc in l]
            if not line:
                out.append((label, "MISSING", RED))
            elif line[0].split()[0] != "-":
                out.append((label, "LIVE", GREEN))
            else:
                out.append((label, "SCHED", AMBER))
    except Exception:
        out.append(("LAUNCHCTL", "ERR", RED))
    return out


def status_footer():
    cells = "".join(
        f'<span style="margin-right:14px;"><span style="color:{DIM};">{n}</span> '
        f'<span style="color:{c}; font-weight:700;">●{s}</span></span>'
        for n, s, c in _service_status())
    meta = load_json(RESEARCH / "panels" / "meta.json") or {}
    import time as _t
    panel_age = (_t.time() - meta.get("built_unix", 0)) / 3600 if meta else None
    sig = load_json(RESEARCH / "signals_latest.json") or {}
    ages = (f'PANEL {panel_age:.1f}H' if panel_age and panel_age < 1e4 else 'PANEL —')
    ages += f' · SIGNALS {(sig.get("as_of") or "—")[:16]}'
    n_open = sum(1 for e in journal_effective().values() if e.get("status") == "open")
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
        f'padding:3px 12px; border-radius:2px; margin-top:6px; font-size:10px; '
        f'font-family:Menlo,monospace; display:flex; justify-content:space-between;">'
        f'<span>{cells}</span>'
        f'<span style="color:{DIM};">{ages} · OPEN CALLS {n_open} · '
        f'{datetime.now(ET).strftime("%a %H:%M ET")}</span></div>',
        unsafe_allow_html=True)


status_footer()
