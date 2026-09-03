"""ADVISOR TERMINAL — Bloomberg-style research console (read-only).

Renders the advisor system's exhaust: live tape (quote-daemon store: IBKR
live where entitled, delayed elsewhere, always labeled), research feed with
evidence + timestamps, candidate slate + watchlist, dossier browser with
kill lists, catalyst calendar, trade-plan charts, factor sheets, portfolio/
risk + alert center, scorecard with calibration/attribution/validation.
NO order paths — Tier-0 guarantee (REGEN spawns the research pipeline only,
behind a single-flight lock; optional token gate via ADVISOR_PORTAL_TOKEN).

Run:  streamlit run advisor/terminal.py --server.port 8505
launchd: com.stockstest.advisor-terminal
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from advisor.render_safety import http_url as safe_http_url
from advisor.render_safety import secret_equal
from advisor.render_safety import text as esc
from advisor.research.datastore import current_meta as current_panel_meta
from advisor.production_status import assess as production_assess
from advisor.brief_control import arm_remaining_s, request_generation
from advisor.brief_control import status as brief_control_status
from advisor.actionability import is_actionable

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
CTX = DATA / "context"
RESEARCH = DATA / "research"
KNOW = DATA / "knowledge"

AMBER = "#ff9f0a"
GREEN = "#33d17a"
RED = "#ff5c57"
DIM = "#8a8f98"

st.set_page_config(page_title="ADVISOR TERMINAL", layout="wide",
                   initial_sidebar_state="collapsed")

# optional token gate (TUNING_NOTES: posture decision) — set
# ADVISOR_PORTAL_TOKEN in the service env to require ?token=... in the URL
_TOKEN = os.environ.get("ADVISOR_PORTAL_TOKEN", "")
if _TOKEN and not secret_equal(st.query_params.get("token"), _TOKEN):
    st.markdown("<h3 style='color:#ff9f0a;font-family:Menlo,monospace;'>"
                "ADVISOR TERMINAL — locked</h3>"
                "<p style='color:#8a8f98;font-family:Menlo,monospace;'>append "
                "?token=… to the URL.</p>", unsafe_allow_html=True)
    st.stop()

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
  div[data-testid="stSelectbox"] *, div[data-testid="stTextInput"] input
      { font-family: Menlo, monospace; font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)

PANEL_BG = "#0d1117"
PANEL_BORDER = "#21262d"


# ── helpers ──────────────────────────────────────────────────────────────────

def chip(text: str, color: str = DIM) -> str:
    text = esc(text)
    return (f'<span style="border:1px solid {color}; color:{color}; '
            f'border-radius:3px; padding:0px 6px; font-size:11px; '
            f'font-family:Menlo,monospace; margin-right:6px;">{text}</span>')


def panel_header(title: str, sub: str = "") -> None:
    st.markdown(
        f'<div style="border-bottom:1px solid #2a2f36; margin:4px 0 10px 0;">'
        f'<span style="color:{AMBER}; font-family:Menlo,monospace; font-size:15px; '
        f'font-weight:700; letter-spacing:2px;">{esc(title)}</span>'
        f'<span style="color:{DIM}; font-size:11px; margin-left:12px;">{esc(sub)}</span></div>',
        unsafe_allow_html=True)


def load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def load_jsonl_tail(p: Path, n: int = 20) -> list[dict]:
    try:
        rows = []
        for line in p.read_text().splitlines()[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[::-1]
    except Exception:
        return []


def safe_markdown(text: str) -> str:
    """Remove model-control comments and prevent dollar math mangling."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text.replace("$", r"\$").strip()


def manual_reload() -> None:
    """Clear caches and force a full app rerun, even from inside a fragment."""
    st.cache_data.clear()
    st.session_state["advisor_last_manual_reload"] = datetime.now(ET).isoformat()
    st.rerun(scope="app")


def journal_effective() -> dict:
    p = DATA / "decision_journal.jsonl"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    machine = ("stamp", "resolve_pending")
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = e.get("id", "?")
        if eid in out and e.get("type") in machine:
            upd = {k: v for k, v in e.items() if k not in ("id", "ts", "type", "status")}
            if e.get("type") == "resolve_pending":
                upd["resolve_pending"] = True
            out[eid] = {**out[eid], **upd}
        else:
            out[eid] = {**out.get(eid, {}), **e}
    return out


def quote_store() -> dict:
    """Quote-daemon snapshot (zero network). {} when missing/stale >120s."""
    snap = load_json(DATA / "quotes" / "latest.json")
    if not snap:
        return {}
    try:
        age = (datetime.now(ET) - datetime.fromisoformat(snap["as_of"])).total_seconds()
        if age > 120:
            return {}
        snap["age_s"] = age
        return snap
    except Exception:
        return {}


@st.cache_data(ttl=55)
def yf_quotes(tickers: tuple) -> dict[str, dict]:
    """yfinance fallback for symbols the daemon doesn't serve."""
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            fi = yf.Ticker(t).fast_info
            px = fi.last_price
            prev = fi.previous_close
            try:
                dlo, dhi = fi.day_low, fi.day_high
            except Exception:
                dlo = dhi = None
            out[t] = {"px": px, "prev_close": prev, "day_low": dlo,
                      "day_high": dhi, "src": "yfinance ~15min", "type": "delayed"}
        except Exception:
            continue
    return out


def get_quotes(tickers: list[str]) -> dict[str, dict]:
    """Merged view: quote store first, yfinance for the rest."""
    snap = quote_store()
    out = {}
    missing = []
    for t in tickers:
        q = (snap.get("quotes") or {}).get(t)
        if q:
            out[t] = q
        else:
            missing.append(t)
    if missing:
        out.update(yf_quotes(tuple(missing)))
    return out


@st.cache_data(ttl=900)
def chart_history(ticker: str):
    """1y OHLCV for trade-plan charts — cached so tab clicks stop re-downloading."""
    import yfinance as yf
    return yf.Ticker(ticker).history(period="1y")


def _range_bar(px, lo, hi, width=44) -> str:
    if not all(isinstance(x, (int, float)) for x in (px, lo, hi)) or hi <= lo:
        return ""
    pos = max(0.0, min(1.0, (px - lo) / (hi - lo)))
    return (f'<span style="display:inline-block; width:{width}px; height:7px; '
            f'background:#21262d; border-radius:1px; position:relative; '
            f'vertical-align:middle; margin-left:5px;">'
            f'<span style="position:absolute; left:{pos*100:.0f}%; top:-1px; '
            f'width:2px; height:9px; background:{AMBER};"></span></span>')


# ── TAPE ─────────────────────────────────────────────────────────────────────

@st.fragment(run_every="20s")
def tape():
    calls = journal_effective()
    open_tkrs = [e.get("yf_ticker") for e in calls.values()
                 if e.get("status") == "open" and e.get("yf_ticker")]
    row1 = list(dict.fromkeys(open_tkrs + ["^GSPC", "^NDX", "IWM", "^VIX"]))[:8]
    row2 = ["^TNX", "CL=F", "GC=F", "SI=F", "EURUSD=X", "BTC-USD", "SMH", "TLT"]
    quotes = get_quotes(list(dict.fromkeys(row1 + row2)))
    snap = quote_store()
    rows_html = []
    n_live = 0
    for tickers in (row1, row2):
        cells = []
        for t in tickers:
            q = quotes.get(t)
            if not q or not isinstance(q.get("px"), (int, float)):
                continue
            px = q["px"]
            prev = q.get("prev_close")
            chg = (px / prev - 1) * 100 if prev else 0.0
            col = GREEN if chg >= 0 else RED
            arrow = "▲" if chg >= 0 else "▼"
            live = q.get("type") == "live"
            n_live += 1 if live else 0
            dot = (f'<span style="color:{GREEN}; font-size:8px;">●</span>'
                   if live else "")
            cells.append(
                f'<td style="padding:1px 16px 1px 0; white-space:nowrap; '
                f'border-right:1px solid #161b22;">'
                f'<span style="color:{AMBER}; font-weight:700;">'
                f'{esc(t.replace("=X", "").replace("=F", ""))}</span>{dot} '
                f'<span style="color:#e8e6e3;">{px:,.2f}</span> '
                f'<span style="color:{col}; font-size:11px;">{arrow}{abs(chg):.2f}%</span>'
                f'{_range_bar(px, q.get("day_low"), q.get("day_high"))}</td>')
        rows_html.append(f'<tr>{"".join(cells)}</tr>')
    src_note = (f'QUOTE DAEMON {snap.get("age_s", 0):.0f}S OLD · '
                f'{n_live} LIVE (IBKR) · REST DELAYED ~15MIN'
                if snap else 'DAEMON DOWN — YFINANCE DELAYED ~15MIN')
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
        f'padding:4px 10px; border-radius:2px;">'
        f'<table style="font-size:13px;">{"".join(rows_html)}</table>'
        f'<div style="color:{DIM}; font-size:9px; letter-spacing:1px;">'
        f'{src_note} · ●=LIVE · AS OF '
        f'{datetime.now(ET).strftime("%H:%M:%S ET %Y-%m-%d")} · REFRESH 20S</div></div>',
        unsafe_allow_html=True)


# ── ACTIVE RECOMMENDATIONS (pinned board — union of still-valid calls) ──────

