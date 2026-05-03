"""
dashboard.py — MES ORB Bot monitoring dashboard (Streamlit).

Live view  : current bot state, open position P&L, VWAP, range levels
Today      : completed trades table for the current session
History    : daily P&L chart + summary table across all sessions

Run locally with SSH tunnel:
    ssh -L 8502:127.0.0.1:8502 ubuntu@34.247.209.179
    http://127.0.0.1:8502

Start on server:
    sudo systemctl start mes-orb-dashboard
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

# ── Path setup ─────────────────────────────────────────────────────────────────
# When run by systemd the CWD is the bot root; adjust if needed.
BOT_ROOT = Path(__file__).parent.resolve()
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

import journal   # noqa: E402 — needs sys.path above

ET = ZoneInfo("America/New_York")

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MES ORB Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _pnl_color(v: float | None) -> str:
    if v is None:
        return "gray"
    return "#2ecc71" if v >= 0 else "#e74c3c"


def _fmt_usd(v: float | None, always_sign: bool = True) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    if always_sign:
        return f"{sign}${v:,.2f}"
    return f"${v:,.2f}"


def _fmt_pts(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f} pts"


def _state_badge(state: str) -> str:
    colors = {
        "STARTUP": "#95a5a6",
        "RANGE_BUILDING": "#f39c12",
        "WAITING_ENTRY": "#3498db",
        "IN_TRADE_LONG": "#2ecc71",
        "IN_TRADE_SHORT": "#e74c3c",
        "DAILY_DONE": "#9b59b6",
        "HALTED": "#e67e22",
        "CLOSED": "#7f8c8d",
    }
    c = colors.get(state, "#bdc3c7")
    return f'<span style="background:{c};color:white;padding:3px 10px;border-radius:4px;font-weight:bold;font-size:0.9em">{state}</span>'


def _reason_badge(reason: str) -> str:
    colors = {
        "WIN": "#2ecc71",
        "LOSS": "#e74c3c",
        "VWAP_STOP": "#e67e22",
        "WARNING_EXIT": "#e67e22",
        "HARD_CLOSE": "#7f8c8d",
        "STOP": "#e74c3c",
        "TARGET": "#2ecc71",
    }
    c = colors.get(reason, "#bdc3c7")
    return f'<span style="background:{c};color:white;padding:2px 8px;border-radius:3px;font-size:0.85em">{reason}</span>'


def _staleness_warning(ts_str: str | None) -> None:
    if not ts_str:
        return
    try:
        ts = datetime.fromisoformat(ts_str)
        age = (datetime.now(ET) - ts).total_seconds()
        if age > 120:
            st.warning(f"⚠️ Status data is {age/60:.0f} min old — bot may not be running.")
    except Exception:
        pass


# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=10)
def _get_status():
    return journal.load_status(str(BOT_ROOT / "logs"))


@st.cache_data(ttl=10)
def _get_today_trades():
    return journal.load_today_trades(str(BOT_ROOT / "logs"))


@st.cache_data(ttl=60)
def _get_summaries():
    return journal.load_all_summaries(str(BOT_ROOT / "logs"))


# ── Header ─────────────────────────────────────────────────────────────────────

st.markdown(
    "<h1 style='margin-bottom:0'>📈 MES ORB Bot</h1>"
    "<p style='color:gray;margin-top:4px'>Micro E-mini S&P 500 · Opening Range Breakout · LONG ONLY</p>",
    unsafe_allow_html=True,
)

now_et = datetime.now(ET)
st.markdown(
    f"<p style='color:#888;font-size:0.85em'>ET: {now_et.strftime('%A %Y-%m-%d %H:%M:%S')} "
    f"&nbsp;·&nbsp; Auto-refreshes every 10s</p>",
    unsafe_allow_html=True,
)

# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_live, tab_today, tab_history = st.tabs(["🔴 Live", "📋 Today's Trades", "📊 History"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — LIVE
# ════════════════════════════════════════════════════════════════════════════════

def _render_live_tab() -> None:
    """
    Renders the Live tab content.  Extracted into a function so that `return`
    can be used for early exit — st.stop() would halt the whole script and
    prevent the Today and History tabs from rendering when status.json is absent.
    """
    status = _get_status()

    if status is None:
        st.info("No status data yet. Bot hasn't started today or logs directory is empty.")
        return   # return, not st.stop() — lets other tabs render normally

    _staleness_warning(status.get("timestamp"))

    state = status.get("state", "UNKNOWN")
    paper = status.get("paper_trading", True)

    # ── Top row: state + mode ──────────────────────────────────────────────────
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        st.markdown(f"**Bot State** &nbsp; {_state_badge(state)}", unsafe_allow_html=True)
    with c2:
        mode_label = "📄 PAPER" if paper else "🔴 LIVE"
        st.markdown(f"**Mode:** {mode_label}")
    with c3:
        ts = status.get("timestamp", "")
        ts_fmt = ts[11:19] if len(ts) >= 19 else ts
        st.markdown(f"**Updated:** {ts_fmt} ET")

    st.divider()

    # ── Daily P&L ──────────────────────────────────────────────────────────────
    st.markdown("#### Daily P&L")
    d1, d2, d3, d4, d5 = st.columns(5)
    net = status.get("daily_net_usd", 0.0) or 0.0
    gross = status.get("daily_gross_usd", 0.0) or 0.0
    comm = status.get("daily_comm_usd", 0.0) or 0.0
    trades = status.get("daily_trades", 0) or 0
    wins = status.get("daily_wins", 0) or 0

    with d1:
        st.metric("Net P&L", _fmt_usd(net), delta=None)
    with d2:
        st.metric("Gross", _fmt_usd(gross))
    with d3:
        st.metric("Commission", f"−${comm:.2f}")
    with d4:
        st.metric("Trades", trades)
    with d5:
        wr = f"{wins/trades:.0%}" if trades else "—"
        st.metric("Win Rate", wr)

    st.divider()

    # ── Opening range ──────────────────────────────────────────────────────────
    rng_high = status.get("range_high")
    rng_low = status.get("range_low")
    rng_width = status.get("range_width")

    if rng_high and rng_low:
        st.markdown("#### Opening Range (09:30–10:00)")
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            st.metric("Range High", f"{rng_high:.2f}")
        with r2:
            st.metric("Range Low", f"{rng_low:.2f}")
        with r3:
            st.metric("Width", f"{rng_width:.2f} pts" if rng_width else "—")
        with r4:
            vwap = status.get("vwap")
            st.metric("Session VWAP", f"{vwap:.2f}" if vwap else "—")
        st.divider()

    # ── Open position ──────────────────────────────────────────────────────────
    in_trade = state in ("IN_TRADE_LONG", "IN_TRADE_SHORT")

    if in_trade:
        direction = status.get("direction", "")
        entry = status.get("entry_price")
        stop = status.get("stop_price")
        target = status.get("target_price")
        unreal_pts = status.get("unrealized_pnl_pts")
        unreal_usd = status.get("unrealized_pnl_usd")
        bars_held = status.get("bars_in_trade", 0)

        dir_color = "#2ecc71" if direction == "LONG" else "#e74c3c"
        st.markdown(
            f"#### Open Position &nbsp; "
            f'<span style="color:{dir_color};font-weight:bold">{direction}</span>',
            unsafe_allow_html=True,
        )

        p1, p2, p3, p4, p5 = st.columns(5)
        with p1:
            st.metric("Entry", f"{entry:.2f}" if entry else "—")
        with p2:
            st.metric("Stop", f"{stop:.2f}" if stop else "—")
        with p3:
            st.metric("Target", f"{target:.2f}" if target else "—")
        with p4:
            st.metric(
                "Unrealized",
                _fmt_usd(unreal_usd),
                delta=_fmt_pts(unreal_pts),
            )
        with p5:
            st.metric("Bars Held", bars_held)

        # Visual progress bar: where is price between stop and target?
        if entry and stop and target and unreal_pts is not None:
            price_now = (entry + unreal_pts) if direction == "LONG" else (entry - unreal_pts)
            total_range = target - stop
            progress = (price_now - stop) / total_range if total_range else 0.5
            progress = max(0.0, min(1.0, progress))
            st.markdown("**Price position** (stop → target)")
            st.progress(progress)
            st.markdown(
                f'<span style="color:#e74c3c">Stop {stop:.2f}</span> &nbsp;·&nbsp; '
                f'<span style="color:#f39c12">Now {price_now:.2f}</span> &nbsp;·&nbsp; '
                f'<span style="color:#2ecc71">Target {target:.2f}</span>',
                unsafe_allow_html=True,
            )
    else:
        st.info(f"No open position. State: **{state}**")
        halt_reason = status.get("halt_reason")
        if halt_reason:
            st.warning(f"Halt reason: {halt_reason}")

    # ── Context strip ──────────────────────────────────────────────────────────
    st.divider()
    cx1, cx2, cx3 = st.columns(3)
    with cx1:
        vix = status.get("vix")
        st.metric("VIX", f"{vix:.1f}" if vix else "—")
    with cx2:
        st.metric("Contracts", status.get("contracts", 4))
    with cx3:
        st.metric("Symbol", status.get("symbol", "MES"))


with tab_live:
    _render_live_tab()


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — TODAY'S TRADES
# ════════════════════════════════════════════════════════════════════════════════

with tab_today:
    trades = _get_today_trades()

    date_str = datetime.now(ET).strftime("%A %B %-d, %Y")
    st.markdown(f"#### Trades — {date_str}")

    if not trades:
        st.info("No completed trades today.")
    else:
        # Summary strip
        wins_t = [t for t in trades if t.get("outcome") == "WIN"]
        total_net = sum(t.get("net_usd", 0) for t in trades)
        t1, t2, t3, t4 = st.columns(4)
        with t1:
            st.metric("Trades", len(trades))
        with t2:
            st.metric("Wins / Losses", f"{len(wins_t)} / {len(trades) - len(wins_t)}")
        with t3:
            st.metric("Net P&L", _fmt_usd(total_net))
        with t4:
            wr = f"{len(wins_t)/len(trades):.0%}"
            st.metric("Win Rate", wr)

        st.divider()

        # Trade cards
        for i, t in enumerate(reversed(trades)):
            outcome = t.get("outcome", "")
            net = t.get("net_usd", 0)
            direction = t.get("direction", "")
            entry_p = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            reason = t.get("exit_reason", "")

            border = "#2ecc71" if outcome == "WIN" else "#e74c3c"
            with st.container():
                st.markdown(
                    f'<div style="border-left:4px solid {border};padding-left:12px;margin-bottom:8px">',
                    unsafe_allow_html=True,
                )
                h1, h2, h3, h4, h5, h6 = st.columns([1, 1, 1, 1, 1.5, 1])
                with h1:
                    dir_c = "#2ecc71" if direction == "LONG" else "#e74c3c"
                    st.markdown(
                        f'<span style="color:{dir_c};font-weight:bold;font-size:1.1em">{direction}</span>',
                        unsafe_allow_html=True,
                    )
                    st.caption(f"{t.get('entry_time','?')} → {t.get('exit_time','?')}")
                with h2:
                    st.metric("Entry", f"{entry_p:.2f}")
                with h3:
                    st.metric("Exit", f"{exit_p:.2f}")
                with h4:
                    pnl_pts = t.get("pnl_pts", 0)
                    st.metric("P&L pts", _fmt_pts(pnl_pts))
                with h5:
                    st.metric("Net USD", _fmt_usd(net))
                with h6:
                    st.markdown(
                        f"**Exit** &nbsp; {_reason_badge(reason)} &nbsp; {_reason_badge(outcome)}",
                        unsafe_allow_html=True,
                    )
                    st.caption(f"Held {t.get('hold_min','?')} min")
                st.markdown("</div>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — HISTORY
# ════════════════════════════════════════════════════════════════════════════════

def _render_history_tab() -> None:
    """
    Renders the History tab content.  Extracted to use `return` instead of
    st.stop() — st.stop() would prevent the auto-refresh JS from injecting.
    """
    summaries = _get_summaries()

    if not summaries:
        st.info("No historical summaries yet. They are written at the end of each trading session.")
        return   # return, not st.stop()

    st.markdown(f"#### Session History ({len(summaries)} days)")

    # Running equity curve
    try:
        import altair as alt
        import pandas as pd

        rows = []
        cum = 0.0
        for s in reversed(summaries):
            net = s.get("total_net_usd", 0) or 0
            cum += net
            rows.append({
                "Date": s["date"],
                "Daily Net ($)": net,
                "Cumulative ($)": cum,
            })
        df = pd.DataFrame(rows)

        c1, c2 = st.columns(2)
        with c1:
            bar = (
                alt.Chart(df)
                .mark_bar()
                .encode(
                    x=alt.X("Date:N", sort=None, title=""),
                    y=alt.Y("Daily Net ($):Q", title="Net P&L ($)"),
                    color=alt.condition(
                        alt.datum["Daily Net ($)"] >= 0,
                        alt.value("#2ecc71"),
                        alt.value("#e74c3c"),
                    ),
                    tooltip=["Date", "Daily Net ($)"],
                )
                .properties(title="Daily Net P&L", height=250)
            )
            st.altair_chart(bar, use_container_width=True)

        with c2:
            line = (
                alt.Chart(df)
                .mark_line(point=True, color="#3498db")
                .encode(
                    x=alt.X("Date:N", sort=None, title=""),
                    y=alt.Y("Cumulative ($):Q", title="Cumulative ($)"),
                    tooltip=["Date", "Cumulative ($)"],
                )
                .properties(title="Equity Curve", height=250)
            )
            st.altair_chart(line, use_container_width=True)

    except ImportError:
        st.warning("Install altair + pandas for charts: pip install altair pandas")

    st.divider()

    # Summary table
    table_rows = []
    for s in summaries:
        n = s.get("total_trades", 0) or 0
        wins_h = s.get("wins", 0) or 0
        wr = f"{wins_h/n:.0%}" if n else "—"
        exits = s.get("exits_by_reason", {})
        exit_str = " · ".join(f"{k}:{v}" for k, v in exits.items()) if exits else "—"
        table_rows.append({
            "Date": s.get("date", ""),
            "Trades": n,
            "W/L": f"{wins_h}/{n - wins_h}",
            "Win %": wr,
            "Net ($)": _fmt_usd(s.get("total_net_usd")),
            "State": s.get("final_state", ""),
            "VIX": f"{s['vix']:.1f}" if s.get("vix") else "—",
            "Range (pts)": f"{s['range_width']:.2f}" if s.get("range_width") else "—",
            "Exits": exit_str,
        })

    try:
        import pandas as pd
        df_tbl = pd.DataFrame(table_rows)
        st.dataframe(df_tbl, use_container_width=True, hide_index=True)
    except ImportError:
        for row in table_rows:
            st.write(row)

    # Aggregate stats
    st.divider()
    st.markdown("#### All-Time Stats")
    all_net = [s.get("total_net_usd", 0) or 0 for s in summaries]
    all_trades = [s.get("total_trades", 0) or 0 for s in summaries]
    all_wins = [s.get("wins", 0) or 0 for s in summaries]
    days_traded = sum(1 for t in all_trades if t > 0)

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        st.metric("Total Sessions", len(summaries))
    with a2:
        st.metric("Days With Trades", days_traded)
    with a3:
        st.metric("Total Net P&L", _fmt_usd(sum(all_net)))
    with a4:
        tw = sum(all_wins)
        tt = sum(all_trades)
        st.metric("Overall Win Rate", f"{tw/tt:.0%}" if tt else "—")
    with a5:
        avg_day = sum(all_net) / len(summaries) if summaries else 0
        st.metric("Avg Daily Net", _fmt_usd(avg_day))


with tab_history:
    _render_history_tab()


# ── Auto-refresh ───────────────────────────────────────────────────────────────
# Streamlit re-runs the script on user interaction; for polling we use
# st.rerun() on a timer embedded in the page via JavaScript.

st.markdown(
    """
    <script>
    setTimeout(function() { window.location.reload(); }, 10000);
    </script>
    """,
    unsafe_allow_html=True,
)