def _brief_missing_banner() -> str | None:
    """Unmissable red banner when today's brief hasn't landed by 09:45 on a
    weekday (2026-07-16 audit: 8 of 10 pipeline days failed silently while
    the footer showed a small amber dot nobody noticed)."""
    now = datetime.now(ET)
    if now.weekday() > 4 or now.strftime("%H:%M") < "09:45":
        return None
    today = now.date().isoformat()
    if (CTX / today / "brief.json").exists():
        return None
    reason = "no pipeline heartbeat today"
    rows = load_jsonl_tail(REPO / "advisor" / "logs" / "pipeline_runs.jsonl", 8)
    for r in rows:
        if r.get("ts", "").startswith(today):
            reason = f"last stage: {r.get('stage')} rc={r.get('rc')} {r.get('note', '')[:60]}"
            break
    else:
        try:
            for line in reversed((REPO / "advisor" / "logs" / "brief_runs.log")
                                 .read_text().splitlines()[-8:]):
                if today[5:] in line or "ABORT" in line:
                    reason = line.strip()[:110]
                    break
        except Exception:
            pass
    return (f'<div style="background:#2a0d0d; border:2px solid {RED}; '
            f'padding:10px 14px; border-radius:3px; margin:4px 0;">'
            f'<span style="color:{RED}; font-family:Menlo,monospace; '
            f'font-size:15px; font-weight:800; letter-spacing:2px;">'
            f'⛔ NO BRIEF TODAY ({today})</span>'
            f'<span style="color:#e8e6e3; font-size:12px; margin-left:12px; '
            f'font-family:Menlo,monospace;">{esc(reason)}</span>'
            f'<div style="color:{DIM}; font-size:11px; margin-top:4px;">'
            f'watchdog self-heals after 09:00 · or use GENERATE NEW BRIEF above · '
            f'root cause: dark-wake TCC denies the 08:15 calendar job '
            f'(see TUNING_NOTES)</div></div>')


@st.fragment(run_every="30s")
def active_recommendations():
    """The standing set: every call that still carries conviction, from ANY
    brief. A card leaves the board the moment its call stops being a
    suggestion: time-stop passed (hidden), level hit (shows as an observed
    research outcome until resolved), withdrawn/resolved (gone)."""
    today = datetime.now(ET).date().isoformat()
    cards = []
    open_views = {k: v for k, v in journal_effective().items()
                  if v.get("type") == "view" and v.get("status") == "open"}
    tkrs = sorted({v.get("yf_ticker") for v in open_views.values()
                   if v.get("yf_ticker")})
    quotes = get_quotes(tkrs) if tkrs else {}

    for eid, e in open_views.items():
        tstop = e.get("time_stop") or "9999"
        if tstop < today and not e.get("resolve_pending"):
            continue        # conviction expired — post-mortem will resolve it
        tkr = e.get("yf_ticker", "")
        q = quotes.get(tkr) or {}
        px = q.get("px")
        long_ = (e.get("direction") or "long").lower() != "short"
        conv = (e.get("conviction") or "?").upper()
        actionable = is_actionable(e)

        if e.get("resolve_pending"):
            hit = e.get("hit_level")
            color = RED if hit == "stop" else GREEN
            state = (("🛑 ACTIONABLE-IDEA STOP REACHED" if hit == "stop"
                      else "🎯 ACTIONABLE-IDEA TARGET REACHED") if actionable else
                     ("🛑 RESEARCH VIEW INVALIDATED — stop observed" if hit == "stop"
                      else "🎯 RESEARCH TARGET OBSERVED"))
            detail = (f'hit {e.get("hit_px")} at {str(e.get("hit_ts"))[:16]} — '
                      f'no position or execution is inferred')
        else:
            color = GREEN if conv == "HIGH" else AMBER
            lo, hi = e.get("entry_px_low"), e.get("entry_px_high")
            sp, tp = e.get("stop_px"), e.get("target_px")
            if isinstance(px, (int, float)) and isinstance(lo, (int, float)) \
                    and isinstance(hi, (int, float)) and not (lo <= px <= hi) \
                    and ((px > hi) if long_ else (px < lo)):
                state = f"⏳ WAIT — outside entry zone {lo}–{hi}"
                detail = f"px {px:,.2f} ({q.get('src', 'no quote')})"
            elif isinstance(px, (int, float)) and isinstance(lo, (int, float)) \
                    and isinstance(hi, (int, float)) and lo <= px <= hi:
                state = (f"🟢 ACTIONABLE IDEA — in entry zone {lo}–{hi}"
                         if actionable else
                         f"🟡 RESEARCH ENTRY ZONE — not a buy/sell signal · {lo}–{hi}")
                detail = f"px {px:,.2f} ({q.get('src', '')})"
            elif all(isinstance(x, (int, float)) for x in (px, sp, tp)) and sp != tp:
                prog = max(0.0, min(1.0, (px - sp) / (tp - sp)))
                state = f"▶ IN PLAY — {prog * 100:.0f}% of stop→target"
                detail = f"px {px:,.2f} ({q.get('src', '')})"
            else:
                state = "▶ STANDING"
                detail = f"px {px:,.2f}" if isinstance(px, (int, float)) else "no fresh quote"
        cards.append(
            f'<div style="border:1px solid #2a2f36; border-left:4px solid {color}; '
            f'background:#11151a; padding:8px 12px; margin-bottom:5px; border-radius:3px;">'
            f'<span style="color:{color}; font-weight:700;">'
            f'{esc(e.get("instrument", "?"))} — {"LONG" if long_ else "SHORT"} — {esc(conv)}'
            f'{" · p" + esc(e.get("p_win")) if e.get("p_win") else ""}</span> '
            f'<span style="color:#e8e6e3; font-size:12px; margin-left:8px;">{esc(state)}</span> '
            f'<span style="color:{DIM}; font-size:11px;">{esc(detail)}</span><br>'
            f'<span style="color:#c9c7c2; font-size:12px;">'
            f'{esc((e.get("thesis") or "")[:150])}</span><br>'
            f'{chip("ENTRY " + str(e.get("entry", "—")), "#e8e6e3")}'
            f'{chip("TGT " + str(e.get("target", "—")), GREEN)}'
            f'{chip("STOP " + str(e.get("stop", "—")), RED)}'
            f'{chip("T-STOP " + str(e.get("time_stop", "—")), DIM)}'
            f'{chip("since " + str(e.get("ts", ""))[:10], DIM)}'
            f'{chip(eid, DIM)}</div>')

    # watchlist entries whose trigger just fired = actionable re-entries
    try:
        from advisor.watchlist import load as wl_load
        for t, w in wl_load().items():
            if w.get("state") == "watchlist" and w.get("triggered"):
                trg = w.get("trigger") or {}
                cards.append(
                    f'<div style="border:1px solid #2a2f36; border-left:4px solid '
                    f'{AMBER}; background:#11151a; padding:8px 12px; '
                    f'margin-bottom:5px; border-radius:3px;">'
                    f'<span style="color:{AMBER}; font-weight:700;">{esc(t)} — '
                    f'⚡ RESEARCH REVISIT TRIGGERED</span> '
                    f'<span style="color:#e8e6e3; font-size:12px;">crossed '
                    f'{esc(trg.get("dir"))} {esc(trg.get("px"))} at '
                    f'{esc(str(w["triggered"].get("ts"))[:16])}</span><br>'
                    f'<span style="color:#c9c7c2; font-size:12px;">'
                    f'{esc(w.get("note", "")[:150])} — parked by an earlier brief; '
                    f'the morning session re-underwrites it before any action.'
                    f'</span></div>')
    except Exception:
        pass

    banner = _brief_missing_banner()
    if banner:
        st.markdown(banner, unsafe_allow_html=True)
    n = len(cards)
    hdr = (f'<span style="color:{AMBER}; font-family:Menlo,monospace; '
           f'font-size:13px; font-weight:700; letter-spacing:2px;">'
           f'ACTIVE RESEARCH VIEWS ({n})</span>'
           f'<span style="color:{DIM}; font-size:10px; margin-left:10px;">'
           f'union of still-valid calls from all briefs · auto-clears when a '
           f'call stops being suggested · 30s refresh</span>')
    if not cards:
        body = (f'<div style="color:{DIM}; font-size:12px; padding:6px 2px;">'
                f'none — no standing research views; no position is inferred.</div>')
    else:
        body = "".join(cards)
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {AMBER}; '
        f'padding:6px 10px; border-radius:2px; margin:4px 0;">'
        f'{hdr}{body}</div>', unsafe_allow_html=True)


# ── RESEARCH FEED ────────────────────────────────────────────────────────────

def research_feed():
    days = sorted([d.name for d in CTX.iterdir() if d.is_dir()], reverse=True) \
        if CTX.exists() else []
    if not days:
        st.info("No briefs yet.")
        return
    day = st.selectbox("brief date", days, index=0, label_visibility="collapsed")
    brief = CTX / day / "brief.md"
    bj = load_json(CTX / day / "brief.json")  # structured (newer briefs)
    macro = load_json(CTX / day / "macro.json")
    themes_p = KNOW / "narrative" / "current_themes.md"
    panel_header("RESEARCH FEED", f"{day} · macro → synthesis → red-team → publish")
    if bj:
        flags = []
        if bj.get("redteam") == "missing":
            flags.append(chip("⚠ UNREDTEAMED", RED))
        if bj.get("narrative_delta"):
            flags.append(chip(f"Δ {bj['narrative_delta'][:80]}", AMBER))
        if flags:
            st.markdown("".join(flags), unsafe_allow_html=True)
    if bj and bj.get("views"):
        for v in bj["views"]:
            _view_card(v)
    if bj and bj.get("rejected"):
        rows = "".join(
            f'<div style="color:#c9c7c2; font-size:12px; margin:2px 0;">'
            f'✕ <span style="color:{RED};">{esc(r.get("idea", "?"))}</span> — '
            f'{esc(r.get("killed_by", ""))}</div>' for r in bj["rejected"])
        st.markdown(
            f'<div style="border:1px solid #2a2f36; border-left:3px solid {RED}; '
            f'background:#11151a; padding:10px; margin-bottom:10px; border-radius:4px;">'
            f'<span style="color:{RED}; font-weight:700; font-size:12px;">KILLED IDEAS '
            f'(the bar exists)</span>{rows}</div>', unsafe_allow_html=True)
    if macro and macro.get("calendar"):
        cal = " · ".join(f"{c.get('time_et', '')} {c.get('event', '')}"
                         for c in macro["calendar"][:6])
        st.markdown(chip(f"today: {cal}", DIM), unsafe_allow_html=True)
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
    if themes_p.exists():
        with st.expander("CURRENT THEMES (rolling market memory — macro stage maintains)"):
            st.markdown(safe_markdown(themes_p.read_text()))


def _view_card(v: dict):
    conv = (v.get("conviction") or "?").upper()
    cc = GREEN if conv == "HIGH" else AMBER
    pw = v.get("p_win")
    src = v.get("source")
    rows = ""
    for ev in v.get("evidence", []):
        url = safe_http_url(ev.get("url"))
        link = (f' <a href="{url}" target="_self" rel="noopener noreferrer" '
                f'style="color:{AMBER};">[src]</a>' if url else "")
        ts = chip(ev.get("retrieved", ""), DIM) if ev.get("retrieved") else ""
        rows += (f'<div style="color:#c9c7c2; font-size:12px; margin:2px 0;">'
                 f'• {esc(ev.get("claim", ""))}{link} {ts}</div>')
    meta = ""
    if pw:
        calibrated = bool((v.get("probability_basis") or {}).get("calibrated"))
        meta += chip(f"p_win {pw} {'CAL' if calibrated else 'UNCAL'}",
                     cc if calibrated else AMBER)
    meta += chip(str(v.get("recommendation_class", "LEGACY")).upper(),
                 GREEN if v.get("recommendation_class") == "actionable_idea" else AMBER)
    if src:
        meta += chip(f"src {src}", DIM)
    if v.get("sizing"):
        meta += chip(str(v["sizing"])[:40], DIM)
    st.markdown(
        f'<div style="border:1px solid #2a2f36; border-left:3px solid {cc}; '
        f'background:#11151a; padding:12px; margin-bottom:10px; border-radius:4px;">'
        f'<span style="color:{cc}; font-weight:700;">{esc(v.get("instrument", "?"))} — '
        f'{esc(v.get("direction", ""))} — {esc(conv)}</span> {meta}<br>'
        f'<span style="color:#e8e6e3; font-size:13px;">{esc(v.get("thesis", ""))}</span>'
        f'{rows}'
        f'<div style="margin-top:6px;">{chip("ENTRY " + str(v.get("entry", "—")), "#e8e6e3")}'
        f'{chip("TARGET " + str(v.get("target", "—")), GREEN)}'
        f'{chip("STOP " + str(v.get("stop", "—")), RED)}'
        f'{chip("TIME " + str(v.get("time_stop", "—")), DIM)}</div></div>',
        unsafe_allow_html=True)


# ── OPEN CALLS + CHARTS ──────────────────────────────────────────────────────

def open_calls_and_charts():
    panel_header("OPEN CALLS · TRADE PLANS", "journal + exit-watcher levels, live chart bands")
    calls = {k: v for k, v in journal_effective().items() if v.get("status") == "open"
             and v.get("type") == "view"}
    if not calls:
        st.info("No open calls.")
        return
    import plotly.graph_objects as go
    for eid, e in calls.items():
        tkr = e.get("yf_ticker")
        cols = st.columns([2, 3])
        with cols[0]:
            _view_card({**e, "evidence": []})
            flags = chip(f"journaled {e.get('ts', '')[:16]}", DIM) + chip(f"id {eid}", DIM)
            if e.get("ref_px"):
                flags += chip(f"ref {e['ref_px']}", DIM)
            if e.get("resolve_pending"):
                flags += chip(f"⚡ {e.get('hit_level')} hit {str(e.get('hit_ts'))[:16]}", RED)
            st.markdown(flags, unsafe_allow_html=True)
        with cols[1]:
            if not tkr:
                continue
            try:
                from plotly.subplots import make_subplots
                h = chart_history(tkr)
                sma20 = h.Close.rolling(20).mean()
                sma50 = h.Close.rolling(50).mean()
                sma200 = h.Close.rolling(200).mean()
                delta = h.Close.diff()
                up = delta.clip(lower=0).ewm(alpha=1 / 14).mean()
                dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14).mean()
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
                st.plotly_chart(fig, width="stretch",
                                key=f"chart_{eid}")
            except Exception as exc:
                st.warning(f"{tkr}: chart unavailable ({exc})")


# ── IDEAS · SLATE · WATCHLIST ────────────────────────────────────────────────

BUCKET_COLORS = {"tactical_long": GREEN, "tactical_short": RED,
                 "pead_fresh": AMBER, "insider_cluster": "#58a6ff",
                 "revision_leader": "#bc8cff", "cheap_quality": "#33d1c9",
                 "new_entrant": GREEN, "squeeze_flag": RED}


def ideas_tab():
    slate = load_json(RESEARCH / "candidates_latest.json")
    panel_header("CANDIDATE SLATE",
                 (f"{slate['as_of'][:16]} · {slate['n']} names · confluence: "
                  + (", ".join(slate["confluence"]) or "none"))
                 if slate else "nightly generators haven't run yet")
    if slate:
        fscope = (slate.get("generator_scope") or {}).get("revision_leader") or {}
        st.markdown(chip(
            f"RANK SCOPE {fscope.get('ranking_scope', 'mixed labeled subsets')} · "
            "candidate discovery, not market-wide coverage", RED),
            unsafe_allow_html=True)
        rows = []
        for e in slate["slate"]:
            bchips = "".join(chip(b, BUCKET_COLORS.get(b, DIM)) for b in e["buckets"])
            d = e.get("detail", {})
            det = " ".join(f'{k}={v}' for k, v in list(d.items())[:5])
            rows.append(
                f'<tr style="border-bottom:1px solid #161b22;">'
                f'<td style="padding:3px 10px; color:{AMBER}; font-weight:700;">'
                f'{esc(e["ticker"])}{" ★" if len(e["buckets"]) >= 2 else ""}</td>'
                f'<td style="padding:3px 6px;">{bchips}</td>'
                f'<td style="padding:3px 10px; color:{DIM}; font-size:11px;">{esc(det)}</td>'
                f'<td style="padding:3px 10px; color:{"#e8e6e3" if d.get("next_earnings") else DIM}; '
                f'font-size:11px;">{esc(d.get("next_earnings", "—"))}</td>'
                f'<td style="padding:3px 10px; text-align:center;">'
                f'{"📁" if e.get("has_dossier") else ""}</td></tr>')
        st.markdown(
            f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'border-radius:2px; padding:4px; overflow-x:auto;">'
            f'<table style="font-size:12px; font-family:Menlo,monospace; '
            f'color:#e8e6e3; border-collapse:collapse; width:100%;">'
            f'<thead><tr>' + "".join(
                f'<th style="padding:3px 10px; color:{AMBER}; text-align:left; '
                f'border-bottom:1px solid {PANEL_BORDER};">{h}</th>'
                for h in ("TKR", "GENERATORS", "DETAIL", "NEXT EPS (PROVIDER EST)", "DOSSIER"))
            + f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>',
            unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    panel_header("WATCHLIST", "state machine — ⚡ triggered entries are the warmest leads")
    try:
        from advisor.watchlist import load as wl_load
        wl = wl_load()
    except Exception:
        wl = {}
    active = {t: e for t, e in wl.items() if e.get("state") != "dormant"}
    if not active:
        st.info("Watchlist empty — publish parks 'right idea, wrong price' here.")
    else:
        state_col = {"watchlist": AMBER, "active_view": GREEN,
                     "researched": "#58a6ff", "candidate": DIM, "resolved": DIM}
        rows = []
        for t, e in sorted(active.items(),
                           key=lambda kv: (not kv[1].get("triggered"),
                                           kv[1].get("state") != "watchlist")):
            trg = e.get("trigger")
            trg_s = f'{trg["dir"]} {trg["px"]}' if trg else "—"
            fired = e.get("triggered")
            rows.append(
                f'<tr style="border-bottom:1px solid #161b22;">'
                f'<td style="padding:3px 10px; color:{AMBER}; font-weight:700;">{esc(t)}</td>'
                f'<td style="padding:3px 10px;">{chip(e.get("state", "?"), state_col.get(e.get("state"), DIM))}</td>'
                f'<td style="padding:3px 10px; color:#e8e6e3; font-size:11px;">{esc(trg_s)}</td>'
                f'<td style="padding:3px 10px; color:{RED}; font-size:11px;">'
                f'{esc("⚡ " + str(fired.get("px")) + " @ " + str(fired.get("ts"))[:16]) if fired else ""}</td>'
                f'<td style="padding:3px 10px; color:{DIM}; font-size:11px;">exp {esc(e.get("expires", "—"))}</td>'
                f'<td style="padding:3px 10px; color:{DIM}; font-size:11px;">{esc(e.get("note", "")[:60])}</td></tr>')
        st.markdown(
            f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'border-radius:2px; padding:4px;">'
            f'<table style="font-size:12px; font-family:Menlo,monospace; color:#e8e6e3; '
            f'border-collapse:collapse; width:100%;"><tbody>{"".join(rows)}</tbody></table></div>',
            unsafe_allow_html=True)

    clusters = load_json(RESEARCH / "positioning" / "insider_clusters.json")
    if clusters and clusters.get("clusters"):
        st.markdown("<br>", unsafe_allow_html=True)
        panel_header("INSIDER BUY CLUSTERS",
                      f"{clusters['as_of'][:16]} · ≥2 distinct insiders net buying, "
                      f"{clusters['window_days']}d window · candidate source, not a factor")
        for c in clusters["clusters"][:8]:
            st.markdown(
                chip(c["ticker"], AMBER)
                + chip(f"{c['n_buys']} buys / {c['n_sells']} sells", GREEN)
                + chip(f"net ${c['net_value_usd']:,.0f}", GREEN)
                + chip(", ".join(c.get("buyers_seen", [])[:3]), DIM),
                unsafe_allow_html=True)


# ── DOSSIER BROWSER ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _peek_cached(ticker: str) -> dict:
    from advisor.research.peek import peek
    return peek(ticker)


def dossier_tab():
    dossiers = sorted([p.name for p in (KNOW / "dossiers").iterdir()
                       if p.is_dir()]) if (KNOW / "dossiers").exists() else []
    panel_header("DOSSIERS", f"{len(dossiers)} names researched · facts persist, opinions re-earn")
    c1, c2 = st.columns([1, 1])
    with c1:
        pick = st.selectbox("dossier", ["—"] + dossiers, index=0,
                            label_visibility="collapsed")
    with c2:
        lookup = st.text_input("any ticker (PIT snapshot readout)",
                               placeholder="ANY TICKER — e.g. FISV",
                               label_visibility="collapsed").strip().upper()

    if lookup:
        try:
            p = _peek_cached(lookup)
            st.markdown(chip(f"{lookup} — {p.get('src', '')}", AMBER),
                        unsafe_allow_html=True)
            cols = st.columns(4)
            secs = [("POSITIONING", p.get("positioning")),
                    ("VALUATION", p.get("valuation")),
                    ("ESTIMATE MOMENTUM", p.get("estimate_momentum")),
                    ("ANALYST", p.get("analyst"))]
            for col, (name, d) in zip(cols, secs):
                with col:
                    body = "".join(f'<div style="color:#c9c7c2; font-size:11px;">'
                                   f'{esc(k)}: <span style="color:#e8e6e3;">{esc(v)}</span></div>'
                                   for k, v in (d or {}).items()) or \
                        f'<div style="color:{DIM}; font-size:11px;">no data yet</div>'
                    st.markdown(
                        f'<div style="background:{PANEL_BG}; border:1px solid '
                        f'{PANEL_BORDER}; padding:8px; border-radius:2px;">'
                        f'<div style="color:{AMBER}; font-size:10px; '
                        f'letter-spacing:1px;">{name}</div>{body}</div>',
                        unsafe_allow_html=True)
            extra = ""
            if p.get("next_earnings"):
                extra += chip(f"next EPS {p['next_earnings'].get('next_earnings', '?')}", AMBER)
            if p.get("insider_cluster"):
                extra += chip(f"⚡ insider cluster: {p['insider_cluster']['n_buys']} buys "
                              f"net ${p['insider_cluster']['net_value_usd']:,.0f}", GREEN)
            if p.get("last_surprises"):
                extra += chip("surprises: " + " ".join(
                    f"{s['date'][:7]}:{s['surprise_pct']}%"
                    for s in p["last_surprises"]), DIM)
            if extra:
                st.markdown(extra, unsafe_allow_html=True)
        except Exception as exc:
            st.warning(f"peek failed: {exc}")

    if pick != "—":
        d = KNOW / "dossiers" / pick
        meta = load_json(d / "meta.json") or {}
        facts = load_json(d / "facts.json") or {}
        stale_days = None
        try:
            stale_days = (datetime.now(ET)
                          - datetime.fromisoformat(meta.get("facts_refreshed"))).days
        except Exception:
            pass
        flags = chip(f"state {meta.get('state', '?')}", "#58a6ff")
        flags += chip(f"facts {str(meta.get('facts_refreshed', '?'))[:16]}",
                      RED if (stale_days or 0) > 5 else DIM)
        flags += chip(f"narrative {str(meta.get('narrative_updated', '—'))[:16]}", DIM)
        st.markdown(flags, unsafe_allow_html=True)
        kc1, kc2 = st.columns([1, 2])
        with kc1:
            kn = facts.get("key_numbers", {})
            pos = facts.get("positioning", {})
            body = "".join(f'<div style="color:#c9c7c2; font-size:11px;">{esc(k)}: '
                           f'<span style="color:#e8e6e3;">{esc(v)}</span></div>'
                           for k, v in list(kn.items())[:12] if v is not None)
            ins = pos.get("insiders_90d") or {}
            if ins:
                body += (f'<div style="color:{AMBER}; font-size:11px; margin-top:4px;">'
                         f'insiders 90d: {ins.get("n_buys", 0)}B/{ins.get("n_sells", 0)}S '
                         f'net ${ins.get("net_value_usd", 0):,.0f}</div>')
            st.markdown(
                f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
                f'padding:10px; border-radius:2px;">'
                f'<div style="color:{AMBER}; font-size:10px; letter-spacing:1px;">'
                f'FACTS ({esc(str(facts.get("as_of", "?"))[:10])})</div>{body}</div>',
                unsafe_allow_html=True)
        with kc2:
            narrative = d / "narrative.md"
            if narrative.exists():
                txt = narrative.read_text()
                if txt.strip().startswith("⚡"):
                    st.markdown(chip("⚡ REVISIT TRIGGERED", RED), unsafe_allow_html=True)
                st.markdown(
                    f'<div style="background:#11151a; border:1px solid #2a2f36; '
                    f'padding:12px; border-radius:4px; max-height:520px; overflow-y:auto; '
                    f'color:#c9c7c2; font-size:12.5px;">\n\n', unsafe_allow_html=True)
                st.markdown(txt)
                st.markdown('</div>', unsafe_allow_html=True)


# ── CALENDAR ─────────────────────────────────────────────────────────────────

def calendar_tab():
    days = sorted([d.name for d in CTX.iterdir() if d.is_dir()], reverse=True) \
        if CTX.exists() else []
    cal = load_json(CTX / days[0] / "calendar.json") if days else None
    macro = load_json(CTX / days[0] / "macro.json") if days else None
    panel_header("CATALYST CALENDAR",
                 f"{cal['as_of'][:16]} · {cal['n_names_tracked']} names tracked · {cal['src']}"
                 if cal else "no calendar built yet (next 08:15 run)")
    if macro and macro.get("calendar"):
        st.markdown("".join(chip(f"{c.get('time_et', '')} {c.get('event', '')}"
                                 + (f" (cons {c['consensus']})" if c.get("consensus") else ""),
                                 AMBER) for c in macro["calendar"][:8]),
                    unsafe_allow_html=True)
    events = (cal or {}).get("events", [])
    if not events:
        st.info("No equity catalysts in the 14-day window for tracked names.")
        return
    by_date: dict[str, list] = {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)
    for d, evs in sorted(by_date.items()):
        weekday = datetime.fromisoformat(d).strftime("%a")
        rows = "".join(
            f'<span style="margin-right:14px;">'
            f'<span style="color:{AMBER}; font-weight:700;">{esc(e["ticker"])}</span> '
            f'<span style="color:#c9c7c2; font-size:11px;">{esc(e["event"])}'
            f'{" ✓ issuer-confirmed" if e.get("confirmed") else " (provider est; verify)"}</span> '
            f'{chip(e["why"], GREEN if e["why"] == "HELD" else DIM)}</span>'
            for e in evs)
        st.markdown(
            f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'border-left:3px solid {AMBER}; padding:6px 12px; margin-bottom:4px; '
            f'border-radius:2px;"><span style="color:{AMBER}; font-size:12px; '
            f'font-weight:700;">{esc(d)} {esc(weekday)}</span>&nbsp;&nbsp;{rows}</div>',
            unsafe_allow_html=True)


# ── FACTOR SHEETS ────────────────────────────────────────────────────────────

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
    model = s.get("model_validation") or {}
    quality = s.get("data_quality") or {}
    model_status = str(model.get("status", "unknown")).upper()
    quality_status = "PASSED" if quality.get("ok") else "UNKNOWN"
    # live-IC feedback changes factor weights; surface it rather than silently
    # shipping a different model than the regime table implies
    fb = (reg or {}).get("ic_feedback") or {}
    if fb.get("enabled") and fb.get("detail"):
        base = (reg or {}).get("base_weights") or {}
        adj = (reg or {}).get("weights") or {}
        parts = [chip(f'{k} IC {d["ic"]:+.3f} n={d["n_independent"]} '
                      f'{base[k]:.2f}->{adj.get(k, base[k]):.3f}',
                      RED if d["mult"] < 1.0 else GREEN)
                 for k, d in sorted(fb["detail"].items()) if base.get(k)]
        if parts:
            st.markdown(chip("LIVE-IC FEEDBACK ACTIVE — de-weight only, "
                             "shrunk by evidence, floored 0.25", AMBER)
                        + "".join(parts), unsafe_allow_html=True)
    st.markdown(chip(s["method"], DIM)
                + chip(f"MODEL {model_status} · {model.get('role', 'unknown')}",
                       GREEN if model_status == "PRODUCTION_ELIGIBLE" else RED)
                + chip(f"DATA QUALITY {quality_status} · build "
                       f"{s.get('panel_build_id', 'legacy')} · "
                       f"{len(s.get('quarantined_tickers') or [])} quarantined",
                       GREEN if quality.get("ok") else RED)
                + chip("factor tilts are candidate generators, NOT proven alpha "
                       "(see SCORECARD)", RED),
                unsafe_allow_html=True)
    if model.get("reasons"):
        st.caption("Promotion blockers: " + " · ".join(str(x) for x in model["reasons"]))
    fund = load_json(RESEARCH / "fundamental_latest.json")
    technical = load_json(RESEARCH / "technical_latest.json")
    edgar_fund = load_json(RESEARCH / "edgar_fundamental_latest.json")
    tabs = st.tabs(["LONGS", "SHORTS", "SHOCK (REVERSION CANDIDATES)",
                    "REVISIONS", "CHEAP-QUALITY", "SEC FUNDAMENTALS",
                    "TECHNICAL STATE", "DATA SOURCES"])
    for tab, key in zip(tabs[:3], ("longs", "shorts", "shock_candidates")):
        with tab:
            items = s.get(key, [])
            if items:
                st.markdown(_heat_table(items), unsafe_allow_html=True)
    with tabs[3]:
        rows = (fund or {}).get("revision_leaders", [])
        scope = (fund or {}).get("scope") or {}
        st.markdown(chip(f"WITHIN {scope.get('ranking_scope', 'UNKNOWN SCOPE')} · "
                         "NOT MARKET-WIDE", RED), unsafe_allow_html=True)
        if rows:
            st.markdown("".join(
                chip(r["ticker"], AMBER) + chip(f"rev {r.get('est_revision')}", GREEN)
                + (chip(f"val {r.get('value')}", DIM) if r.get("value") is not None else "")
                for r in rows), unsafe_allow_html=True)
            st.markdown(chip((fund or {}).get("gate", ""), RED), unsafe_allow_html=True)
        else:
            st.info("Revision sheet builds from tonight's estimate snapshots.")
    with tabs[4]:
        rows = (fund or {}).get("cheap_quality", [])
        scope = (fund or {}).get("scope") or {}
        st.markdown(chip(f"WITHIN {scope.get('ranking_scope', 'UNKNOWN SCOPE')} · "
                         "NOT MARKET-WIDE", RED), unsafe_allow_html=True)
        if rows:
            st.markdown("".join(
                chip(r["ticker"], AMBER) + chip(f"value {r.get('value')}", GREEN)
                + chip(f"quality {r.get('quality')}", "#33d1c9") for r in rows),
                unsafe_allow_html=True)
        else:
            st.info("Cheap-quality joint screen builds as snapshots accrue.")
    with tabs[5]:
        if edgar_fund:
            st.markdown(chip(edgar_fund.get("gate", "DISCOVERY-ONLY"), RED)
                        + chip(edgar_fund.get("source", "SEC EDGAR"), GREEN)
                        + chip(f"{edgar_fund.get('n_eligible', 0)} eligible", DIM),
                        unsafe_allow_html=True)
            rows = [{"ticker": r.get("ticker"), "combined": r.get("edgar_quality_growth"),
                     "growth z": r.get("edgar_growth"), "quality z": r.get("edgar_quality"),
                     "revenue growth": r.get("revenue_growth"),
                     "op margin": r.get("operating_margin"), "ROE": r.get("roe"),
                     "FCF margin": r.get("fcf_margin"), "debt/CFO": r.get("debt_to_cfo"),
                     "latest filed": r.get("latest_filed")}
                    for r in edgar_fund.get("leaders", [])]
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("SEC fundamental factors build after the next EDGAR sweep.")
    with tabs[6]:
        if technical:
            st.markdown(chip(technical.get("gate", "RESEARCH-ONLY"), RED)
                        + chip(technical.get("method", ""), DIM),
                        unsafe_allow_html=True)
            rows = [{"ticker": r.get("ticker"), "setup": r.get("setup"),
                     "confidence": r.get("state_confidence"), "RSI14": r.get("rsi14"),
                     "ADX14": r.get("adx14"), "ATR%": r.get("atr14_pct"),
                     "MACD%": r.get("macd_hist_pct"), "BollZ": r.get("bollinger_z"),
                     "RS vs SPY 63d%": r.get("rs_spy_63d_pct"),
                     "Vol 20/120": r.get("volume_ratio_20_120"),
                     "DD126%": r.get("drawdown126_pct")}
                    for r in technical.get("setups", [])]
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True)
            else:
                st.info("No technically coherent setup cleared the descriptive screen.")
        else:
            st.info("Technical state builds with the next research run.")
    with tabs[7]:
        try:
            from advisor.source_health import assess as assess_source_health
            health = assess_source_health()
            st.markdown(chip(f"CRITICAL {health['healthy_critical']}/{health['critical_count']}",
                             GREEN if health.get("ok") else RED)
                        + chip("COMMERCIAL RIGHTS CLEAR" if health.get("commercially_clear")
                               else "COMMERCIAL DATA RIGHTS NOT CLEAR", RED),
                        unsafe_allow_html=True)
            rows = [{"source": name, "provider": item.get("provider"),
                     "healthy": item.get("healthy"), "role": item.get("role"),
                     "redistribution": item.get("commercial_redistribution"),
                     "detail": item.get("detail")}
                    for name, item in health.get("sources", {}).items()]
            st.dataframe(rows, width="stretch", hide_index=True)
        except Exception as exc:
            st.warning(f"Source-health inventory unavailable: {type(exc).__name__}")


def _heat_cell(v) -> str:
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
            f'<td style="padding:2px 8px; color:{AMBER}; font-weight:700;">{esc(x["ticker"])}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{x["px"]:,.2f}</td>'
            f'{score_cell}'
            f'<td style="padding:2px 8px; color:{DIM}; font-size:11px;">{esc(x["sector"][:18])}</td>'
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


# ── PORTFOLIO / RISK / ALERTS ────────────────────────────────────────────────

def portfolio_risk():
    # ADVISOR BOOK ONLY — the legacy bot's pre-advisor record is a different
    # strategy and is deliberately not shown here (user rule 2026-06-12).
    panel_header("PORTFOLIO / RISK", "advisor book only — strategy started 2026-06-12")
    calls = journal_effective()
    open_calls = {k: v for k, v in calls.items()
                  if v.get("status") == "open" and v.get("type") == "view"}
    resolved = {k: v for k, v in calls.items()
                if v.get("status") in ("hit_target", "stopped", "time_stop", "closed")}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BUDGET", "$25,000", "advisor mandate")
    c2.metric("OPEN CALLS", len(open_calls))
    c3.metric("RESOLVED CALLS", len(resolved),
              f"{sum(1 for v in resolved.values() if v.get('status') == 'hit_target')} hit target")
    days = sorted([d.name for d in CTX.iterdir() if d.is_dir()], reverse=True) \
        if CTX.exists() else []
    snap = load_json(CTX / days[0] / "portfolio.json") if days else None
    op = (snap or {}).get("state", {}).get("open_position")
    c4.metric("TIER-1 POSITION", f"{op.get('short_strike')}/{op.get('long_strike')}P"
              if op else "NONE")
    if open_calls:
        st.markdown(_levels_board(open_calls), unsafe_allow_html=True)

    panel_header("ALERT CENTER", "filings on held/watched names · watcher level hits")
    alerts = load_jsonl_tail(DATA / "alerts" / "intraday_alerts.jsonl", 15)
    watcher = {k: v for k, v in (load_json(DATA / "watcher_alerts.json") or {}).items() if v}
    if not alerts and not watcher:
        st.markdown(f'<div style="color:{DIM}; font-size:12px;">quiet — exit-watcher '
                    f'armed, filing poller live (RTH, 20min)</div>',
                    unsafe_allow_html=True)
    for a in alerts:
        alert_url = safe_http_url(a.get("url"))
        st.markdown(
            chip(a.get("ts", "")[:16], DIM)
            + chip(a.get("ticker", "?"), AMBER)
            + chip(f"{a.get('form', a.get('kind', ''))} filed {a.get('filed', '')}", "#58a6ff")
            + (f'<a href="{alert_url}" target="_self" rel="noopener noreferrer" '
               f'style="color:{AMBER}; font-size:11px;">[filing]</a>'
               if alert_url else ""),
            unsafe_allow_html=True)
    if watcher:
        st.markdown(f'<div style="color:{DIM}; font-size:12px; margin-top:6px;">'
                    f'watcher level-hits: '
                    + esc(", ".join(f"{k} ({','.join(v.keys())})" for k, v in watcher.items()))
                    + '</div>', unsafe_allow_html=True)


def _levels_board(open_calls: dict) -> str:
    """Live stop ↔ price ↔ target position bar per open call."""
    tkrs = [v.get("yf_ticker") for v in open_calls.values() if v.get("yf_ticker")]
    quotes = get_quotes(sorted(set(tkrs)))
    px_map = {t: q["px"] for t, q in quotes.items()
              if isinstance(q.get("px"), (int, float))}
    rows = []
    for k, v in open_calls.items():
        tkr = v.get("yf_ticker", "")
        px, sp, tp = px_map.get(tkr), v.get("stop_px"), v.get("target_px")
        src = (quotes.get(tkr) or {}).get("src", "")
        bar = f'<span style="color:{DIM};">no live levels</span>'
        if all(isinstance(x, (int, float)) for x in (px, sp, tp)) and sp != tp:
            long_ = (v.get("direction") or "long").lower() != "short"
            pos = (px - sp) / (tp - sp)            # works both directions
            pos_c = max(0.0, min(1.0, pos))
            toward = "→TGT" if (pos > 0.5) else ("→STP" if pos < 0.25 else "")
            t_col = GREEN if toward == "→TGT" else (RED if toward else DIM)
            bar = (
                f'<span style="color:{RED}; font-size:10px;">STP {sp}</span>'
                f'<span style="display:inline-block; width:170px; height:9px; '
                f'background:linear-gradient(90deg, rgba(255,92,87,.35), '
                f'rgba(51,209,122,.35)); border:1px solid {PANEL_BORDER}; '
                f'position:relative; vertical-align:middle; margin:0 6px;">'
                f'<span style="position:absolute; left:{pos_c*100:.0f}%; top:-2px; '
                f'width:2px; height:11px; background:#fff;"></span></span>'
                f'<span style="color:{GREEN}; font-size:10px;">TGT {tp}</span> '
                f'<span style="color:#e8e6e3;">px {px:,.2f}</span> '
                f'<span style="color:{t_col}; font-size:10px;">{toward}</span> '
                f'<span style="color:{DIM}; font-size:10px;">'
                f'({"long" if long_ else "short"} · {pos*100:.0f}% · {esc(src)})</span>')
        rows.append(
            f'<tr style="border-bottom:1px solid #161b22;">'
            f'<td style="padding:4px 10px; color:{AMBER}; font-weight:700; '
            f'white-space:nowrap;">{esc(v.get("instrument", "?")[:34])}</td>'
            f'<td style="padding:4px 10px; color:{DIM}; font-size:10px;">{esc(k)}</td>'
            f'<td style="padding:4px 10px; white-space:nowrap;">{bar}</td>'
            f'<td style="padding:4px 10px; color:{DIM}; font-size:10px; '
            f'white-space:nowrap;">t-stop {esc(v.get("time_stop", "—"))}</td></tr>')
    return (f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'border-radius:2px; padding:4px; margin-top:6px;">'
            f'<table style="font-family:Menlo,monospace; font-size:12px; '
            f'color:#e8e6e3; border-collapse:collapse; width:100%;">'
            f'{"".join(rows)}</table></div>')


# ── SCORECARD · CALIBRATION · VALIDATION ─────────────────────────────────────

def scorecard_doctrine():
    panel_header("SCORECARD · DOCTRINE", "every call accountable; every number honest")
    calls = journal_effective()
    rows = [{"id": k, "status": v.get("status"), "type": v.get("type"),
             "instrument": v.get("instrument"), "dir": v.get("direction"),
             "src": v.get("source"), "p_win": v.get("p_win"),
             "ref_px": v.get("ref_px"), "R": v.get("realized_r"),
             "outcome": v.get("outcome_tag"),
             "entry": v.get("entry"), "target": v.get("target"),
             "stop": v.get("stop"), "ts": (v.get("ts") or "")[:16]}
            for k, v in calls.items() if v.get("type") != "stamp"]
    if rows:
        df = pd.DataFrame(rows)
        resolved = df[df.status.isin(["hit_target", "stopped", "time_stop", "closed"])]
        wins = (resolved.status == "hit_target").sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CALLS (ALL)", len(df))
        c2.metric("RESOLVED", len(resolved))
        c3.metric("HIT RATE", f"{wins / len(resolved) * 100:.0f}%" if len(resolved) else "—")
        c4.metric("REJECTED TRACKED",
                  int((df.type == "rejected").sum()) if "type" in df else 0,
                  "counterfactual-scored")
        st.dataframe(df, width="stretch", hide_index=True, height=240)

    cal = load_json(RESEARCH / "calibration_latest.json")
    att = load_json(RESEARCH / "attribution_latest.json")
    cc1, cc2 = st.columns(2)
    with cc1:
        body = f'<div style="color:{DIM}; font-size:12px;">not computed yet</div>'
        if cal:
            rel = "".join(
                f'<div style="color:#c9c7c2; font-size:11px;">stated {esc(p)}: '
                f'n={r["n"]} realized {r["realized_hit_rate"]}</div>'
                for p, r in (cal.get("reliability_by_stated_p") or {}).items())
            body = (f'<div style="color:#e8e6e3; font-size:13px;">Brier '
                    f'{cal.get("brier", "—")} vs 0.25 baseline · '
                    f'n={cal.get("n_resolved_scored")} '
                    f'(explicit {cal.get("n_explicit_scored", "—")}; '
                    f'legacy-map {cal.get("n_legacy_mapped_scored", "—")})</div>'
                    f'{chip(cal.get("sample_gate", ""), AMBER)}{rel}')
        st.markdown(
            f'<div style="border:1px solid #2a2f36; background:#11151a; padding:10px; '
            f'border-radius:4px;"><span style="color:{AMBER}; font-weight:700;">'
            f'CONVICTION CALIBRATION</span><br>{body}</div>', unsafe_allow_html=True)
    with cc2:
        body = f'<div style="color:{DIM}; font-size:12px;">not computed yet</div>'
        if att:
            body = "".join(
                f'<div style="color:#c9c7c2; font-size:11px;">'
                f'<span style="color:{AMBER};">{esc(src)}</span> n={t["n"]} '
                f'hit={t["hit_rate"]} avgR={t["avg_realized_r"]} '
                f'<span style="color:{DIM};">[{t["sample_gate"]}]</span></div>'
                for src, t in (att.get("by_source") or {}).items()) or body
            kills = [c for c in att.get("rejected_counterfactuals", [])
                     if c.get("kill_cost_pct") is not None]
            if kills:
                worst = max(kills, key=lambda c: c["kill_cost_pct"])
                body += (f'<div style="color:{DIM}; font-size:11px; margin-top:4px;">'
                         f'kill costs tracked on {len(kills)} rejects · worst: '
                         f'{worst["yf_ticker"]} {worst["kill_cost_pct"]:+.1f}%</div>')
        st.markdown(
            f'<div style="border:1px solid #2a2f36; background:#11151a; padding:10px; '
            f'border-radius:4px;"><span style="color:{AMBER}; font-weight:700;">'
            f'ATTRIBUTION BY GENERATOR</span><br>{body}</div>', unsafe_allow_html=True)

    v2 = load_json(RESEARCH / "validation2_latest.json")
    if v2:
        wf = v2.get("walk_forward_top20", {})
        dsr = (wf.get("deflated_sharpe_oos") or {}).get("dsr")
        survivors = [k for k, t in v2.get("tests", {}).items()
                     if k.endswith("|oos") and t.get("fdr10_survives")]
        st.markdown(
            f'<div style="border:1px solid #2a2f36; border-left:3px solid {RED}; '
            f'background:#11151a; padding:10px; border-radius:4px; margin:8px 0;">'
            f'<span style="color:{AMBER}; font-weight:700;">VALIDATION (validate2 — honest)</span> '
            + chip(f"as of {v2.get('as_of', '')[:16]}", DIM)
            + chip(f"config {v2.get('config_hash')}", DIM)
            + f'<div style="color:#c9c7c2; font-size:12px; margin-top:6px;">'
            f'{wf.get("oos_n_periods", 0)} non-overlapping OOS 21d periods · '
            f'{v2.get("n_tests_in_grid")} total tests · OOS FDR-10% survivors: '
            f'<span style="color:{RED};">{esc(", ".join(survivors) or "NONE")}</span><br>'
            f'walk-forward net {wf.get("net_total_return_pct")}% vs SPY '
            f'{wf.get("spy_total_pct")}% (all periods; context only) · OOS deflated Sharpe '
            f'<span style="color:{RED if (dsr or 0) < 0.95 else GREEN};">{dsr}</span> '
            f'(&lt;0.95 = not proven)</div>'
            f'<div style="color:{DIM}; font-size:10px; margin-top:4px;">'
            + esc(" · ".join(v2.get("caveats", [])[:2])) + '</div></div>',
            unsafe_allow_html=True)

    ic = load_json(RESEARCH / "ic_live.json")
    if ic and ic.get("factors"):
        fac = ic["factors"]
        n_ind = max((s.get("n_independent") or 0) for s in fac.values())
        line = " · ".join(
            f'{k} {s.get("mean_ic_independent", s["ewma_ic"]):+.3f}'
            for k, s in fac.items())
        st.markdown(
            chip(f"live IC — {ic.get('n_matured')} daily snapshots "
                 f"= {n_ind} INDEPENDENT 21d windows", AMBER)
            + chip(line, DIM)
            + chip("overlapping-window t-stats are not inference; see validate2",
                   RED),
            unsafe_allow_html=True)
    else:
        st.markdown(chip("live IC series: first snapshots mature in ~21 trading days", DIM),
                    unsafe_allow_html=True)

    lessons = load_jsonl_tail(DATA / "lessons.jsonl", 6)
    if lessons:
        body = "".join(
            f'<div style="color:#c9c7c2; font-size:11px;">'
            f'{esc(le.get("ts", "")[:10])} <span style="color:{AMBER};">{esc(le.get("call_id"))}</span> '
            f'{esc(le.get("outcome_tag", ""))} — {esc(le.get("proposed_lesson") or "no lesson")}</div>'
            for le in lessons)
        st.markdown(
            f'<div style="border:1px solid #2a2f36; background:#11151a; padding:10px; '
            f'border-radius:4px;"><span style="color:{AMBER}; font-weight:700;">'
            f'POST-MORTEM LESSONS (auto)</span>{body}</div>', unsafe_allow_html=True)

    val = load_json(RESEARCH / "ic_validation.json")
    if val:
        with st.expander("legacy 2y validation (superseded by validate2 — kept for history)"):
            st.json(val)
    with st.expander("METHODOLOGY (loop doctrine + lessons log)"):
        st.markdown((REPO / "advisor" / "METHODOLOGY.md").read_text())
    with st.expander("IPS (investment policy)"):
        st.markdown((REPO / "advisor" / "IPS.md").read_text())
    with st.expander("TUNING NOTES (data-gated decisions)"):
        p = REPO / "advisor" / "TUNING_NOTES.md"
        if p.exists():
            st.markdown(p.read_text())



# ── DAILY PICKS + TRACK RECORD ───────────────────────────────────────────────

def picks_tab():
    picks = load_json(RESEARCH / "picks_latest.json")
    if not picks or not picks.get("picks"):
        st.info("No picks yet — run `python -m advisor.research.picks`.")
        return
    src = picks.get("source", "live")
    panel_header("DAILY RANKED PICKS",
                 f"{picks['as_of'][:16]} · bar {picks.get('price_bar')} · "
                 f"top {picks['n_picks']} of {picks['n_slate']} slate · "
                 f"horizon {picks.get('horizon_trading_days')}td · {src}")
    st.markdown(chip(picks.get("class", "research_idea"), AMBER)
                + chip("levels: deterministic ATR — no model-authored numbers", DIM),
                unsafe_allow_html=True)

    # The record panel is not decoration: it is the honest health of this lane.
    rec = load_json(RESEARCH / "pick_record.json") or {}
    if rec.get("resolved"):
        edge = rec.get("vs_spy_pp")
        col = RED if (edge is not None and edge < 0) else GREEN
        st.markdown(
            f'<div style="border:1px solid {col}; background:#11151a; padding:8px 12px; '
            f'border-radius:3px; margin:6px 0;">'
            f'<span style="color:{col}; font-weight:700;">LANE PERFORMANCE — '
            f'{rec["resolved"]} resolved picks</span> '
            f'<span style="color:#c9c7c2; font-size:12px;">hit {rec.get("hit_rate")} · '
            f'avg {rec.get("avg_r")}R · vs SPY {edge:+.1f}pp over {rec.get("horizon_td")}td'
            f'</span><br><span style="color:{DIM}; font-size:11px;">'
            f'{rec.get("verdict","")}</span></div>', unsafe_allow_html=True)

    rows = []
    for i, p in enumerate(picks["picks"], 1):
        conf = (f'{p["confidence_pct"]}%' if p.get("confidence_pct") is not None
                else f'{p["score"]:.2f} <span style="color:{DIM};">uncal</span>')
        dcol = GREEN if p["direction"] == "long" else RED
        gens = "".join(chip(g, BUCKET_COLORS.get(g, DIM)) for g in p.get("generators", [])[:3])
        rows.append(
            f'<tr style="border-bottom:1px solid #161b22;">'
            f'<td style="padding:3px 8px; color:{DIM};">{i}</td>'
            f'<td style="padding:3px 8px; color:{AMBER}; font-weight:700;">{p["ticker"]}</td>'
            f'<td style="padding:3px 8px; color:{dcol}; font-weight:700;">{p["direction"].upper()}</td>'
            f'<td style="padding:3px 8px; color:#e8e6e3;">{conf}</td>'
            f'<td style="padding:3px 8px;">{p["entry_low"]}–{p["entry_high"]}</td>'
            f'<td style="padding:3px 8px; color:{RED};">{p["stop"]}</td>'
            f'<td style="padding:3px 8px; color:{GREEN};">{p["target"]}</td>'
            f'<td style="padding:3px 8px; color:{DIM};">{p.get("atr20")}</td>'
            f'<td style="padding:3px 8px;">{gens}</td>'
            f'<td style="padding:3px 8px; color:{DIM}; font-size:11px;">'
            f'{p.get("next_earnings") or "—"}</td></tr>')
    head = "".join(f'<th style="padding:4px 8px; color:{AMBER}; text-align:left; '
                   f'border-bottom:1px solid {PANEL_BORDER};">{h}</th>'
                   for h in ("#", "TKR", "DIR", "CONF", "ENTRY", "STOP", "TARGET",
                             "ATR20", "GENERATORS", "NEXT EPS"))
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
        f'border-radius:2px; padding:4px; overflow-x:auto;">'
        f'<table style="font-size:12px; font-family:Menlo,monospace; color:#e8e6e3; '
        f'border-collapse:collapse; width:100%;"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)
    st.markdown(chip(picks.get("score_method", ""), DIM)
                + chip(picks.get("disclaimer", ""), DIM), unsafe_allow_html=True)


def record_tab():
    rec = load_json(RESEARCH / "pick_record.json") or {}
    cal = load_json(RESEARCH / "pick_calibration.json") or {}
    panel_header("TRACK RECORD · AUTO-CALIBRATION",
                 f"every pick followed to conclusion · {rec.get('as_of','')[:16]}")
    if not rec.get("resolved"):
        st.info("No resolved picks yet — run `pick_tracker --resolve`.")
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("PICKS", rec.get("total_picks"))
    c2.metric("RESOLVED", rec.get("resolved"))
    c3.metric("HIT RATE", f'{(rec.get("hit_rate") or 0)*100:.1f}%')
    c4.metric("AVG R", rec.get("avg_r"))
    edge = rec.get("vs_spy_pp")
    c5.metric("VS SPY", f'{edge:+.1f}pp' if edge is not None else "—",
              "underperforms" if (edge or 0) < 0 else "outperforms")

    st.markdown(
        f'<div style="border:1px solid {RED}; background:#11151a; padding:10px; '
        f'border-radius:4px; margin:8px 0;"><span style="color:{RED}; '
        f'font-weight:700;">HONEST VERDICT</span><br>'
        f'<span style="color:#c9c7c2; font-size:12.5px;">{rec.get("verdict","")}</span>'
        f'</div>', unsafe_allow_html=True)

    by = rec.get("by_outcome") or {}
    st.markdown("".join(
        chip(f"{k}: {v}", {"target": GREEN, "stop": RED}.get(k, DIM))
        for k, v in sorted(by.items(), key=lambda kv: -kv[1])),
        unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    panel_header("CALIBRATION", cal.get("gate", ""))
    buckets = cal.get("buckets") or []
    if buckets:
        rows = []
        for b in buckets:
            width = int(b["hit_rate"] * 220)
            rows.append(
                f'<tr><td style="padding:3px 10px; color:#e8e6e3;">'
                f'score {b["lo"]:.2f}–{b["hi"]:.2f}</td>'
                f'<td style="padding:3px 10px; color:{AMBER}; font-weight:700;">'
                f'{b["hit_rate"]*100:.1f}%</td>'
                f'<td style="padding:3px 10px; color:{DIM};">n={b["n"]}</td>'
                f'<td style="padding:3px 10px;"><span style="display:inline-block; '
                f'width:{width}px; height:9px; background:{AMBER}; opacity:.65;"></span></td></tr>')
        st.markdown(
            f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
            f'padding:6px; border-radius:2px;"><table style="font-size:12px; '
            f'font-family:Menlo,monospace; border-collapse:collapse; width:100%;">'
            f'{"".join(rows)}</table></div>', unsafe_allow_html=True)
        st.markdown(chip(f"method: {cal.get('method','')}", DIM)
                    + chip(f"fitted {cal.get('fitted_at','')[:16]} · "
                           f"id {str(cal.get('calibration_id',''))[:8]}", DIM),
                    unsafe_allow_html=True)


# ── LAYOUT ───────────────────────────────────────────────────────────────────

def _regime_chip() -> str:
    s = load_json(RESEARCH / "signals_latest.json") or {}
    reg = (s.get("regime") or {})
    name = (reg.get("name") or "?").upper()
    col = {"RISK_ON": GREEN, "NEUTRAL": AMBER, "STRESS": RED}.get(name, DIM)
    return (f'<span style="color:{col}; border:1px solid {col}; padding:1px 8px; '
            f'border-radius:2px; font-size:11px; font-weight:700;">REGIME {esc(name)}'
            f' · VIX {esc(reg.get("vix", "?"))} · '
            f'TERM {esc(reg.get("vix_term", "?"))}</span>')


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

st.markdown(
    f'<div style="background:#151109; border:1px solid {AMBER}; padding:4px 10px; '
    f'color:#d7d2c8; font-size:10px; letter-spacing:.4px;">'
    f'<b style="color:{AMBER};">RESEARCH IDEAS — NOT PERSONALIZED ADVICE.</b> '
    f'Projected probabilities and outcomes are hypothetical, may change with each run, '
    f'do not reflect actual client results, and are not guarantees. Uncalibrated estimates '
    f'are labeled UNCAL. Verify source evidence, suitability, liquidity, tax, and loss risk '
    f'before acting.</div>', unsafe_allow_html=True)
with st.expander("METHODOLOGY · UNIVERSE · LIMITATIONS · CONFLICTS"):
    st.markdown(
        "**Selection.** A broad U.S. equity/ETF research universe is filtered for "
        "liquidity, ranked using sector-neutral technical/fundamental signals, then "
        "subjected to catalyst, primary-evidence, valuation, risk/reward, and independent "
        "red-team gates. Other securities may have similar or superior characteristics.\n\n"
        "**Limitations.** Factor ranks are discovery-only until their machine-readable "
        "validation gate passes. `p_win` is analyst judgment unless explicitly marked CAL. "
        "Delayed feeds, source errors, regime changes, slippage, gaps, and corporate events "
        "can invalidate a view. A missing verified portfolio makes all sizing illustrative.\n\n"
        "**Conflicts and data rights.** The prototype has no issuer compensation or market-"
        "making relationship recorded. Commercial redistribution is disabled pending "
        "licensed market-data contracts and a formal conflict-disclosure process."
    )

# ── controlled on-demand generation + data reload ───────────────────────────
@st.fragment(run_every="5s")
def brief_controls() -> None:
    _bc1, _bc2, _bc3, _bc4 = st.columns([1.1, 1.45, 1, 4.45])
    allow = os.environ.get("ADVISOR_ALLOW_UI_REGEN") == "1"
    state = brief_control_status()
    running = state["state"] in {"starting", "running"}
    now_ts = datetime.now(ET).timestamp()
    arm_deadline = st.session_state.get("regen_arm_deadline", 0)
    arm_remaining = arm_remaining_s(arm_deadline, now_ts=now_ts)
    armed = allow and not running and arm_remaining > 0
    with _bc1:
        if st.button("1 · ARM 60s", width="stretch", disabled=running or not allow,
                     help="Temporarily arm one research run. Arming expires after "
                          "60 seconds and never places an order."):
            arm_deadline = now_ts + 60
            st.session_state["regen_arm_deadline"] = arm_deadline
            arm_remaining = arm_remaining_s(arm_deadline, now_ts=now_ts)
            armed = True
        if armed:
            st.caption(f"ARMED · {arm_remaining}s")
    with _bc2:
        if st.button("⟳ GENERATE NEW BRIEF", width="stretch",
                     disabled=running or not allow or not armed,
                     help="Queue macro → synthesis → red-team → publish through "
                          "the credential-isolated system service."):
            # Every click consumes the arm, even when the service refuses the request.
            st.session_state["regen_arm_deadline"] = 0
            result = request_generation()
            if result["outcome"] == "accepted":
                st.toast("New brief queued. Progress will appear here automatically.",
                         icon="🔄")
            elif result["reason"] == "cooldown":
                st.toast(f"Cooldown active — retry in {result['retry_after_s']}s.",
                         icon="⏱️")
            elif result["reason"] == "already_running":
                st.toast("A brief pipeline is already running.", icon="🔒")
            else:
                st.toast("Brief request failed safely; inspect the operator audit.",
                         icon="⚠️")
    with _bc3:
        st.button("↻ RELOAD DATA", width="stretch", on_click=manual_reload,
                  help="Re-read published artifacts. This does not generate a brief.")
        _reloaded = st.session_state.get("advisor_last_manual_reload")
        if _reloaded:
            try:
                _reload_time = datetime.fromisoformat(_reloaded).strftime("%H:%M:%S ET")
                st.caption(f"DATA RELOADED {_reload_time}")
            except ValueError:
                pass
    with _bc4:
        if running:
            st.info(f"BRIEF RUNNING · {str(state['stage']).upper()} · status updates every 5s")
        elif state["state"] == "complete":
            st.success(f"LATEST BRIEF COMPLETE · {state.get('date') or '—'}")
        elif state["state"] == "failed":
            st.warning(f"LAST RUN FAILED CLOSED · {str(state['stage']).upper()} · "
                       f"{state.get('reason') or 'unknown'} · prior brief preserved")
        elif not allow:
            st.caption("ON-DEMAND GENERATION DISABLED BY OPERATOR POLICY")


brief_controls()
tape()
active_recommendations()

t0, t9, t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs(
    ["PICK ▸ DAILY PICKS", "REC ▸ TRACK RECORD",
     "RSCH ▸ RESEARCH", "CALL ▸ OPEN CALLS", "IDEA ▸ SLATE·WATCH",
     "FCTR ▸ FACTORS", "DOSR ▸ DOSSIERS", "CAL ▸ CALENDAR",
     "PORT ▸ PORTFOLIO", "SCOR ▸ SCORECARD"])
with t0:
    picks_tab()
with t9:
    record_tab()
with t1:
    research_feed()
with t2:
    open_calls_and_charts()
with t3:
    ideas_tab()
with t4:
    factor_sheets()
with t5:
    dossier_tab()
with t6:
    calendar_tab()
with t7:
    portfolio_risk()
with t8:
    scorecard_doctrine()


# ── STATUS FOOTER (service health + data ages) ───────────────────────────────

SERVICES = (("advisor-quoted", "QUOTED"), ("advisor-approvals", "APPROVALS"),
            ("advisor-exitwatch", "EXITWATCH"), ("advisor-terminal", "TERMINAL"),
            ("advisor-events", "EVENTS"), ("advisor-watchdog", "WATCHDOG"),
            ("advisor-brief", "BRIEF"), ("advisor-research", "RSCH"),
            ("advisor-librarian", "LIBRARIAN"), ("advisor-ivsnap", "IVSNAP"))


# Linux units differ from the mac launchd set: no approvals/events/ivsnap
# (Tier-1 execution and the IV snapshot are mac-side), plus timers.
SERVICES_SYSTEMD = (("advisor-terminal.service", "TERMINAL"),
                    ("advisor-quoted.service", "QUOTED"),
                    ("advisor-exitwatch.service", "EXITWATCH"),
                    ("advisor-watchdog.timer", "WATCHDOG"),
                    ("advisor-research.timer", "RSCH"),
                    ("advisor-brief.timer", "BRIEF"),
                    ("advisor-librarian.timer", "LIBRARIAN"))


@st.cache_data(ttl=60)
def _service_status() -> list[tuple]:
    """Health row. launchd on macOS, systemd --user on the Linux host —
    the mac-only path used to render a single red LAUNCHCTL ERR there."""
    import sys as _sys
    out: list[tuple] = []
    if _sys.platform == "darwin":
        try:
            listing = subprocess.run(["launchctl", "list"], capture_output=True,
                                     text=True, timeout=5).stdout
            for svc, label in SERVICES:
                line = [l for l in listing.splitlines() if svc in l]
                if not line:
                    out.append((label, "MISSING", RED))
                    continue
                pid, status = line[0].split()[0], line[0].split()[1]
                if pid != "-":
                    out.append((label, "LIVE", GREEN))
                elif status == "0":
                    out.append((label, "OK", AMBER))   # scheduled, last run clean
                elif status == "-15":
                    out.append((label, "RESTART", AMBER))
                else:
                    out.append((label, f"ERR({status})", RED))
        except Exception:
            out.append(("LAUNCHCTL", "ERR", RED))
        return out
    for unit, label in SERVICES_SYSTEMD:
        try:
            state = subprocess.run(["systemctl", "--user", "is-active", unit],
                                   capture_output=True, text=True,
                                   timeout=5).stdout.strip()
        except Exception:
            out.append((label, "ERR", RED))
            continue
        if state == "active":
            out.append((label, "LIVE" if unit.endswith(".service") else "ARMED",
                        GREEN))
        elif state in ("activating", "reloading"):
            out.append((label, "START", AMBER))
        else:
            out.append((label, state.upper() or "DEAD", RED))
    return out


def _last_pipeline_note() -> str:
    rows = load_jsonl_tail(REPO / "advisor" / "logs" / "pipeline_runs.jsonl", 12)
    for r in rows:
        if r.get("stage") == "pipeline":
            ok = r.get("rc") == 0
            return (f'PIPE <span style="color:{GREEN if ok else RED};">'
                    f'{"OK" if ok else "rc=" + str(r.get("rc"))}</span> '
                    f'{r.get("ts", "")[5:16]}')
    return "PIPE —"


def status_footer():
    cells = "".join(
        f'<span style="margin-right:12px;"><span style="color:{DIM};">{n}</span> '
        f'<span style="color:{c}; font-weight:700;">●{s}</span></span>'
        for n, s, c in _service_status())
    try:
        meta = current_panel_meta()
    except Exception:
        meta = {}
    import time as _t
    panel_age = (_t.time() - meta.get("built_unix", 0)) / 3600 if meta.get("built_unix") else None
    sig = load_json(RESEARCH / "signals_latest.json") or {}
    snap = quote_store()
    ages = (f'QUOTES {snap.get("age_s", 0):.0f}s' if snap else 'QUOTES DOWN')
    ages += f' · PANEL {panel_age:.1f}h' if panel_age and panel_age < 1e4 else ' · PANEL —'
    ages += f' · SIGNALS {(sig.get("as_of") or "—")[:16]}'
    ages += f' · {_last_pipeline_note()}'
    n_open = sum(1 for e in journal_effective().values()
                 if e.get("status") == "open" and e.get("type") == "view")
    try:
        trust = production_assess()
        verdict = trust["verdict"].upper()
        vcol = {"READY": GREEN, "DEGRADED": AMBER, "BLOCKED": RED}[verdict]
        release = load_json(DATA / "deployment_manifest.json") or {}
        trust_html = (f'<span style="color:{vcol}; font-weight:700;">'
                      f'TRUST {verdict}</span> · REL {esc(release.get("release_id", "—"))}')
    except Exception:
        trust_html = f'<span style="color:{RED};">TRUST ERROR</span>'
    st.markdown(
        f'<div style="background:{PANEL_BG}; border:1px solid {PANEL_BORDER}; '
        f'padding:3px 12px; border-radius:2px; margin-top:6px; font-size:10px; '
        f'font-family:Menlo,monospace; display:flex; justify-content:space-between;">'
        f'<span>{cells}</span>'
        f'<span style="color:{DIM};">{trust_html} · {ages} · OPEN {n_open} · '
        f'{datetime.now(ET).strftime("%a %H:%M ET")}</span></div>',
        unsafe_allow_html=True)


status_footer()
