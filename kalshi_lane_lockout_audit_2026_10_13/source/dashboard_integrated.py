"""Kalshi Unbroken-Favorite Bot — Streamlit dashboard.

Visual style mirrors webull_bot/dashboard.py (light theme, grid strip,
KPI tiles, Primer-ish palette, Inter + JetBrains Mono).
Reads state/summary.json, state/trades.jsonl, state/run.log, config.yaml.

Run:  streamlit run dashboard.py --server.port 8506
"""
from __future__ import annotations

import html
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))


@st.cache_data(ttl=120, show_spinner=False)
def live_gas():
    """Open KXAAAGASD strikes with the gas lane's own entry test applied.

    Rule from config.yaml gas_lane: favourite mid 0.95-0.99, final 1-8 hours,
    spread <= 2c, ask <= 0.985, 5 contracts, max 4 positions.

    NOTE these strikes all settle on ONE AAA print, so unlike rain's cities
    they are not independent -- four open gas positions are four slices of a
    single settlement shock, which is why the lane caps at 4 and shares one
    $20 group loss limit.
    """
    import json as _j, urllib.request as _u, time as _t
    from datetime import datetime as _dt
    out = []
    d = None
    for _try in range(3):
        try:
            rq = _u.Request("https://api.elections.kalshi.com/trade-api/v2/"
                            "markets?status=open&limit=200"
                            "&series_ticker=KXAAAGASD",
                            headers={"User-Agent": "dash/1.0"})
            with _u.urlopen(rq, timeout=12) as r:
                d = _j.load(r)
            break
        except Exception:                                   # noqa: BLE001
            _t.sleep(0.6)
    if d is None:
        return [{"t": "", "strike": "—", "hrs": -1.0, "b": 0.0, "a": 0.0,
                 "side": "—", "fb": 0.0, "fa": 0.0, "fm": 0.0,
                 "why": ["FETCH FAILED — quote unavailable"]}]
    for m in d.get("markets", []) or []:
        try:
            b = float(m.get("yes_bid_dollars"))
            a = float(m.get("yes_ask_dollars"))
        except (TypeError, ValueError):
            continue
        if not (0 < b <= a < 1):
            continue
        try:
            ct = _dt.fromisoformat(
                str(m.get("close_time")).replace("Z", "+00:00")).timestamp()
        except Exception:                                   # noqa: BLE001
            continue
        hrs = (ct - _t.time()) / 3600.0
        if hrs < 0 or hrs > 24:
            continue
        mid = (b + a) / 2
        if mid > 0.5:
            side, fb, fa, fm = "YES", b, a, mid
        else:
            side, fb, fa, fm = "NO", 1 - a, 1 - b, 1 - mid
        why = []
        if not (1 <= hrs <= 8):
            why.append("%.1fh \u2209 1-8h" % hrs)
        if not (0.95 <= fm < 0.99):
            why.append("fav %.1f\u00a2 \u2209 95-99" % (fm * 100))
        if fa - fb > 0.02 + 1e-9:
            why.append("spread %.0f\u00a2 >2\u00a2" % ((fa - fb) * 100))
        if fa > 0.985:
            why.append("ask >98.5\u00a2")
        out.append({"t": m.get("ticker", ""),
                    "strike": m.get("ticker", "").split("-")[-1],
                    "hrs": hrs, "b": b, "a": a, "side": side,
                    "fb": fb, "fa": fa, "fm": fm, "why": why})
    return sorted(out, key=lambda x: (x["hrs"], x["strike"]))


@st.cache_data(ttl=120, show_spinner=False)
def live_rain():
    """Open KXRAIN markets with the bot's entry test applied.

    Mirrors rain_bot.py: final 1-8 hours, favourite mid 0.95-0.99, spread
    <=2c. The 2-hour held-in-band history is bot state and can't be recomputed
    from one snapshot, so a market passing price+time reads 'needs 2h held'.
    Cities settle in four timezone groups (00/01/02/03 ET) -- those groups are
    the weather-correlation clusters, so the group is shown per row.
    """
    import json as _j, urllib.request as _u, time as _t
    from datetime import datetime as _dt
    GROUP = {"ATL": "East", "BOS": "East", "DC": "East", "MIA": "East",
             "NYC": "East", "PHIL": "East",
             "AUS": "Ctrl", "CHI": "Ctrl", "DAL": "Ctrl", "HOU": "Ctrl",
             "MIN": "Ctrl", "NOLA": "Ctrl", "OKC": "Ctrl", "SATX": "Ctrl",
             "DEN": "Mtn",
             "LAX": "West", "LV": "West", "PHX": "West", "SEA": "West",
             "SFO": "West"}
    out = []
    d = None
    for _try in range(3):
        try:
            rq = _u.Request("https://api.elections.kalshi.com/trade-api/v2/"
                            "markets?status=open&limit=200"
                            "&series_ticker=KXRAIN",
                            headers={"User-Agent": "dash/1.0"})
            with _u.urlopen(rq, timeout=12) as r:
                d = _j.load(r)
            break
        except Exception:                                   # noqa: BLE001
            _t.sleep(0.6)
    if d is None:
        return [{"t": "", "city": "—", "grp": "—", "hrs": -1.0,
                 "b": 0.0, "a": 0.0, "side": "—", "fb": 0.0, "fa": 0.0,
                 "fm": 0.0, "why": ["FETCH FAILED — quote unavailable"]}]
    for m in d.get("markets", []) or []:
        try:
            b = float(m.get("yes_bid_dollars"))
            a = float(m.get("yes_ask_dollars"))
        except (TypeError, ValueError):
            continue
        if not (0 < b <= a < 1):
            continue
        try:
            ct = _dt.fromisoformat(
                str(m.get("close_time")).replace("Z", "+00:00")).timestamp()
        except Exception:                                   # noqa: BLE001
            continue
        hrs = (ct - _t.time()) / 3600.0
        if hrs < -2 or hrs > 20:
            continue
        mid = (b + a) / 2
        if mid > 0.5:
            side, fb, fa, fm = "YES", b, a, mid
        else:
            side, fb, fa, fm = "NO", 1 - a, 1 - b, 1 - mid
        why = []
        if not (1 <= hrs <= 12):
            why.append("%.1fh \u2209 1-12h" % hrs)
        if not (0.95 <= fm < 0.99):
            why.append("fav %.1f\u00a2 \u2209 95-99" % (fm * 100))
        if fa - fb > 0.02 + 1e-9:
            why.append("spread %.0f\u00a2 >2\u00a2" % ((fa - fb) * 100))
        city = m.get("ticker", "").split("-")[-1]
        out.append({"t": m.get("ticker", ""), "city": city,
                    "grp": GROUP.get(city, "?"), "hrs": hrs,
                    "b": b, "a": a, "side": side, "fb": fb, "fa": fa,
                    "fm": fm, "why": why})
    return sorted(out, key=lambda x: (x["hrs"], x["city"]))


@st.cache_data(ttl=20, show_spinner=False)
def temp_scan_file():
    """The temp bot's own leader board. Reading it costs ~0.1ms; the API
    path below costs ~4.7s of blocking I/O and was the dashboard's main
    freeze. Returns None when the file is missing or stale so the API
    fallback still covers a bot outage."""
    import json as _j, os as _os, time as _t
    p = "/home/varmak/kalshi/kalshi_unbroken_bot/temp/state/scan.json"
    try:
        if _t.time() - _os.path.getmtime(p) > 180:
            return None
        d = _j.load(open(p))
        return d.get("rows") or None
    except Exception:                                       # noqa: BLE001
        return None


@st.cache_data(ttl=120, show_spinner=False)
def live_temp():
    """Open HIGH-temp ladders, 20 cities: current LEADER bracket with the
    temp bot's gates applied (leader mid 95-99c, spread <=2c, ask <=99c,
    final 1-12h). NO stability gate — an ELIGIBLE row is one the bot takes
    on its next 30s poll."""
    import json as _j, urllib.request as _u, time as _t
    from datetime import datetime as _dt
    from concurrent.futures import ThreadPoolExecutor
    CITY = {"KXHIGHNY": "NYC", "KXHIGHCHI": "CHI", "KXHIGHDEN": "DEN",
            "KXHIGHPHIL": "PHIL", "KXHIGHTATL": "ATL", "KXHIGHTPHX": "PHX",
            "KXHIGHLAX": "LAX", "KXHIGHMIA": "MIA", "KXHIGHTBOS": "BOS",
            "KXHIGHTOKC": "OKC", "KXHIGHAUS": "AUS", "KXHIGHTHOU": "HOU",
            "KXHIGHTLV": "LV", "KXHIGHTSFO": "SFO", "KXHIGHTSEA": "SEA",
            "KXHIGHTMIN": "MIN", "KXHIGHTDAL": "DAL", "KXHIGHTDC": "DC",
            "KXHIGHTNOLA": "NOLA", "KXHIGHTSATX": "SATX"}

    # FAST PATH: the temp bot already computed this leader board while
    # polling; reading its file costs ~0.1ms against ~4.7s of blocking
    # API calls here. That 4.7s was the dashboard's main freeze.
    _rows = temp_scan_file()
    if _rows:
        _out = []
        for _r in _rows:
            _ser = _r.get("series")
            if _ser not in CITY:
                continue
            _b, _a = _r.get("bid"), _r.get("ask")
            _mid, _hrs = _r.get("mid"), _r.get("hrs")
            if None in (_b, _a, _mid, _hrs):
                continue
            _why = []
            if not (1 <= _hrs <= 12):
                _why.append("%.1fh \u2209 1-12h" % _hrs)
            if not (0.95 <= _mid < 0.99):
                _why.append("leader %.1f\u00a2 \u2209 95-99" % (_mid * 100))
            if _a - _b > 0.02 + 1e-9:
                _why.append("spread %.0f\u00a2 >2\u00a2" % ((_a - _b) * 100))
            if _a > 0.99 + 1e-9:
                _why.append("ask >99\u00a2")
            _out.append({"t": _r.get("tkr"), "city": CITY[_ser],
                         "brk": (_r.get("tkr") or "").split("-")[-1],
                         "hrs": _hrs, "b": _b, "a": _a, "mid": _mid,
                         "why": _why})
        if _out:
            return sorted(_out,
                          key=lambda x: (x["hrs"] < 0, x["hrs"], x["city"]))


    def fetch(ser):
        # 20 series x 8-way parallel tripped Kalshi throttling and rendered
        # 8 cities as failures; 3-way + jittered backoff stays under it
        for _try in range(4):
            try:
                rq = _u.Request(
                    "https://api.elections.kalshi.com/trade-api/v2/markets"
                    f"?status=open&limit=100&series_ticker={ser}",
                    headers={"User-Agent": "dash/1.0"})
                with _u.urlopen(rq, timeout=10) as r:
                    return ser, _j.load(r)
            except Exception:                               # noqa: BLE001
                _t.sleep(0.5 * (_try + 1))
        return ser, None

    out = []
    with ThreadPoolExecutor(3) as ex:
        for ser, d in ex.map(fetch, list(CITY)):
            if d is None:
                out.append({"t": "", "city": CITY[ser], "brk": "—",
                            "hrs": -1.0, "b": 0.0, "a": 0.0, "mid": 0.0,
                            "why": ["FETCH FAILED — quote unavailable"]})
                continue
            ladders, closes = {}, {}
            for m in d.get("markets", []) or []:
                try:
                    b = float(m.get("yes_bid_dollars"))
                    a = float(m.get("yes_ask_dollars"))
                    ct = _dt.fromisoformat(str(m.get("close_time"))
                                           .replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    continue
                # display filter, NOT the bot's entry filter: a locked
                # ladder quotes 99/100 with 0/1 elsewhere -- keep it visible
                # as 'ask >99c' instead of vanishing. Only drop empty books.
                if not (0 <= b <= a <= 1) or (b <= 0 and a >= 1):
                    continue
                parts = str(m.get("ticker", "")).split("-")
                if len(parts) < 3:
                    continue
                ladders.setdefault(parts[1], {})[m["ticker"]] = (b, a)
                closes[parts[1]] = ct
            for daykey, quotes in ladders.items():
                best = None
                for tkr, (b, a) in quotes.items():
                    mid = (b + a) / 2
                    if best is None or mid > best[3]:
                        best = (tkr, b, a, mid)
                tkr, b, a, mid = best
                hrs = (closes[daykey] - _t.time()) / 3600.0
                if hrs < -2 or hrs > 20:
                    continue
                why = []
                if not (1 <= hrs <= 12):
                    why.append("%.1fh \u2209 1-12h" % hrs)
                if not (0.95 <= mid < 0.99):
                    why.append("leader %.1f\u00a2 \u2209 95-99" % (mid * 100))
                if a - b > 0.02 + 1e-9:
                    why.append("spread %.0f\u00a2 >2\u00a2" % ((a - b) * 100))
                if a > 0.99 + 1e-9:
                    why.append("ask >99\u00a2")
                out.append({"t": tkr, "city": CITY[ser],
                            "brk": tkr.split("-")[-1], "hrs": hrs,
                            "b": b, "a": a, "mid": mid, "why": why})
    # failures LAST (hrs -1 was sorting them to the top of the table)
    return sorted(out, key=lambda x: (x["hrs"] < 0, x["hrs"], x["city"]))


@st.cache_data(ttl=30, show_spinner=False)
def live_metals():
    """Open KXGOLD15M / KXSILVER15M with quotes, depth, minutes left, and
    the bot's own entry test applied so the panel explains itself.

    Mirrors the price/time portion of metals15m_bot.py: the shared core is
    rem 4-12, favourite mid 0.93-0.99 and ask <= 0.98; Silver also has an
    earlier rem 9-12, 0.90-0.93, ask <= 0.94 lane. Both require a <=1c
    spread. The unbroken history is bot state and is shown as 'needs history'.
    """
    import json as _j, urllib.request as _u, time as _t
    from datetime import datetime as _dt
    out = []
    # the 3 series were fetched SEQUENTIALLY (~880ms); 3-way parallel
    # brings it to ~300ms. Well under the throttling limit that forced
    # live_temp down to 3 workers for its 20 series.
    from concurrent.futures import ThreadPoolExecutor as _TPE
    _pre = {}
    def _grab(_s):
        for _t2 in range(3):
            try:
                _rq = _u.Request(
                    "https://api.elections.kalshi.com/trade-api/v2/markets"
                    f"?status=open&limit=50&series_ticker={_s}",
                    headers={"User-Agent": "dash/1.0"})
                with _u.urlopen(_rq, timeout=12) as _r:
                    return _s, _j.load(_r)
            except Exception:                               # noqa: BLE001
                _t.sleep(0.4 * (_t2 + 1))
        return _s, None
    with _TPE(3) as _ex:
        for _s, _dd in _ex.map(_grab, ("KXGOLD15M", "KXSILVER15M",
                                       "KXWTI15M")):
            _pre[_s] = _dd
    for ser in ("KXGOLD15M", "KXSILVER15M", "KXWTI15M"):
        # A silent `continue` here made a timed-out fetch indistinguishable
        # from "this series has no open markets" -- gold simply disappeared
        # from the board with no explanation. Failures now render a row.
        d = None
        for _try in range(3):
            try:
                rq = _u.Request(
                    "https://api.elections.kalshi.com/trade-api/v2/markets"
                    f"?status=open&limit=50&series_ticker={ser}",
                    headers={"User-Agent": "dash/1.0"})
                with _u.urlopen(rq, timeout=12) as r:
                    d = _j.load(r)
                break
            except Exception:                               # noqa: BLE001
                _t.sleep(0.6)
        if d is None:
            out.append({"t": "", "ser": ser, "b": 0.0, "a": 0.0,
                        "side": "—", "fb": 0.0, "fa": 0.0, "fm": 0.0,
                        "sz": None, "rem": -1.0, "vol": None,
                        "why": ["FETCH FAILED — quote unavailable, not a "
                                "signal decision"], "lane": ""})
            continue
        for m in d.get("markets", []) or []:
            try:
                b = float(m.get("yes_bid_dollars"))
                a = float(m.get("yes_ask_dollars"))
            except (TypeError, ValueError):
                continue
            if not (0 < b <= a < 1):
                continue
            try:
                ct = _dt.fromisoformat(
                    str(m.get("close_time")).replace("Z", "+00:00")).timestamp()
            except Exception:                               # noqa: BLE001
                continue
            rem = (ct - _t.time()) / 60.0
            # Kalshi keeps a 15-min market at status=open while it settles, so
            # without this the row hangs on the board with a negative clock
            # long after the bet has cleared. Anything more than 2 min past
            # close is gone; the brief window in between is shown as SETTLING
            # so a just-closed market does not vanish mid-glance.
            if rem < -2:
                continue
            mid = (b + a) / 2
            if mid > 0.5:
                side, fb, fa, fm = "YES", b, a, mid
                sz = m.get("yes_ask_size_fp")
            else:
                side, fb, fa, fm = "NO", 1 - a, 1 - b, 1 - mid
                sz = m.get("yes_bid_size_fp")
            if ser == "KXWTI15M":
                # WTI pilot band: 93-97 only (user 2026-08-10)
                core = 4 <= rem <= 12 and 0.93 <= fm < 0.97 and fa <= 0.9699
            else:
                core = 4 <= rem <= 12 and 0.93 <= fm < 0.99 and fa <= 0.98
            early = (ser == "KXSILVER15M" and 9 <= rem <= 12
                     and 0.90 <= fm < 0.93 and fa <= 0.94)
            lane = "silver early" if early else ("core" if core else "")
            # Per-lane verdicts. The old version printed only ONE reason and
            # stopped at the time check, so a Silver market at mid 93.5c with
            # 12.5 min left read "rem 12.5 outside 4-12" -- implying time was
            # the only obstacle, when its price ALSO failed the early band
            # (needs <93c). Each lane now reports its own blocker.
            why = []
            if not core and not early:
                c = []
                if not (4 <= rem <= 12):
                    c.append("rem %.1f\u2209 4-12" % rem)
                _bhi, _ahi = ((0.97, 0.9699) if ser == "KXWTI15M"
                              else (0.99, 0.98))
                if not (0.93 <= fm < _bhi):
                    c.append("fav %.1f\u00a2 \u2209 93-%d"
                             % (fm * 100, int(_bhi * 100)))
                if fa > _ahi:
                    c.append("ask >%d\u00a2" % int(round(_ahi * 100)))
                why.append("core: " + ", ".join(c))
                if ser == "KXSILVER15M":
                    e = []
                    if not (9 <= rem <= 12):
                        e.append("rem %.1f\u2209 9-12" % rem)
                    if not (0.90 <= fm < 0.93):
                        e.append("fav %.1f\u00a2 \u2209 90-93" % (fm * 100))
                    if fa > 0.94:
                        e.append("ask >94\u00a2")
                    why.append("early: " + ", ".join(e))
            if fa - fb > 0.01 + 1e-9:
                why.append("spread %.0f\u00a2 (both lanes need \u22641\u00a2)"
                           % ((fa - fb) * 100))
            out.append({"t": m.get("ticker", ""), "ser": ser, "b": b, "a": a,
                        "side": side, "fb": fb, "fa": fa, "fm": fm,
                        "sz": float(sz) if sz not in (None, "") else None,
                        "rem": rem, "vol": m.get("volume_fp"),
                        "why": why, "lane": lane})
    return sorted(out, key=lambda x: x["rem"])


@st.cache_data(ttl=20, show_spinner=False)
def live_balance():
    """(cash, positions_value, total) straight from Kalshi. None on failure.

    The app's headline PREDICTIONS figure is cash + positions. summary.json
    carries only the cash leg, so reading it alone understated the number.
    """
    try:
        from kalshi_client import Credentials, KalshiClientV2
        c = KalshiClientV2(creds=Credentials.load())
        b = c._request("GET", "/portfolio/balance")
        cash = float(b.get("balance_dollars") or 0)
        pos = float(b.get("portfolio_value") or 0) / 100.0
        return cash, pos, cash + pos
    except Exception:                                       # noqa: BLE001
        return None
import streamlit.components.v1 as components
import yaml

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
SPORTS_STATE = ("/Users/varmakammili/Documents/GitHub/StocksTradingTest"
                "/kalshi_sports_2026_08/state")
if not Path(SPORTS_STATE).is_dir():                 # Ubuntu repo layout
    SPORTS_STATE = str(HERE / "sports" / "state")
STATE = HERE / "state"

st.set_page_config(page_title="Kalshi Unbroken Bot", page_icon="🎯",
                   layout="wide", initial_sidebar_state="collapsed")

# ── palette / CSS mirrored from webull_bot/dashboard.py ─────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
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

.bot-card { display:flex; align-items:center; gap:12px; background:#fff;
  border:1px solid var(--bd); border-radius:8px; padding:12px 18px;
  margin-bottom:10px; box-shadow:var(--shadow-sm); }
.tb-icon { width:32px; height:32px; border-radius:7px; flex-shrink:0;
  background:linear-gradient(135deg,#0969da 0%,#0d7377 100%);
  display:flex; align-items:center; justify-content:center; font-size:15px; color:#fff; }
.tb-name { font-size:14px; font-weight:700; color:var(--t1); letter-spacing:-.2px; }
.tb-sub { font-size:11px; color:var(--t3); margin-top:2px; }
.tb-right { margin-left:auto; display:flex; align-items:center; gap:8px; }

.b { display:inline-flex; align-items:center; gap:4px; padding:2px 8px;
  border-radius:4px; font-size:10px; font-weight:700; letter-spacing:.5px;
  text-transform:uppercase; border:1px solid transparent; white-space:nowrap;
  vertical-align:middle; line-height:1; }
.b-grn { color:var(--grn); background:var(--grn-bg); border-color:rgba(26,127,55,.25); }
.b-blu { color:var(--blu); background:var(--blu-bg); border-color:rgba(9,105,218,.25); }
.b-red { color:var(--red); background:var(--red-bg); border-color:rgba(207,34,46,.25); }
/* A static grey 'waiting' made a live lane look like a frozen record. This
   badge breathes so the lane reads as armed-and-watching, not stalled. */
.b-live { color:#9a6700; background:#fff8c5; border-color:rgba(154,103,0,.30);
          animation: brea 2.4s ease-in-out infinite; }
.b-live .dot { width:6px; height:6px; border-radius:50%; background:#9a6700;
               display:inline-block; animation: brea 2.4s ease-in-out infinite; }
@keyframes brea { 0%,100% { opacity:1 } 50% { opacity:.35 } }
.b-amb { color:var(--amb); background:var(--amb-bg); border-color:rgba(154,103,0,.25); }
.b-dim { color:var(--t2); background:var(--p2); border-color:var(--bd); }
.dot { width:5px; height:5px; border-radius:50%; display:inline-block; }
.dg { background:var(--grn); animation:pl 2s infinite; } .dr { background:var(--red); }
@keyframes pl { 0%,100%{opacity:1} 50%{opacity:.3} }
.et-clock { font-family:var(--mono); font-size:11px; color:var(--t2);
  background:var(--p2); border:1px solid var(--bd); border-radius:5px;
  padding:4px 10px; letter-spacing:.4px; }

.strip { display:grid; grid-template-columns:repeat(7,1fr); gap:1px;
  background:var(--bd); border:1px solid var(--bd); border-radius:8px;
  overflow:hidden; margin-bottom:12px; box-shadow:var(--shadow-sm); }
.sc { background:var(--p1); padding:10px 14px; }
.sl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:4px; }
.sv { font-family:var(--mono); font-size:13px; font-weight:500; color:var(--t1); }
.sv-big { font-family:var(--mono); font-size:18px; font-weight:700; color:var(--t1); line-height:1.1; }
.ss { font-size:10px; color:var(--t3); margin-top:3px; }
.vg { color:var(--grn) !important; } .vr { color:var(--red) !important; }
.va { color:var(--amb) !important; } .vb { color:var(--blu) !important; }

.sr { display:flex; align-items:center; gap:8px; margin:16px 0 6px; }
.sr span { font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; color:var(--t3); white-space:nowrap; }
.sr hr { flex:1; border:none; border-top:1px solid var(--bd); }

.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:10px; }
.kpi { background:var(--p1); border:1px solid var(--bd); border-top:2px solid var(--bd2);
  border-radius:8px; padding:12px 14px; box-shadow:var(--shadow-sm); }
.kpi.kg { border-top-color:var(--grn); } .kpi.kr { border-top-color:var(--red); }
.kpi.kb { border-top-color:var(--blu); } .kpi.ka { border-top-color:var(--amb); }
.kl { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase; color:var(--t3); margin-bottom:8px; }
.kv { font-family:var(--mono); font-size:20px; font-weight:500; color:var(--t1); line-height:1; }
.ks { font-size:10px; color:var(--t3); margin-top:5px; }

.panel { background:var(--p1); border:1px solid var(--bd); border-radius:8px;
  padding:12px 16px; box-shadow:var(--shadow-sm); margin-bottom:10px; }
table.tt { width:100%; border-collapse:collapse; }
table.tt th { font-size:9px; font-weight:700; letter-spacing:.8px; text-transform:uppercase;
  color:var(--t3); text-align:left; padding:4px 8px; border-bottom:1px solid var(--bd); }
table.tt td { font-family:var(--mono); font-size:11.5px; padding:5px 8px;
  border-bottom:1px solid var(--p3); color:var(--t1); }
[data-testid="stRadio"] p { color:var(--t1) !important; font-size:12px !important;
  font-weight:600; }
[data-testid="stExpander"] { background:var(--p1); border:1px solid var(--bd);
  border-radius:8px; margin-bottom:10px; }
[data-testid="stExpander"] summary { padding:8px 12px; }
[data-testid="stExpander"] summary p, [data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary div { color:var(--t2) !important;
  font-size:11px !important; font-weight:600 !important; }
[data-testid="stExpander"] summary:hover p { color:var(--blu) !important; }
[data-testid="stExpander"] svg { fill:var(--t3); }
[data-testid="stRadio"] { margin-bottom:-6px; }
.logbox { font-family:var(--mono); font-size:10.5px; color:var(--t2);
  background:var(--p2); border:1px solid var(--bd); border-radius:6px;
  padding:10px 12px; white-space:pre-wrap; word-break:break-all; line-height:1.55; }
</style>
""", unsafe_allow_html=True)


def load_state():
    summary, trades, loglines, cfg = {}, [], [], {}
    try:
        summary = json.loads((STATE / "summary.json").read_text())
    except Exception:
        pass
    try:
        trades = [json.loads(l) for l in (STATE / "trades.jsonl").read_text().splitlines() if l.strip()]
    except Exception:
        pass
    try:
        loglines = (STATE / "run.log").read_text().splitlines()[-12:]
    except Exception:
        pass
    try:
        cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    except Exception:
        pass
    return summary, trades, loglines, cfg


def live_intervals():
    """Current open 15M markets: favorite mid + band status per asset."""
    import urllib.request, calendar
    out = []
    now = time.time()
    for s in ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M"):
        try:
            req = urllib.request.Request(
                f"https://api.elections.kalshi.com/trade-api/v2/markets?"
                f"series_ticker={s}&status=open&limit=3",
                headers={"User-Agent": "dashboard/1.0"})
            d = json.load(urllib.request.urlopen(req, timeout=6))
            for m in d.get("markets", []):
                ct = calendar.timegm(time.strptime(m["close_time"],
                                                   "%Y-%m-%dT%H:%M:%SZ"))
                rem = (ct - now) / 60
                if 0 < rem < 16:
                    bid = float(m.get("yes_bid_dollars") or 0)
                    ask = float(m.get("yes_ask_dollars") or 1)
                    fav = max((bid + ask) / 2, 1 - (bid + ask) / 2)
                    # flip check from minute-close candles (mirrors the bot)
                    flipped = False
                    held = 0
                    try:
                        req2 = urllib.request.Request(
                            f"https://api.elections.kalshi.com/trade-api/v2/"
                            f"series/{s}/markets/{m['ticker']}/candlesticks?"
                            f"start_ts={ct-900}&end_ts={ct}&period_interval=1",
                            headers={"User-Agent": "dashboard/1.0"})
                        cs = json.load(urllib.request.urlopen(req2, timeout=6)
                                       ).get("candlesticks", [])
                        sides = []
                        for c in cs:
                            b2 = c["yes_bid"].get("close_dollars")
                            a2 = c["yes_ask"].get("close_dollars")
                            if not b2 or not a2:
                                continue
                            sides.append("yes" if (float(b2) + float(a2)) / 2
                                         > 0.5 else "no")
                        flipped = any(x != y for x, y in zip(sides, sides[1:]))
                        for sd in reversed(sides):   # current leader's streak
                            if sd == sides[-1]:
                                held += 1
                            else:
                                break
                    except Exception:
                        pass
                    out.append((s.replace("KX", "").replace("15M", ""),
                                rem, fav, 0.90 <= fav < 0.99, flipped, held))
                    break
        except Exception:
            out.append((s.replace("KX", "").replace("15M", ""), None, None,
                        False, False, 0))
    return out


def bot_process_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "bot.py --live"],
                             capture_output=True, text=True, timeout=3)
        return bool(out.stdout.strip())
    except Exception:
        return False


def money(v, plus=False):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if (plus and v > 0) else ""
    return f"{sign}${v:,.2f}" if v >= 0 or not plus else f"-${abs(v):,.2f}"


def pnl_class(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return "vg" if v > 0 else ("vr" if v < 0 else "")


def et_time(ts):
    return datetime.fromtimestamp(ts, ET).strftime("%m-%d %H:%M")


def equity_chart_html(settled, rng="Today"):
    """Range-aware P&L chart (Today / 1W / All). Each window's curve starts
    at $0 at the window's left edge and runs to NOW at the right edge, so
    the day view grows as the day progresses. ET-aligned time ticks,
    $ gridlines, loss markers, crosshair tooltip."""
    from datetime import timedelta
    import math as _m
    now = time.time()
    rows = sorted(settled, key=lambda t: t.get("ts", 0))
    if rng == "Today":
        x0 = datetime.now(ET).replace(hour=0, minute=0, second=0,
                                      microsecond=0).timestamp()
    elif rng == "1W":
        x0 = now - 7 * 86400
    else:
        x0 = min((t.get("ts", now) for t in rows), default=now - 3600) - 60
    rows = [t for t in rows if t.get("ts", 0) >= x0]
    if not rows:
        return None
    x1 = now
    pts, eq = [(x0, 0.0, 0.0, "", "")], 0.0        # window opens at $0
    for t in rows:
        eq += t.get("pnl", 0)
        pts.append((t.get("ts", 0), eq, t.get("pnl", 0),
                    t.get("result", ""), t.get("lane", "r1")))
    W, H, ML, MR, MT, MB = 1400, 228, 56, 16, 16, 26
    ys = [p[1] for p in pts]
    y0, y1 = min(min(ys), 0), max(max(ys), 0)
    ypad = (y1 - y0) * 0.12 or 0.5
    y0 -= ypad; y1 += ypad
    xr = (x1 - x0) or 1; yr = y1 - y0
    def X(x): return ML + (x - x0) / xr * (W - ML - MR)
    def Y(y): return MT + (y1 - y) / yr * (H - MT - MB)
    # \u2500\u2500 y gridlines: nice $ steps, ~5 lines, every line labeled \u2500\u2500
    tgt = yr / 5
    mag = 10 ** _m.floor(_m.log10(tgt))
    step = next((s for s in (mag, 2*mag, 2.5*mag, 5*mag, 10*mag) if s >= tgt), mag)
    grid = []
    v = _m.ceil(y0 / step) * step
    while v <= y1:
        yy = Y(v)
        if abs(v) > 1e-9:
            grid.append(f"<line x1='{ML}' y1='{yy:.1f}' x2='{W-MR}' y2='{yy:.1f}' "
                        f"stroke='#eaeef2' stroke-width='1'/>"
                        f"<text x='{ML-6}' y='{yy+3.5:.1f}' text-anchor='end' "
                        f"font-size='10' fill='#57606a'>"
                        f"{'+' if v > 0 else '\u2212'}${abs(v):.2f}</text>")
        v += step
    zero = Y(0)
    grid.append(f"<line x1='{ML}' y1='{zero:.1f}' x2='{W-MR}' y2='{zero:.1f}' "
                f"stroke='#afb8c1' stroke-width='1'/>"
                f"<text x='{ML-6}' y='{zero+3.5:.1f}' text-anchor='end' "
                f"font-size='10' fill='#57606a'>$0</text>")
    # \u2500\u2500 x time ticks: ET-aligned, step picked from the window span \u2500\u2500
    span = x1 - x0
    if span <= 26 * 3600:
        tstep, tfmt = timedelta(hours=3), "%H:%M"
    elif span <= 3.5 * 86400:
        tstep, tfmt = timedelta(hours=12), "%H:%M"
    elif span <= 9 * 86400:
        tstep, tfmt = timedelta(days=1), "%m-%d"
    else:
        tstep, tfmt = timedelta(days=7), "%m-%d"
    tick = datetime.fromtimestamp(x0, ET).replace(hour=0, minute=0, second=0,
                                                  microsecond=0)
    last_lx = -1e9
    while tick.timestamp() <= x1:
        ts_ = tick.timestamp()
        if ts_ >= x0:
            bx = X(ts_)
            mid = tick.hour == 0 and tick.minute == 0
            dash = " stroke-dasharray='3,3'" if mid else ""
            grid.append(f"<line x1='{bx:.1f}' y1='{MT}' x2='{bx:.1f}' "
                        f"y2='{H-MB}' stroke='#eaeef2' stroke-width='1'{dash}/>")
            lab = tick.strftime("%m-%d" if mid else tfmt)
            if bx - last_lx > 60 and bx < W - MR - 110:   # keep clear of 'now'
                grid.append(f"<text x='{bx+4:.1f}' y='{H-8}' font-size='10' "
                            f"fill='{'#1f2328' if mid else '#57606a'}'>{lab}</text>")
                last_lx = bx
        tick += tstep
    grid.append(f"<text x='{W-MR}' y='{H-8}' text-anchor='end' font-size='10' "
                f"fill='#57606a'>now {datetime.now(ET).strftime('%H:%M')} ET</text>")
    line = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}"
                    for i, (x, y, *_ ) in enumerate(pts))
    lx, lyv = pts[-1][0], pts[-1][1]
    area = (line + f" L{X(lx):.1f},{zero:.1f} L{X(x0):.1f},{zero:.1f} Z")
    # dotted continuation from last settle to the live right edge
    cont = (f"<line x1='{X(lx):.1f}' y1='{Y(lyv):.1f}' x2='{W-MR}' "
            f"y2='{Y(lyv):.1f}' stroke='#0969da' stroke-width='1.5' "
            f"stroke-dasharray='2,4' opacity='0.6'/>")
    losses = "".join(f"<circle cx='{X(x):.1f}' cy='{Y(y):.1f}' r='3.5' "
                     f"fill='#cf222e' stroke='#fff' stroke-width='1.5'/>"
                     for x, y, p, r, _l in pts if r == "loss")
    endv = ys[-1]
    endcol = "#1a7f37" if endv > 0 else ("#cf222e" if endv < 0 else "#57606a")
    endlab = (f"<text x='{W-MR-4}' y='{Y(lyv)-8:.1f}' text-anchor='end' "
              f"font-size='12' font-weight='700' fill='{endcol}'>${endv:+.2f}</text>")
    data = json.dumps([[round(X(x), 1), round(Y(y), 1), et_time(x), round(y, 2),
                        round(p, 2), r, l] for x, y, p, r, l in pts])
    svg = (f"<svg id='eq' viewBox='0 0 {W} {H}' style='width:100%;height:auto;"
           f"display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace'>"
           f"{''.join(grid)}"
           f"<defs><linearGradient id='ag' x1='0' y1='0' x2='0' y2='1'>"
           f"<stop offset='0' stop-color='#0969da' stop-opacity='0.16'/>"
           f"<stop offset='1' stop-color='#0969da' stop-opacity='0.02'/>"
           f"</linearGradient></defs>"
           f"<path d='{area}' fill='url(#ag)' stroke='none'/>"
           f"<path d='{line}' fill='none' stroke='#0969da' stroke-width='2' "
           f"stroke-linejoin='round' stroke-linecap='round'/>"
           f"{cont}{losses}{endlab}"
           f"<line id='ch' x1='0' y1='{MT}' x2='0' y2='{H-MB}' stroke='#57606a' "
           f"stroke-width='1' stroke-dasharray='2,2' visibility='hidden'/>"
           f"<circle id='cp' r='4' fill='#0969da' stroke='#fff' stroke-width='1.5' "
           f"visibility='hidden'/>"
           f"<rect x='{ML}' y='{MT}' width='{W-ML-MR}' height='{H-MT-MB}' "
           f"fill='transparent' style='cursor:crosshair'/></svg>")
    js = """
<script>
const D = __DATA__;
const svg = document.getElementById('eq');
const ch = document.getElementById('ch'), cp = document.getElementById('cp');
const tip = document.getElementById('tip');
svg.addEventListener('mousemove', ev => {
  const r = svg.getBoundingClientRect();
  const mx = (ev.clientX - r.left) * (1400 / r.width);
  let best = 0, bd = 1e9;
  for (let i = 0; i < D.length; i++) {
    const d = Math.abs(D[i][0] - mx);
    if (d < bd) { bd = d; best = i; }
  }
  const p = D[best];
  ch.setAttribute('x1', p[0]); ch.setAttribute('x2', p[0]);
  cp.setAttribute('cx', p[0]); cp.setAttribute('cy', p[1]);
  ch.setAttribute('visibility', 'visible'); cp.setAttribute('visibility', 'visible');
  const sign = p[4] >= 0 ? '+' : '';
  tip.innerHTML = '<b>$' + (p[3] >= 0 ? '+' : '') + p[3].toFixed(2) + '</b> · '
    + p[2] + ' ET<br>' + (p[5] ? p[6] + ' ' + p[5] + ' ' + sign + p[4].toFixed(2)
                               : 'window start');
  tip.style.display = 'block';
  const px = ev.clientX - r.left, flip = px > r.width - 170;
  tip.style.left = (flip ? px - 165 : px + 12) + 'px';
  tip.style.top = Math.max(2, ev.clientY - r.top - 40) + 'px';
});
svg.addEventListener('mouseleave', () => {
  ch.setAttribute('visibility', 'hidden'); cp.setAttribute('visibility', 'hidden');
  tip.style.display = 'none';
});
</script>""".replace("__DATA__", data)
    return ("<div style='position:relative;border:1px solid #d0d7de;"
            "border-radius:8px;background:#ffffff;padding:10px 12px 6px'>"
            "<div id='tip' style='display:none;position:absolute;z-index:9;"
            "background:#ffffff;border:1px solid #d0d7de;border-radius:6px;"
            "box-shadow:0 3px 10px rgba(31,35,40,.12);padding:6px 9px;"
            "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;"
            "color:#1f2328;pointer-events:none;white-space:nowrap'></div>"
            + svg + js + "</div>")


def daily_chart_html(settled):
    """Daily realized P&L bars, equity-chart styling: $ gridlines, date axis,
    value labels, W/L tooltip. Self-contained for components.html."""
    import math as _m
    by_day = {}
    for t in settled:
        d = t.get("settled_day", "?")
        st_ = by_day.setdefault(d, [0.0, 0, 0])
        st_[0] += t.get("pnl", 0)
        st_[1] += 1 if t["result"] == "win" else 0
        st_[2] += 1 if t["result"] == "loss" else 0
    days = sorted(k for k in by_day if k != "?")[-30:]
    if not days:
        return None
    W, H, ML, MR, MT, MB = 1400, 210, 56, 16, 20, 26
    vals = [by_day[d][0] for d in days]
    y1 = max(max(vals), 0.01) * 1.25
    y0 = min(min(vals), -0.01) * 1.25
    yr = y1 - y0
    def Y(v): return MT + (y1 - v) / yr * (H - MT - MB)
    grid = []
    tgt = yr / 4
    mag = 10 ** _m.floor(_m.log10(tgt))
    step = next((x for x in (mag, 2*mag, 2.5*mag, 5*mag, 10*mag) if x >= tgt), mag)
    v = _m.ceil(y0 / step) * step
    while v <= y1:
        yy = Y(v)
        if abs(v) > 1e-9:
            grid.append(f"<line x1='{ML}' y1='{yy:.1f}' x2='{W-MR}' y2='{yy:.1f}' "
                        f"stroke='#eaeef2' stroke-width='1'/>"
                        f"<text x='{ML-6}' y='{yy+3.5:.1f}' text-anchor='end' "
                        f"font-size='10' fill='#57606a'>"
                        f"{'+' if v > 0 else chr(0x2212)}${abs(v):.2f}</text>")
        v += step
    zero = Y(0)
    grid.append(f"<line x1='{ML}' y1='{zero:.1f}' x2='{W-MR}' y2='{zero:.1f}' "
                f"stroke='#afb8c1' stroke-width='1'/>"
                f"<text x='{ML-6}' y='{zero+3.5:.1f}' text-anchor='end' "
                f"font-size='10' fill='#57606a'>$0</text>")
    slot = (W - ML - MR) / len(days)
    bw = min(64.0, slot * 0.55)
    bars = []
    data = []
    for i, d in enumerate(days):
        pnl, w, l = by_day[d]
        cx = ML + slot * (i + 0.5)
        x = cx - bw / 2
        top, bot = (Y(pnl), zero) if pnl >= 0 else (zero, Y(pnl))
        col = "#1a7f37" if pnl >= 0 else "#cf222e"
        bars.append(f"<rect x='{x:.1f}' y='{top:.1f}' width='{bw:.1f}' "
                    f"height='{max(bot-top,1.5):.1f}' rx='4' fill='{col}'/>")
        vy = top - 7 if pnl >= 0 else bot + 14
        bars.append(f"<text x='{cx:.1f}' y='{vy:.1f}' text-anchor='middle' "
                    f"font-size='11.5' font-weight='700' fill='{col}'>"
                    f"${pnl:+.2f}</text>")
        bars.append(f"<text x='{cx:.1f}' y='{H-8}' text-anchor='middle' "
                    f"font-size='10' fill='#57606a'>{d[5:]}</text>")
        data.append([round(cx, 1), d, round(pnl, 2), w, l])
    svg = (f"<svg id='dc' viewBox='0 0 {W} {H}' style='width:100%;height:auto;"
           f"display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace'>"
           f"{''.join(grid)}{''.join(bars)}"
           f"<rect x='{ML}' y='{MT}' width='{W-ML-MR}' height='{H-MT-MB}' "
           f"fill='transparent'/></svg>")
    import json as _json
    js = """
<script>
const B = __DATA__;
const svg = document.getElementById('dc');
const tip = document.getElementById('dtip');
svg.addEventListener('mousemove', ev => {
  const r = svg.getBoundingClientRect();
  const mx = (ev.clientX - r.left) * (1400 / r.width);
  let best = 0, bd = 1e9;
  for (let i = 0; i < B.length; i++) {
    const d = Math.abs(B[i][0] - mx);
    if (d < bd) { bd = d; best = i; }
  }
  const b = B[best];
  tip.innerHTML = b[1] + ' · <b>$' + (b[2] >= 0 ? '+' : '') + b[2].toFixed(2)
    + '</b><br>' + b[3] + 'W / ' + b[4] + 'L · ' + (b[3] + b[4]) + ' settled';
  tip.style.display = 'block';
  const px = ev.clientX - r.left, flip = px > r.width - 150;
  tip.style.left = (flip ? px - 145 : px + 12) + 'px';
  tip.style.top = Math.max(2, ev.clientY - r.top - 36) + 'px';
});
svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
</script>""".replace("__DATA__", _json.dumps(data))
    return ("<div style='position:relative;border:1px solid #d0d7de;"
            "border-radius:8px;background:#ffffff;padding:10px 12px 6px'>"
            "<div id='dtip' style='display:none;position:absolute;z-index:9;"
            "background:#ffffff;border:1px solid #d0d7de;border-radius:6px;"
            "box-shadow:0 3px 10px rgba(31,35,40,.12);padding:6px 9px;"
            "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
            "font-size:11px;color:#1f2328;pointer-events:none;"
            "white-space:nowrap'></div>" + svg + js + "</div>")


@st.fragment(run_every="30s")
def render():
    # Independent, read-only z2 pilot panel; production execution is never imported.
    import importlib.util as _z2_import
    _z2_spec = _z2_import.spec_from_file_location(
        "crypto_z2_panel", "/home/varmak/kalshi_revalidation_2026_09_12/live_v1/dashboard_panel.py")
    try:
        _z2_panel = _z2_import.module_from_spec(_z2_spec)
        _z2_spec.loader.exec_module(_z2_panel)
        _z2_panel.render(st)
    except Exception as _z2_error:
        st.warning("Crypto z2 panel unavailable: " + str(_z2_error))
    summary, trades, loglines, cfg = load_state()
    settled = [t for t in trades if t.get("result") in ("win", "loss")]
    opens = [t for t in trades if t.get("result") == "open"]
    # money-in-bets across BOTH lanes (gas ledger + sports live ledger)
    try:
        _sp_lt = [json.loads(l) for l in open(SPORTS_STATE + "/live_trades.jsonl")]
    except Exception:
        _sp_lt = []
    sports_open_top = [t for t in _sp_lt if t.get("result") == "open"]
    gas_in = sum(t.get("entry", 0) * t.get("count", 0) for t in opens)
    sports_in = sum(t["entry"] * t["count"] for t in sports_open_top)
    in_bets = gas_in + sports_in
    sp_set = [t for t in _sp_lt if t.get("result") in ("win", "loss")]
    sp_w = sum(1 for t in sp_set if t["result"] == "win")
    sp_pnl = sum(t.get("pnl", 0) for t in sp_set)
    _spd = datetime.now(ET).strftime("%Y-%m-%d")
    sp_today = sum(t.get("pnl", 0) for t in sp_set
                   if t.get("settled_day") == _spd)
    # metals 15m lane (perpetuals retired 2026-08-07 — funds withdrawn)
    _MET = HERE / "metals15m" / "state"
    try:
        met_rows = [json.loads(l)
                    for l in open(_MET / "metals_trades.jsonl")]
    except Exception:
        met_rows = []
    met_open = [t for t in met_rows if t.get("result") == "open"]
    met_set = [t for t in met_rows if t.get("result") in ("win", "loss")]
    met_w = sum(1 for t in met_set if t["result"] == "win")
    met_pnl = sum(t.get("pnl", 0) for t in met_set)
    met_today = sum(t.get("pnl", 0) for t in met_set
                    if t.get("settled_day") == _spd)
    met_in = sum(t.get("entry", 0) * t.get("count", 0) for t in met_open)
    in_bets += met_in
    # rain lane
    _RN = HERE / "rain" / "state"
    try:
        rain_rows = [json.loads(l) for l in open(_RN / "rain_trades.jsonl")]
    except Exception:
        rain_rows = []
    rain_open = [t for t in rain_rows if t.get("result") == "open"]
    rain_set = [t for t in rain_rows if t.get("result") in ("win", "loss")]
    rain_w = sum(1 for t in rain_set if t["result"] == "win")
    rain_pnl = sum(t.get("pnl", 0) for t in rain_set)
    rain_today = sum(t.get("pnl", 0) for t in rain_set
                     if t.get("settled_day") == _spd)
    rain_in = sum(t.get("entry", 0) * t.get("count", 0) for t in rain_open)
    in_bets += rain_in
    try:
        rain_live = subprocess.run(["pgrep", "-f", "rain_bot.py"],
                                   capture_output=True).returncode == 0
    except Exception:
        rain_live = False
    rain_halt = (_RN / "HALT_RAIN").exists()

    _TMP = (_RN / ".." / ".." / "temp" / "state").resolve()
    try:
        temp_rows = [json.loads(l) for l in open(_TMP / "temp_trades.jsonl")]
    except Exception:
        temp_rows = []
    temp_open = [t for t in temp_rows if t.get("result") == "open"]
    temp_set = [t for t in temp_rows if t.get("result") in ("win", "loss")]
    temp_w = sum(1 for t in temp_set if t["result"] == "win")
    temp_pnl = sum(t.get("pnl", 0) for t in temp_set)
    temp_today = sum(t.get("pnl", 0) for t in temp_set
                     if t.get("settled_day") == _spd)
    temp_in = sum(t.get("entry", 0) * t.get("count", 0) for t in temp_open)
    in_bets += temp_in
    try:
        temp_live = subprocess.run(["pgrep", "-f", "temp_bot.py"],
                                   capture_output=True).returncode == 0
    except Exception:
        temp_live = False
    temp_halt = (_TMP / "HALT_TEMP").exists()

    _CUS = (_RN / ".." / ".." / "cushion" / "state").resolve()
    try:
        cus_rows = [json.loads(l) for l in open(_CUS / "cushion_trades.jsonl")]
    except Exception:
        cus_rows = []
    cus_open = [t for t in cus_rows if t.get("result") == "open"]
    # "stopped" is a TERMINAL state like win/loss — excluding it dropped
    # its P&L from every total (bug introduced with the stop, 2026-08-11)
    cus_set = [t for t in cus_rows
               if t.get("result") in ("win", "loss", "stopped")]
    cus_stops = [t for t in cus_rows if t.get("result") == "stopped"]
    # a stop is a FALSE exit when the position would have won anyway
    cus_false = sum(1 for t in cus_stops if t.get("would_have") == "win")
    # dollars the stop saved (or cost) vs simply holding each position
    cus_saved = 0.0
    for t in cus_stops:
        if not t.get("would_have"):
            continue
        held = ((1 - t.get("entry", 0)) if t["would_have"] == "win"
                else -t.get("entry", 0)) * t.get("count", 0)
        cus_saved += (t.get("pnl") or 0) - held
    cus_w = sum(1 for t in cus_set if t["result"] == "win")
    cus_pnl = sum(t.get("pnl", 0) for t in cus_set)
    cus_today = sum(t.get("pnl", 0) for t in cus_set
                    if t.get("settled_day") == _spd)
    cus_in = sum(t.get("entry", 0) * t.get("count", 0) for t in cus_open)
    in_bets += cus_in
    try:
        cus_live = subprocess.run(["pgrep", "-f", "cushion_bot.py"],
                                  capture_output=True).returncode == 0
    except Exception:
        cus_live = False
    cus_halt = (_CUS / "HALT_CUSHION").exists()
    try:
        cus_scan = json.load(open(_CUS / "scan.json"))
    except Exception:
        cus_scan = {}

    # Desk-wide realised for the ET day. This tile used to read the GAS bot's
    # summary.realized_today, which is gas-only -- and briefly looked correct
    # only because the gas reconciler was wrongly adopting metals fills into
    # its ledger. With that fixed the tile under-reported the desk by exactly
    # the metals P&L. Each lane keeps its own ledger, so sum them.
    gas_today = summary.get("realized_today", 0) or 0
    desk_today = (gas_today + sp_today + met_today + rain_today + temp_today + cus_today)
    try:
        met_live = subprocess.run(["pgrep", "-f", "metals15m_bot.py"],
                                  capture_output=True).returncode == 0
    except Exception:
        met_live = False
    met_halt = (_MET / "HALT_METALS").exists()
    running = bot_process_running()
    try:
        watcher = subprocess.run(["pgrep", "-f", "watch_sports.py"],
                                 capture_output=True).returncode == 0
    except Exception:
        watcher = False
    _bal = live_balance()
    live = not summary.get("dry_run", True)
    halted = summary.get("halted") or ""

    wins = sum(1 for t in settled if t["result"] == "win")
    wr = wins / len(settled) if settled else None
    avg_entry = sum(t["entry"] for t in settled) / len(settled) if settled else None
    makers = sum(1 for t in settled + opens if t.get("fill_type") == "maker")
    fillbase = len(settled) + len(opens)
    total_pnl = sum(t.get("pnl", 0) for t in settled)
    days_active = max(len({t.get("settled_day") for t in settled}), 1)
    # interval-level calibration (mirrors bot.calibration_ok v3: PER LANE)
    calib = {}
    import math as _m
    _since = float(cfg.get("calib_since", 0))
    for _lane in ("r1", "stab"):
        rows_l = [t for t in settled if t.get("lane", "r1") == _lane
                  and (t.get("close_ts") or t["ts"]) >= _since]
        by_iv = {}
        for t in rows_l:
            by_iv.setdefault(t.get("close_ts") or t["ts"], []).append(t)
        ivs = sorted(by_iv)[-30:]
        if len(ivs) >= 10:
            L = sum(1 for k in ivs if any(t["result"] == "loss" for t in by_iv[k]))
            E = sum(1 - sum(t["entry"] for t in by_iv[k]) / len(by_iv[k]) for k in ivs)
            sig = _m.sqrt(max(E * (1 - E / len(ivs)), 1e-9))
            calib[_lane] = (L, E + 3 * sig, len(ivs))

    run_badge = ("<span class='b b-grn'><span class='dot dg'></span>GAS BOT</span>" if running
                 else "<span class='b b-red'><span class='dot dr'></span>GAS BOT DOWN</span>")
    watch_badge = ("<span class='b b-grn'><span class='dot dg'></span>PAPER WATCHER</span>" if watcher
                   else "<span class='b b-red'><span class='dot dr'></span>WATCHER DOWN</span>")
    mode_badge = ("<span class='b b-blu'>LIVE ORDERS ENABLED</span>" if live and not halted
                  else "<span class='b b-amb'>LIVE CONFIG · HALTED</span>" if live
                  else "<span class='b b-amb'>DRY RUN</span>")
    halt_badge = f"<span class='b b-red'>HALTED: {html.escape(halted)}</span>" if halted else ""
    clock = datetime.now(ET).strftime("%a %b %d · %H:%M:%S ET")

    try:
        import json as _sj, time as _st
        _sent = _sj.load(open("/home/varmak/kalshi/kalshi_unbroken_bot/"
                              "sentinel_state/sentinel.json"))
        _sage = _st.time() - _sent.get("ts", 0)
        if _sage > 1500:
            st.markdown("<div style='background:#7a1f1f;color:#fff;"
                        "padding:6px 12px;border-radius:6px;margin:4px 0'>"
                        "\U0001F6F0 SENTINEL STALE \u2014 last check "
                        f"{_sage/60:.0f} min ago (timer down?)</div>",
                        unsafe_allow_html=True)
        elif not _sent.get("ok", False):
            _al = " \u00b7 ".join(_sent.get("alerts", [])[:4])
            st.markdown("<div style='background:#7a1f1f;color:#fff;"
                        "padding:6px 12px;border-radius:6px;margin:4px 0'>"
                        f"\U0001F6F0 SENTINEL: {_al}</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<div class='ss' style='margin:2px 0'>\U0001F6F0 "
                        "sentinel: all lanes healthy \u00b7 checked "
                        f"{_sent.get('checked','?')} \u00b7 auto-checks "
                        "every 10 min</div>", unsafe_allow_html=True)
    except Exception:                                       # noqa: BLE001
        pass

    st.markdown(f"""
<div class='bot-card'>
  <div class='tb-icon'>🎯</div>
  <div><div class='tb-name'>Kalshi Desk — legacy lanes</div>
  <div class='tb-sub'>gas {(cfg.get('gas_lane') or {}).get('contracts',5):.0f}ct×{(cfg.get('gas_lane') or {}).get('max_positions',4)} · sports LIVE 10ct maker-first (WTA+WNBA) · temp 20 cities · crypto CUSHION z\u22651.5 · legacy strategy</div></div>
  <div class='tb-right'>{run_badge} {watch_badge} {mode_badge} {halt_badge}
  <span class='et-clock'>{clock}</span></div>
</div>
<div class='strip' style='grid-template-columns:repeat(8,1fr)'>
  <div class='sc'><div class='sl'>Portfolio</div>
    <div class='sv-big'>{('$%.2f' % _bal[2]) if _bal else money(summary.get('balance'))}</div>
    <div class='ss'>{('cash $%.2f · positions $%.2f' % (_bal[0], _bal[1])) if _bal else 'cash only — live fetch failed'}</div></div>
  <div class='sc'><div class='sl'>Gas — net</div>
    <div class='sv-big {pnl_class(total_pnl)}'>{money(total_pnl, plus=True)}</div>
    <div class='ss'>{len(settled)} settled</div></div>
  <div class='sc'><div class='sl'>Sports — net</div>
    <div class='sv-big {pnl_class(sp_pnl)}'>{money(sp_pnl, plus=True)}</div>
    <div class='ss'>{sp_w}W/{len(sp_set)-sp_w}L</div></div>
  <div class='sc'><div class='sl'>Metals — net</div>
    <div class='sv-big {pnl_class(met_pnl)}'>{money(met_pnl, plus=True)}</div>
    <div class='ss'>{met_w}W/{len(met_set)-met_w}L</div></div>
  <div class='sc'><div class='sl'>Rain — net</div>
    <div class='sv-big {pnl_class(rain_pnl)}'>{money(rain_pnl, plus=True)}</div>
    <div class='ss'>{rain_w}W/{len(rain_set)-rain_w}L</div></div>
  <div class='sc'><div class='sl'>Temp — net</div>
    <div class='sv-big {pnl_class(temp_pnl)}'>{money(temp_pnl, plus=True)}</div>
    <div class='ss'>{temp_w}W/{len(temp_set)-temp_w}L</div></div>
  <div class='sc'><div class='sl'>Crypto cushion — net</div>
    <div class='sv-big {pnl_class(cus_pnl)}'>{money(cus_pnl, plus=True)}</div>
    <div class='ss'>{cus_w}W/{len(cus_set)-cus_w-len(cus_stops)}L{f" · {len(cus_stops)} stopped" if cus_stops else ""}</div></div>
  <div class='sc'><div class='sl'>$ in bets</div>
    <div class='sv-big'>${in_bets:.2f}</div>
    <div class='ss'>gas ${gas_in:.2f}({len(opens)}) · sports ${sports_in:.2f}({len(sports_open_top)}) · metals ${met_in:.2f}({len(met_open)}) · rain ${rain_in:.2f}({len(rain_open)}) · temp ${temp_in:.2f}({len(temp_open)}) · cushion ${cus_in:.2f}({len(cus_open)})</div></div>
</div>
<div class='strip' style='grid-template-columns:repeat(8,1fr);margin-top:6px'>
  <div class='sc'><div class='sl'>Today — legacy lanes</div>
    <div class='sv-big {pnl_class(desk_today)}'>{money(desk_today, plus=True)}</div>
    <div class='ss'>realized this ET day</div></div>
  <div class='sc'><div class='sl'>Gas — today</div>
    <div class='sv-big {pnl_class(gas_today)}'>{money(gas_today, plus=True)}</div>
    <div class='ss'>{len(opens)} open</div></div>
  <div class='sc'><div class='sl'>Sports — today</div>
    <div class='sv-big {pnl_class(sp_today)}'>{money(sp_today, plus=True)}</div>
    <div class='ss'>{len(sports_open_top)} open</div></div>
  <div class='sc'><div class='sl'>Metals — today</div>
    <div class='sv-big {pnl_class(met_today)}'>{money(met_today, plus=True)}</div>
    <div class='ss'>{len(met_open)} open{' · HALTED' if met_halt else ('' if met_live else ' · BOT DOWN')}</div></div>
  <div class='sc'><div class='sl'>Rain — today</div>
    <div class='sv-big {pnl_class(rain_today)}'>{money(rain_today, plus=True)}</div>
    <div class='ss'>{len(rain_open)} open{' · HALTED' if rain_halt else ('' if rain_live else ' · BOT DOWN')}</div></div>
  <div class='sc'><div class='sl'>Temp — today</div>
    <div class='sv-big {pnl_class(temp_today)}'>{money(temp_today, plus=True)}</div>
    <div class='ss'>{len(temp_open)} open{' · HALTED' if temp_halt else ('' if temp_live else ' · BOT DOWN')}</div></div>
  <div class='sc'><div class='sl'>Crypto cushion — today</div>
    <div class='sv-big {pnl_class(cus_today)}'>{money(cus_today, plus=True)}</div>
    <div class='ss'>{len(cus_open)} open{' · HALTED' if cus_halt else ('' if cus_live else ' · BOT DOWN')}</div></div>
  <div class='sc'><div class='sl'>&nbsp;</div>
    <div class='sv-big'>&nbsp;</div>
    <div class='ss'>&nbsp;</div></div>
</div></div>""", unsafe_allow_html=True)

    # ---------------- LIVE POSITIONS (all lanes, at a glance) ----------------
    live_rows = []
    for lane, lst in (("gas", [t2 for t2 in opens if t2.get("lane") == "gas"]),
                      ("sports", sports_open_top), ("metals", met_open),
                      ("rain", rain_open), ("temp", temp_open),
                      ("cushion", cus_open)):
        for t in lst:
            live_rows.append((t.get("ts", 0), lane, t))
    _lane_rank = {"gas": 0, "sports": 1, "metals": 2, "rain": 3,
                  "temp": 4, "cushion": 5}
    live_rows.sort(key=lambda x: (_lane_rank.get(x[1], 9), -x[0]))
    st.markdown("<div class='sr'><span>Legacy lane positions — new z2 pilot shown above</span><hr/></div>",
                unsafe_allow_html=True)
    if live_rows:
        lp = []
        for ts_, lane, t in live_rows:
            e = t.get("entry") or t.get("vwap") or 0
            c = t.get("count") or t.get("n") or 0
            lp.append(f"<tr><td>{lane}</td>"
                      f"<td>{html.escape(str(t.get('ticker','')).replace('KX',''))}</td>"
                      f"<td>{str(t.get('side','')).upper()}</td>"
                      f"<td>{c:g}ct</td>"
                      f"<td>{e*100:.0f}¢</td>"
                      f"<td>${e*c:.2f}</td>"
                      f"<td>{et_time(ts_)}</td></tr>")
        st.markdown("<div class='panel'><table class='tt'>"
                    "<tr><th>Lane</th><th>Market</th><th>Side</th><th>Size</th>"
                    "<th>Entry</th><th>$ at risk</th><th>Placed (ET)</th></tr>"
                    + ''.join(lp) + "</table></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='panel'><div class='ss'>no open positions "
                    "right now</div></div>", unsafe_allow_html=True)




    # ---------------- GAS panel (the live-money lane) ----------------
    gcfg = cfg.get("gas_lane") or {}
    gas_open = [t2 for t2 in opens if t2.get("lane") == "gas"]
    gas_hist = [t2 for t2 in settled if t2.get("lane") == "gas"]
    gw = sum(1 for t2 in gas_hist if t2["result"] == "win")
    gexp = sum(t2["entry"] * t2["count"] for t2 in gas_open)
    gcap = gcfg.get("max_group_loss_usd", cfg.get("daily_loss_halt_usd", 15))
    cells_g = [
        f"<div class='sc'><div class='sl'>gas record</div>"
        f"<div class='sv-big {'vg' if sum(t2.get('pnl',0) for t2 in gas_hist)>0 else ''}'>"
        f"{gw}W/{len(gas_hist)-gw}L</div>"
        f"<div class='ss'>${sum(t2.get('pnl',0) for t2 in gas_hist):+.2f} lifetime</div></div>",
        f"<div class='sc'><div class='sl'>exposure / cap</div>"
        f"<div class='sv-big'>${gexp:.2f} / ${gcap:.0f}</div>"
        f"<div class='ss'>{len(gas_open)}/{gcfg.get('max_positions',2)} positions · "
        f"{gcfg.get('contracts',7):.0f}ct each</div></div>"]
    for g in gas_open:
        hrs = ((g.get("close_ts") or 0) - time.time()) / 3600
        cells_g.append(
            f"<div class='sc'><div class='sl'>{html.escape(g['ticker'].split('-')[-1])} "
            f"{g['side'].upper()} · {max(hrs,0):.1f}h left</div>"
            f"<div class='sv'>{g.get('count',0):.0f}ct @ {g.get('entry',0)*100:.1f}¢</div>"
            f"<div class='ss'>max loss ${g.get('entry',0)*g.get('count',0):.2f}</div></div>")
    st.markdown("<div class='sr'><span>Gas lane — live</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({len(cells_g)},1fr)'>"
                f"{''.join(cells_g)}</div>", unsafe_allow_html=True)

    _lg = live_gas()
    _held_g = {t.get("ticker") for t in (settled + opens)}
    _open_g = {t.get("ticker") for t in opens}
    _gr = []
    for q in _lg:
        if 0 >= q["hrs"] > -900:
            _gr.append(f"<tr><td><b>{html.escape(q['strike'])}</b></td>"
                       f"<td class='ss'>closed</td><td colspan='6' class='ss'>—</td>"
                       f"<td><span class='b b-gry'>SETTLING</span></td>"
                       f"<td class='ss'>window closed, awaiting settlement</td></tr>")
            continue
        if q["hrs"] < 0:
            _gr.append(f"<tr><td colspan='9' class='ss'>"
                       f"{html.escape(' · '.join(q['why']))}</td></tr>")
            continue
        if q["t"] in _held_g:
            inpos = q["t"] in _open_g
            badge = ("<span class='b b-blu'>HOLDING</span>" if inpos
                     else "<span class='b b-gry'>done</span>")
            reason = ("already holding this strike" if inpos
                      else "already traded this strike")
        elif not q["why"]:
            badge = "<span class='b b-grn'>ELIGIBLE</span>"
            reason = "price+time eligible; needs the held check"
        else:
            badge = ("<span class='b b-live'><span class='dot'></span>"
                     "watching</span>")
            reason = " · ".join(q["why"])
        _gr.append(
            f"<tr><td><b>{html.escape(q['strike'])}</b></td>"
            f"<td>{q['hrs']:.1f} h</td>"
            f"<td>{q['b']*100:.0f}\u00a2 / {q['a']*100:.0f}\u00a2</td>"
            f"<td>{(1-q['a'])*100:.0f}\u00a2 / {(1-q['b'])*100:.0f}\u00a2</td>"
            f"<td><b>{q['side']}</b></td>"
            f"<td><b>{q['fb']*100:.0f}\u00a2 / {q['fa']*100:.0f}\u00a2</b></td>"
            f"<td>{q['fm']*100:.1f}\u00a2</td>"
            f"<td>{(q['fa']-q['fb'])*100:.0f}\u00a2</td>"
            f"<td>{badge}</td>"
            f"<td class='ss'>{html.escape(reason)}</td></tr>")
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Strike</th><th>Closes in</th><th>YES bid/ask</th>"
                "<th>NO bid/ask</th><th>Fav</th><th>Fav bid/ask</th>"
                "<th>Fav mid</th><th>Spread</th><th>Status</th>"
                "<th>Detail</th></tr>"
                + ("".join(_gr) if _gr else
                   "<tr><td colspan='10' class='ss'>no open gas strikes — "
                   "next AAA print listing opens later today</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # ---------------- RAIN lane ----------------
    cells_r = [
        f"<div class='sc'><div class='sl'>rain record "
        f"{'\U0001F534 HALTED' if rain_halt else ('\U0001F7E2' if rain_live else '\U0001F534 DOWN')}</div>"
        f"<div class='sv-big {pnl_class(rain_pnl)}'>{rain_w}W/{len(rain_set)-rain_w}L</div>"
        f"<div class='ss'>{money(rain_pnl, plus=True)} lifetime · "
        f"{money(rain_today, plus=True)} today</div></div>",
        f"<div class='sc'><div class='sl'>rule</div>"
        f"<div class='sv'>95-99\u00a2 · final 1-12h</div>"
        f"<div class='ss'>held 2h · spread\u22642\u00a2 · 1ct · max 15</div></div>",
        f"<div class='sc'><div class='sl'>archive evidence</div>"
        f"<div class='sv'>119-0 · +1.26\u00a2</div>"
        f"<div class='ss'>after 1\u00a2 slippage · 20 cities</div></div>",
        f"<div class='sc'><div class='sl'>open / cap</div>"
        f"<div class='sv-big'>{len(rain_open)} / 15</div>"
        f"<div class='ss'>${rain_in:.2f} at risk</div></div>"]
    st.markdown("<div class='sr'><span>Rain — live (1 contract, max 15)</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({len(cells_r)},1fr)'>"
                f"{''.join(cells_r)}</div>", unsafe_allow_html=True)

    _lr = live_rain()
    _held_r = {t.get("ticker") for t in rain_rows}
    _open_r = {t.get("ticker") for t in rain_open}
    _rr = []
    for q in _lr:
        if q["hrs"] <= 0:
            _rr.append(
                f"<tr><td><b>{html.escape(q['city'])}</b></td>"
                f"<td class='ss'>{q['grp']}</td><td class='ss'>closed</td>"
                f"<td colspan='6' class='ss'>—</td>"
                f"<td><span class='b b-gry'>SETTLING</span></td>"
                f"<td class='ss'>window closed, awaiting settlement</td></tr>")
            continue
        if q["t"] in _held_r:
            inpos = q["t"] in _open_r
            badge = ("<span class='b b-blu'>HOLDING</span>" if inpos
                     else "<span class='b b-gry'>done</span>")
            reason = ("already entered this city-day" if inpos
                      else "already traded this city-day")
        elif not q["why"]:
            badge = "<span class='b b-grn'>ELIGIBLE</span>"
            reason = "price+time eligible; needs 2h held in band"
        else:
            badge = "<span class='b b-live'><span class='dot'></span>watching</span>"
            reason = " · ".join(q["why"])
        _rr.append(
            f"<tr><td><b>{html.escape(q['city'])}</b></td>"
            f"<td class='ss'>{q['grp']}</td>"
            f"<td>{q['hrs']:.1f} h</td>"
            f"<td>{q['b']*100:.0f}\u00a2 / {q['a']*100:.0f}\u00a2</td>"
            f"<td>{(1-q['a'])*100:.0f}\u00a2 / {(1-q['b'])*100:.0f}\u00a2</td>"
            f"<td><b>{q['side']}</b></td>"
            f"<td><b>{q['fb']*100:.0f}\u00a2 / {q['fa']*100:.0f}\u00a2</b></td>"
            f"<td>{q['fm']*100:.1f}\u00a2</td>"
            f"<td>{(q['fa']-q['fb'])*100:.0f}\u00a2</td>"
            f"<td>{badge}</td>"
            f"<td class='ss'>{html.escape(reason)}</td></tr>")
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>City</th><th>Grp</th><th>Closes in</th>"
                "<th>YES bid/ask</th><th>NO bid/ask</th><th>Fav</th>"
                "<th>Fav bid/ask</th><th>Fav mid</th><th>Spread</th>"
                "<th>Status</th><th>Detail</th></tr>"
                + ("".join(_rr) if _rr else
                   "<tr><td colspan='11' class='ss'>no open rain markets"
                   "</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # ---------------- TEMP lane ----------------
    cells_t = [
        f"<div class='sc'><div class='sl'>temp record "
        f"{'\U0001F534 HALTED' if temp_halt else ('\U0001F7E2' if temp_live else '\U0001F534 DOWN')}</div>"
        f"<div class='sv-big {pnl_class(temp_pnl)}'>{temp_w}W/{len(temp_set)-temp_w}L</div>"
        f"<div class='ss'>{money(temp_pnl, plus=True)} lifetime \u00b7 "
        f"{money(temp_today, plus=True)} today</div></div>",
        f"<div class='sc'><div class='sl'>rule</div>"
        f"<div class='sv'>leader 95-99\u00a2 \u00b7 final 1-12h</div>"
        f"<div class='ss'>NO held gate \u00b7 spread\u22642\u00a2 \u00b7 1ct \u00b7 20 cities</div></div>",
        f"<div class='sc'><div class='sl'>archive evidence</div>"
        f"<div class='sv'>924-16 \u00b7 +1.04\u00a2</div>"
        f"<div class='ss'>940 entries / 73d \u00b7 floor +0.36\u00a2 \u00b7 unproven at 95%</div></div>",
        f"<div class='sc'><div class='sl'>open / cap</div>"
        f"<div class='sv-big'>{len(temp_open)} / 20</div>"
        f"<div class='ss'>${temp_in:.2f} at risk</div></div>"]
    st.markdown("<div class='sr'><span>High+Low temp — live (1 contract, 32 city-lanes, no count cap)</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({len(cells_t)},1fr)'>"
                f"{''.join(cells_t)}</div>", unsafe_allow_html=True)

    _lt = live_temp()
    _held_t = {(t.get("ticker", "").split("-")[0],
                t.get("ticker", "").split("-")[1]) for t in temp_rows
               if len(t.get("ticker", "").split("-")) >= 3}
    _open_t = {t.get("ticker") for t in temp_open}
    _tr2 = []
    for q in _lt:
        if q["hrs"] <= 0 and q["t"]:
            _tr2.append(
                f"<tr><td><b>{html.escape(q['city'])}</b></td>"
                f"<td class='ss'>{html.escape(q['brk'])}</td><td class='ss'>closed</td>"
                f"<td colspan='4' class='ss'>\u2014</td>"
                f"<td><span class='b b-gry'>SETTLING</span></td>"
                f"<td class='ss'>window closed, awaiting settlement</td></tr>")
            continue
        keyparts = q["t"].split("-") if q["t"] else []
        traded = (len(keyparts) >= 3
                  and (keyparts[0], keyparts[1]) in _held_t)
        if traded:
            inpos = any(p in _open_t and p.split("-")[:2] == keyparts[:2]
                        for p in _open_t)
            badge = ("<span class='b b-blu'>HOLDING</span>" if inpos
                     else "<span class='b b-gry'>done</span>")
            reason = "one entry per city-day \u2014 taken"
        elif not q["why"]:
            badge = "<span class='b b-grn'>ELIGIBLE</span>"
            reason = "all gates met \u2014 bot enters on next 30s poll"
        elif q["why"] and q["why"][0].startswith("FETCH FAILED"):
            badge = "<span class='b b-gry'>NO DATA</span>"
            reason = "quote fetch failed \u2014 display only, bot polls independently"
        else:
            badge = "<span class='b b-live'><span class='dot'></span>watching</span>"
            reason = " \u00b7 ".join(q["why"])
        _hrs_txt = ("\u2014" if q["hrs"] < 0 else f"{q['hrs']:.1f} h")
        _quote_txt = ("\u2014" if q["hrs"] < 0 else
                      f"{q['b']*100:.0f}\u00a2 / {q['a']*100:.0f}\u00a2")
        _tr2.append(
            f"<tr><td><b>{html.escape(q['city'])}</b></td>"
            f"<td class='ss'>{html.escape(q['brk'])}</td>"
            f"<td>{_hrs_txt}</td>"
            f"<td>{_quote_txt}</td>"
            f"<td>{('\u2014' if q['hrs'] < 0 else f"{q['mid']*100:.1f}\u00a2")}</td>"
            f"<td>{(q['a']-q['b'])*100:.0f}\u00a2</td>"
            f"<td class='ss'>{'yes' if not q['why'] else ''}</td>"
            f"<td>{badge}</td>"
            f"<td class='ss'>{html.escape(reason)}</td></tr>")
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>City</th><th>Leader</th><th>Closes in</th>"
                "<th>Bid/Ask</th><th>Mid</th><th>Spread</th><th>In zone</th>"
                "<th>Status</th><th>Detail</th></tr>"
                + ("".join(_tr2) if _tr2 else
                   "<tr><td colspan='9' class='ss'>no open temp ladders"
                   "</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # ---------------- CUSHION crypto lane ----------------
    import time as _time2
    # CAPTURE FUNNEL (audit 2026-08-10, item 7): four numbers, judged
    # against DEPTH-VALID signals — a signal whose real book never showed
    # an executable ask within the rule's price cap is the cap working,
    # not a miss. Core fill-rate EXCLUDES the maker96 fallback leg
    # (item 6): the pilot is judged on its own ledger line.
    # counted per MARKET, not per row: refusals (flicker locks, phantom
    # books, weakened quotes) log every tick, so raw row-counts inflate
    # into the thousands for a day with ~20 real opportunities. One
    # market = one opportunity, matching how capture was audited.
    _s_raw, _s_dv, _s_sub = set(), set(), set()
    _t0f = _time2.time() - 86400
    try:
        for _l in open(_CUS / "cushion_attempts.jsonl"):
            try:
                _a = json.loads(_l)
            except ValueError:
                continue
            if _a.get("ts", 0) < _t0f or not _a.get("ticker"):
                continue
            _s_raw.add(_a["ticker"])
            if _a.get("depth_valid"):
                _s_dv.add(_a["ticker"])
            if _a.get("outcome") in ("rested", "rested_partial", "filled",
                                     "no_fill", "unknown"):
                _s_sub.add(_a["ticker"])
    except OSError:
        pass
    _f_raw, _f_dv, _f_sub = len(_s_raw), len(_s_dv), len(_s_sub)
    _fill_tkrs = {r.get("ticker") for r in cus_rows
                  if r.get("ts", 0) >= _t0f
                  and r.get("leg") != "maker96" and not r.get("adopted")}
    _f_fill = len(_fill_tkrs)
    _f_m96 = sum(1 for r in cus_rows
                 if r.get("ts", 0) >= _t0f and r.get("leg") == "maker96")
    # fill-rate = fills WITHIN the depth-valid set over depth-valid — the
    # only pair sharing a denominator (audit round 2: raw-fills/dv could
    # exceed 100%, and read 0% while dv warms up after the field's
    # introduction). '—' until dv has data.
    _f_hit = len(_fill_tkrs & _s_dv)
    _f_pct = f"{100.0 * _f_hit / _f_dv:.0f}%" if _f_dv else "—"
    cells_c = [
        f"<div class='sc'><div class='sl'>cushion record "
        f"{'\U0001F534 HALTED' if cus_halt else ('\U0001F7E2' if cus_live else '\U0001F534 DOWN')}</div>"
        f"<div class='sv-big {pnl_class(cus_pnl)}'>{cus_w}W/"
        f"{len(cus_set)-cus_w-len(cus_stops)}L</div>"
        f"<div class='ss'>{money(cus_pnl, plus=True)} lifetime \u00b7 "
        f"{money(cus_today, plus=True)} today</div></div>",
        f"<div class='sc'><div class='sl'>rule</div>"
        f"<div class='sv'>85-93\u00a2 \u00b7 cushion z\u22651.5</div>"
        f"<div class='ss'>final 3-10min \u00b7 spread\u22642\u00a2 \u00b7 5ct GTC + 1ct m96 \u00b7 max 4 \u00b7 ~2 losses/day BY DESIGN</div></div>",
        f"<div class='sc'><div class='sl'>evidence</div>"
        f"<div class='sv'>+1.38\u00a2 / +0.78\u00a2</div>"
        f"<div class='ss'>archive 1656-106 \u00b7 holdout 229-17 \u00b7 stressed +1\u00a2</div></div>",
        f"<div class='sc'><div class='sl'>open / cap</div>"
        f"<div class='sv-big'>{len(cus_open)} / 4</div>"
        f"<div class='ss'>${cus_in:.2f} at risk</div></div>",
        f"<div class='sc'><div class='sl'>40\u00a2 stop</div>"
        f"<div class='sv-big {pnl_class(cus_saved)}'>"
        f"{money(cus_saved, plus=True)}</div>"
        f"<div class='ss'>{len(cus_stops)} fired \u00b7 {cus_false} false "
        f"\u00b7 vs holding</div></div>",
        f"<div class='sc'><div class='sl'>capture 24h (core)</div>"
        f"<div class='sv-big'>{_f_pct}</div>"
        f"<div class='ss'>raw {_f_raw} · depth-valid {_f_dv} · "
        f"submitted {_f_sub} · filled {_f_fill} · "
        f"maker96 {_f_m96}</div></div>"]
    st.markdown("<div class='sr'><span>Legacy crypto cushion — prior configuration and history</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({len(cells_c)},1fr)'>"
                f"{''.join(cells_c)}</div>", unsafe_allow_html=True)

    _scan_age = _time2.time() - (cus_scan.get("ts") or 0)
    _cr = []
    if cus_scan.get("rows") and _scan_age < 120:
        for q in sorted(cus_scan["rows"], key=lambda x: x.get("rem", 99)):
            zt = q.get("z")
            why = q.get("why", "")
            if why == "ENTER":
                badge = "<span class='b b-grn'>ENTERING</span>"
            elif q.get("tkr") in {t.get("ticker") for t in cus_open}:
                badge = "<span class='b b-blu'>HOLDING</span>"
            else:
                badge = "<span class='b b-live'><span class='dot'></span>watching</span>"
            _cr.append(
                f"<tr><td><b>{html.escape(q['series'].replace('KX','').replace('15M',''))}</b></td>"
                f"<td>{q.get('rem',0):.1f} m</td>"
                f"<td><b>{q.get('side','')}</b></td>"
                f"<td>{q.get('fa',0)*100:.1f}\u00a2</td>"
                f"<td>{('%.2f' % zt) if zt is not None else '\u2014'}</td>"
                f"<td>{badge}</td>"
                f"<td class='ss'>{html.escape(str(why))}</td></tr>")
    else:
        _cr.append(f"<tr><td colspan='7' class='ss'>scanner data stale "
                   f"({_scan_age:.0f}s) or bot down</td></tr>")
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Asset</th><th>Closes in</th><th>Fav</th>"
                "<th>Fav ask</th><th>Cushion z</th><th>Status</th><th>Detail</th></tr>"
                + "".join(_cr) + "</table></div>", unsafe_allow_html=True)

    # ---------------- METALS 15m lane ----------------
    # Perpetuals retired 2026-08-07 (funds moved to prediction markets).
    # Gold keeps the shared core; Silver may enter earlier at 90-93c after
    # three contiguous closes. WTI remains excluded.
    cells_m = [
        f"<div class='sc'><div class='sl'>metals record "
        f"{'🔴 HALTED' if met_halt else ('🟢' if met_live else '🔴 DOWN')}</div>"
        f"<div class='sv-big {pnl_class(met_pnl)}'>{met_w}W/{len(met_set)-met_w}L</div>"
        f"<div class='ss'>{money(met_pnl, plus=True)} lifetime · "
        f"{money(met_today, plus=True)} today</div></div>",
        f"<div class='sc'><div class='sl'>rule</div>"
        f"<div class='sv'>core 93-98¢ · rem 4-12</div>"
        f"<div class='ss'>Silver early 90-93¢ · rem 9-12 · 1 contract</div></div>",
        f"<div class='sc'><div class='sl'>archive evidence</div>"
        f"<div class='sv'>Silver early 32-0</div>"
        f"<div class='ss'>+6.51¢ after 1¢ stress · Gold early rejected</div></div>"]
    for p in met_open:
        cells_m.append(
            f"<div class='sc'><div class='sl'>"
            f"{html.escape(p.get('ticker','').replace('KX','').replace('15M',''))}</div>"
            f"<div class='sv'>{p.get('count',0):.0f}ct @ {p.get('entry',0)*100:.0f}¢</div>"
            f"<div class='ss'>{p.get('side','').upper()} · rem {p.get('rem','?')} at entry</div></div>")
    st.markdown("<div class='sr'><span>Metals 15m — live (1 contract)</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({len(cells_m)},1fr)'>"
                f"{''.join(cells_m)}</div>", unsafe_allow_html=True)

    # live board: what the bot is looking at RIGHT NOW, and why it holds off
    _lm = live_metals()
    # Tickers the bot has ALREADY entered. The rule is one position per
    # 15-minute event (research spec, and the backtest that produced the
    # +2.28c figure took exactly one entry per market). A market we already
    # hold will never be re-entered, so showing it as ELIGIBLE was wrong --
    # it read "needs unbroken history" when the real reason is "already in".
    # Both fills would settle on the SAME outcome, so a second entry is not a
    # second trade, it is one trade at double the size the rule was measured at.
    _held = {t.get("ticker") for t in met_rows}
    _open_now = {t.get("ticker") for t in met_open}
    _rows = []
    for q in _lm:
        ok = not q["why"]
        if q["rem"] <= 0:
            _rows.append(
                f"<tr><td>{html.escape(q['ser'].replace('KX','').replace('15M',''))}</td>"
                f"<td class='ss'>closed</td>"
                f"<td colspan='7' class='ss'>—</td>"
                f"<td><span class='b b-gry'>SETTLING</span></td>"
                f"<td class='ss'>window closed, awaiting settlement</td></tr>")
            continue
        if q["t"] in _held:
            inpos = q["t"] in _open_now
            badge = ("<span class='b b-blu'>HOLDING</span>" if inpos
                     else "<span class='b b-gry'>done</span>")
            reason = ("already entered this event — one position per event"
                      if inpos else
                      "already traded this event — no re-entry")
        elif ok:
            badge = "<span class='b b-grn'>ELIGIBLE</span>"
            reason = q["lane"] + " price eligible; needs unbroken history"
        else:
            badge = "<span class='b b-live'><span class='dot'></span>watching</span>"
            reason = " · ".join(q["why"])
        if q["rem"] < 0:
            _rows.append(
                f"<tr><td>{html.escape(q['ser'].replace('KX','').replace('15M',''))}</td>"
                f"<td colspan='9' class='ss'>—</td>"
                f"<td class='ss'>{html.escape(' · '.join(q['why']))}</td></tr>")
            continue
        _rows.append(
            f"<tr><td>{html.escape(q['ser'].replace('KX','').replace('15M',''))}</td>"
            f"<td>{q['rem']:.1f} min</td>"
            f"<td>{q['b']*100:.0f}\u00a2 / {q['a']*100:.0f}\u00a2</td>"
            f"<td>{(1-q['a'])*100:.0f}\u00a2 / {(1-q['b'])*100:.0f}\u00a2</td>"
            f"<td><b>{q['side']}</b></td>"
            f"<td><b>{q['fb']*100:.0f}\u00a2 / {q['fa']*100:.0f}\u00a2</b></td>"
            f"<td>{q['fm']*100:.1f}\u00a2</td>"
            f"<td>{(q['fa']-q['fb'])*100:.0f}\u00a2</td>"
            f"<td>{('%.0f' % q['sz']) if q['sz'] is not None else '—'}</td>"
            f"<td>{badge}</td>"
            f"<td class='ss'>{html.escape(reason)}</td></tr>")
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Market</th><th>Time left</th><th>YES bid/ask</th>"
                "<th>NO bid/ask</th><th>Fav</th><th>Fav bid/ask</th>"
                "<th>Fav mid</th><th>Spread</th><th>Size @ask</th>"
                "<th>Status</th><th>Detail</th></tr>"
                + ("".join(_rows) if _rows else
                   "<tr><td colspan='11' class='ss'>no open metals markets "
                   "right now</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # ---------------- SPORTS paper panel ----------------
    SP = SPORTS_STATE
    sigs, setl = [], {}
    try:
        sigs = [json.loads(l) for l in open(SP + "/signals.jsonl")]
    except Exception:
        pass
    try:
        for l in open(SP + "/settles.jsonl"):
            r = json.loads(l)
            setl[r["ticker"]] = r["result"]
    except Exception:
        pass
    def _fee(p): return 0.07 * p * (1 - p)
    def _net(g, res):
        win = (res == g["side"])
        return (1 - g["fav_ask"] if win else -g["fav_ask"]) - _fee(g["fav_ask"]), win
    def _fam(g):
        return (g["series"].replace("KX", "").replace("MATCH", "")
                .replace("GAME", ""))
    # "validated" = the LIVE rule only (WTA H10, WNBA H5, 97-99, one entry
    # per MATCH — audit 08-03: pooling series/holds/mirrors double-counts
    # and drags in families whose own backtests failed)
    LIVE_CELLS = {("KXWTAMATCH", 10), ("KXWNBAGAME", 5)}
    val, _seen_ev = [], set()
    for g in sorted(sigs, key=lambda g: g.get("ts", 0)):
        if g.get("lo", 0.97) >= 0.97 \
                and (g["series"], g["H"]) in LIVE_CELLS:
            ev = g["ticker"].rsplit("-", 1)[0]
            if ev not in _seen_ev:
                _seen_ev.add(ev)
                val.append(g)
    exp = [g for g in sigs if g.get("lo", 0.97) < 0.97]
    def _tot(gs):
        st_ = [(g, setl[g["ticker"]]) for g in gs if g["ticker"] in setl]
        w = sum(1 for g, r in st_ if r == g["side"])
        net = sum(_net(g, r)[0] for g, r in st_)
        return len(gs), len(st_), w, net
    vn, vs, vw, vnet = _tot(val)
    en, es, ew, enet = _tot(exp)
    depth = []
    for g in sigs:
        lad = g.get("no") if g["side"] == "yes" else g.get("yes")
        if lad:
            try: depth.append(max(float(x[1]) for x in lad))
            except Exception: pass
    depth.sort()
    dmed = f"{depth[len(depth)//2]:,.0f}ct" if depth else "—"
    strip = (
        f"<div class='sc'><div class='sl'>LIVE-RULE paper control (WTA H10 + WNBA H5)</div>"
        f"<div class='sv-big {'vg' if vnet>0 else ('vr' if vnet<0 else '')}'>"
        f"{vw}W/{vs-vw}L</div>"
        f"<div class='ss'>{vn} signals · {vs} settled · {vnet*100:+.0f}¢@1ct "
        f"· ${vnet*10:+.2f}@10ct</div></div>"
        f"<div class='sc'><div class='sl'>exploratory (all series/bands/holds)</div>"
        f"<div class='sv-big {'vg' if enet>0 else ('vr' if enet<0 else '')}'>"
        f"{ew}W/{es-ew}L</div>"
        f"<div class='ss'>{en} signals · {es} settled · {enet*100:+.0f}¢@1ct</div></div>"
        f"<div class='sc'><div class='sl'>depth at entry ask</div>"
        f"<div class='sv-big'>{dmed}</div>"
        f"<div class='ss'>median resting · {len(depth)} snapshots</div></div>"
        f"<div class='sc'><div class='sl'>awaiting settlement</div>"
        f"<div class='sv-big'>{len([g for g in sigs if g['ticker'] not in setl])}</div>"
        f"<div class='ss'>matches in progress</div></div>")
    # sports LIVE strip
    try:
        lt = [json.loads(l) for l in open(SP + "/live_trades.jsonl")]
    except Exception:
        lt = []
    try:
        sbot = subprocess.run(["pgrep", "-f", "sports_bot.py"],
                              capture_output=True).returncode == 0
    except Exception:
        sbot = False
    l_open = [t for t in lt if t.get("result") == "open"]
    lt_main = [t for t in lt if t.get("lane") != "sports_v2"]
    l_set = [t for t in lt_main if t.get("result") in ("win", "loss")]
    import datetime as _dt
    _today = _dt.datetime.now(ET).strftime("%Y-%m-%d")
    l_today = [t for t in l_set if t.get("settled_day") == _today]
    lw = sum(1 for t in l_today if t["result"] == "win")
    lpnl = sum(t.get("pnl", 0) for t in l_today)
    alll = sum(t.get("pnl", 0) for t in l_set)
    try:
        sc = json.load(open(SP + "/scanner_status.json"))
        sc_age = time.time() - sc.get("ts", 0)
        sc_txt = f"{sc.get('total', 0)}" if sc_age < 180 else "stale"
        sc_sub = " · ".join(f"{k.replace('KX','').replace('MATCH','')[:6]} {v}"
                            for k, v in (sc.get("by") or {}).items())
    except Exception:
        sc_txt, sc_sub = "—", ""
    try:
        appr = sc.get("approaching", 0)
        chips = "".join(
            f"<span style='display:inline-block;background:var(--p2);"
            f"border:1px solid var(--bd);border-radius:4px;"
            f"font-family:var(--mono);font-size:10.5px;color:var(--t1);"
            f"padding:2px 8px;margin:2px 4px 2px 0'>"
            f"{html.escape(str(e))} <b>{f2*100:.0f}¢</b></span>"
            for e, f2 in (sc.get("nearest") or []))
    except Exception:
        appr, chips = "—", ""
    live_strip = (
        f"<div class='sc'><div class='sl'>matches under scanner</div>"
        f"<div class='sv-big'>{sc_txt}</div>"
        f"<div class='ss'>{sc_sub[:70]}</div></div>"
        f"<div class='sc'><div class='sl'>approaching band (90-97¢)</div>"
        f"<div class='sv-big'>{appr}</div>"
        f"<div class='ss'>closest to entry first</div></div>"
        f"<div class='sc'><div class='sl'>SPORTS LIVE "
        f"{'🟢' if sbot else '🔴 DOWN'}</div>"
        f"<div class='sv-big {'vg' if lpnl>0 else ('vr' if lpnl<0 else '')}'>"
        f"{lw}W/{len(l_today)-lw}L today</div>"
        f"<div class='ss'>${lpnl:+.2f} today · ${alll:+.2f} lifetime · 10ct/match</div></div>"
        f"<div class='sc'><div class='sl'>open positions</div>"
        f"<div class='sv-big'>{len(l_open)} / 4</div>"
        f"<div class='ss'>exposure ${sum(t['entry']*t['count'] for t in l_open):.2f} "
        f"· halt -$15/day</div></div>")
    # v2 experimental lane — its own tile, never merged into the main record
    v2_all = [t for t in lt if t.get("lane") == "sports_v2"]
    v2_set = [t for t in v2_all if t.get("result") in ("win", "loss")]
    v2w = sum(1 for t in v2_set if t["result"] == "win")
    v2pnl = sum(t.get("pnl", 0) for t in v2_set)
    live_strip += (
        f"<div class='sc'><div class='sl'>v2 experiment (92-95¢, 1ct)</div>"
        f"<div class='sv-big {pnl_class(v2pnl)}'>{v2w}W/{len(v2_set)-v2w}L</div>"
        f"<div class='ss'>{len(v2_all)} fired · {money(v2pnl, plus=True)} · "
        f"spike-veto · unproven</div></div>")
    for t in l_open:                      # ALL of them, not the first two
        tag = "v2" if t.get("lane") == "sports_v2" else "main"
        live_strip += (f"<div class='sc'><div class='sl'>"
                       f"{html.escape(t['ticker'][-16:])} · {tag}</div>"
                       f"<div class='sv'>{t['side'].upper()} {t['count']:.0f}ct "
                       f"@ {t['entry']*100:.1f}¢</div>"
                       f"<div class='ss'>risk ${t['entry']*t['count']:.2f}</div></div>")
    _ncell = 5 + len(l_open)
    st.markdown("<div class='sr'><span>Sports lock-lane — LIVE (10ct main + 1ct v2)</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat({_ncell},1fr)'>"
                f"{live_strip}</div>"
                + (f"<div style='margin:-6px 0 10px;padding:8px 12px;background:var(--p1);"
                   f"border:1px solid var(--bd);border-radius:8px'>"
                   f"<span style='font-size:9px;font-weight:700;letter-spacing:.8px;"
                   f"text-transform:uppercase;color:var(--t3);margin-right:8px'>RADAR</span>"
                   f"{chips}</div>" if chips else ""),
                unsafe_allow_html=True)

    st.markdown("<div class='sr'><span>Sports lock-lane — paper trial</span><hr/></div>"
                f"<div class='strip' style='grid-template-columns:repeat(4,1fr)'>"
                f"{strip}</div>", unsafe_allow_html=True)
    # clean per-tour summary (validated band), full matrix in expander
    fam_sum = {}
    for g in val:
        fam = _fam(g)
        c = fam_sum.setdefault(fam, [0, 0, 0, 0.0])
        c[0] += 1
        r = setl.get(g["ticker"])
        if r:
            nv, win = _net(g, r)
            c[1] += 1; c[2] += win; c[3] += nv
    rows_html = ""
    for fam, (n_, ns, w_, net_) in sorted(fam_sum.items(),
                                          key=lambda kv: -kv[1][3]):
        cls = "vg" if net_ > 0 else ("vr" if net_ < 0 else "")
        rows_html += (f"<tr><td>{html.escape(str(fam))}</td><td>{n_}</td>"
                      f"<td>{ns}</td><td>{w_}W/{ns-w_}L</td>"
                      f"<td class='{cls}'>{net_*100:+.1f}¢</td></tr>")
    if rows_html:
        st.markdown("<div class='panel'><table class='tt'>"
                    "<tr><th>tour (validated 97-99¢)</th><th>signals</th>"
                    "<th>settled</th><th>record</th><th>net @1ct</th></tr>"
                    + rows_html + "</table></div>", unsafe_allow_html=True)
    # mini tape: latest validated settlements
    tape = []
    setl_ts = {}
    try:
        for l in open(SP + "/settles.jsonl"):
            r = json.loads(l)
            setl_ts[r["ticker"]] = r["ts"]
    except Exception:
        pass
    for g in val:
        r = setl.get(g["ticker"])
        if r:
            tape.append((setl_ts.get(g["ticker"], 0), g, r))
    tape.sort(key=lambda x: x[0], reverse=True)
    if tape:
        chips = "".join(
            f"<span style='display:inline-block;background:var(--p2);"
            f"border:1px solid var(--bd);border-radius:4px;"
            f"font-family:var(--mono);font-size:10.5px;"
            f"color:{'var(--grn)' if r == g['side'] else 'var(--red)'};"
            f"padding:2px 8px;margin:2px 4px 2px 0'>"
            f"{html.escape(g['ticker'][-14:])} "
            f"{'W' if r == g['side'] else 'L'}</span>"
            for _, g, r in tape[:6])
        st.markdown(f"<div style='margin:-4px 0 10px;padding:8px 12px;"
                    f"background:var(--p1);border:1px solid var(--bd);"
                    f"border-radius:8px'>"
                    f"<span style='font-size:9px;font-weight:700;"
                    f"letter-spacing:.8px;text-transform:uppercase;"
                    f"color:var(--t3);margin-right:8px'>LATEST PAPER SETTLES"
                    f"</span>{chips}</div>", unsafe_allow_html=True)
    # full research matrix, tucked away
    cells = {}
    for g in sigs:
        key = (g.get("lo", 0.97), g["H"], _fam(g))
        c = cells.setdefault(key, [0, 0, 0, 0.0])
        c[0] += 1
        r = setl.get(g["ticker"])
        if r:
            nv, win = _net(g, r)
            c[1] += 1; c[2] += win; c[3] += nv
    with st.expander("Full research table (band × hold × tour) — week-review detail"):
        big = ""
        for (lo, H, fam), (n_, ns, w_, net_) in sorted(
                cells.items(), key=lambda kv: (-kv[0][0], kv[0][1], kv[0][2])):
            tag = "✅" if lo >= 0.97 else "🔬"
            cls = "vg" if net_ > 0 else ("vr" if net_ < 0 else "")
            big += (f"<tr><td>{tag} {lo*100:.0f}¢+</td><td>H{H}</td>"
                    f"<td>{html.escape(str(fam))}</td><td>{n_}</td><td>{ns}</td>"
                    f"<td>{w_}W/{ns-w_}L</td>"
                    f"<td class='{cls}'>{net_*100:+.1f}¢</td></tr>")
        st.markdown("<table class='tt'><tr><th>band</th><th>hold</th>"
                    "<th>tour</th><th>signals</th><th>settled</th>"
                    "<th>record</th><th>net @1ct</th></tr>" + big
                    + "</table>", unsafe_allow_html=True)

    # per-lane P&L split
    lanes = {}
    for t2 in settled:
        ln = t2.get("lane", "r1")
        d2 = lanes.setdefault(ln, [0, 0, 0.0])
        d2[0] += 1
        d2[1] += 1 if t2["result"] == "win" else 0
        d2[2] += t2.get("pnl", 0)
    open_by_lane = {}
    for t2 in opens:
        open_by_lane[t2.get("lane", "r1")] = open_by_lane.get(t2.get("lane", "r1"), 0) + 1
    lane_cells = []
    for ln in ("r1", "stab", "lock", "gas", "adopted"):
        if ln not in lanes and ln not in open_by_lane:
            continue
        n2, w2, p2 = lanes.get(ln, [0, 0, 0.0])
        cls = "vg" if p2 > 0 else ("vr" if p2 < 0 else "")
        op = open_by_lane.get(ln, 0)
        op_txt = f" · {op} open" if op else ""
        lane_cells.append(
            f"<div class='sc'><div class='sl'>{ln} lane</div>"
            f"<div class='sv-big {cls}'>{'+' if p2>0 else ''}${p2:,.2f}</div>"
            f"<div class='ss'>{w2}W/{n2-w2}L settled{op_txt}</div></div>")
    if lane_cells:
        ncols = min(len(lane_cells), 6)
        st.markdown("<div class='sr'><span>Lanes</span><hr/></div>"
                    f"<div class='strip' style='grid-template-columns:repeat({ncols},1fr)'>"
                    f"{''.join(lane_cells)}</div>", unsafe_allow_html=True)



    # every lane's settled rows, one list — the chart population for BOTH
    # the equity curve and the daily bars (temp & cushion were missing)
    _desk_settled = (settled
                     + [t for t in _sp_lt if t.get("result") in ("win", "loss")]
                     + met_set + rain_set + temp_set + cus_set)
    _desk_settled.sort(key=lambda t: t.get("ts", 0))

    st.markdown("<div class='sr'><span>Equity curve — realized P&L</span><hr/></div>",
                unsafe_allow_html=True)
    rng = st.radio("range", ["Today", "1W", "All"], horizontal=True, index=0,
                   key="eq_range", label_visibility="collapsed")
    now_ts = time.time()
    if rng == "Today":
        w0 = datetime.now(ET).replace(hour=0, minute=0, second=0,
                                      microsecond=0).timestamp()
    elif rng == "1W":
        w0 = now_ts - 7 * 86400
    else:
        w0 = 0
    win = [t for t in _desk_settled if t.get("ts", 0) >= w0]
    if win:
        wr_w = sum(1 for t in win if t["result"] == "win")
        wpnl = sum(t.get("pnl", 0) for t in win)
        cls = "vg" if wpnl > 0 else ("vr" if wpnl < 0 else "")
        st.markdown(f"<div class='ss' style='margin:-2px 0 6px'>{rng}: "
                    f"<span class='{cls}' style='font-weight:700'>${wpnl:+.2f}</span>"
                    f" · {wr_w}W/{len(win)-wr_w}L · {len(win)} settled · "
                    f"x-axis ET time · y-axis $ realized P&L (resets at window start)</div>",
                    unsafe_allow_html=True)
    eq_html = equity_chart_html(_desk_settled, rng)
    if eq_html:
        components.html(eq_html, height=300, scrolling=False)
    else:
        st.markdown("<div class='ss'>no settled trades in this window yet</div>",
                    unsafe_allow_html=True)

    st.markdown("<div class='sr'><span>Daily realized P&L</span><hr/></div>",
                unsafe_allow_html=True)
    # Desk-wide daily bars. This used to pass only `settled` -- the GAS
    # ledger -- so 08-07 rendered +$0.23 (gas) while the desk actually made
    # +$0.48. It looked right before only because the gas reconciler was
    # wrongly adopting metals fills into that ledger. Every lane keeps its own
    # ledger, so combine them; each row already carries settled_day and pnl.
    d_html = daily_chart_html(_desk_settled)
    if d_html:
        components.html(d_html, height=248, scrolling=False)
    else:
        st.markdown("<div class='ss'>daily bars appear after first settlement</div>",
                    unsafe_allow_html=True)

    # per-lane trade tables
    def trade_table(title, rows_src, empty_msg):
        out = []
        for t in reversed(rows_src[-12:] if rows_src else []):
            res = t.get("result")
            badge = {"win": "<span class='b b-grn'>WIN</span>",
                     "loss": "<span class='b b-red'>LOSS</span>",
                     "open": "<span class='b b-blu'>OPEN</span>"}.get(res, res)
            pnl = money(t.get("pnl"), plus=True) if "pnl" in t else "—"
            out.append(f"<tr><td>{et_time(t['ts'])}</td>"
                       f"<td>{html.escape(str(t.get('ticker','')))}</td>"
                       f"<td>{t.get('side','').upper()}</td>"
                       f"<td>{t.get('count',0):.0f}ct</td>"
                       f"<td>{t.get('entry',0)*100:.1f}¢</td>"
                       f"<td>{badge}</td>"
                       f"<td class='{pnl_class(t.get('pnl'))}'>{pnl}</td></tr>")
        st.markdown(f"<div class='sr'><span>{title}</span><hr/></div>",
                    unsafe_allow_html=True)
        st.markdown("<div class='panel'><table class='tt'>"
                    "<tr><th>Time (ET)</th><th>Market</th><th>Side</th>"
                    "<th>Size</th><th>Entry</th><th>Result</th><th>P&L</th></tr>"
                    + (''.join(out) if out else
                       f"<tr><td colspan='7' class='ss'>{empty_msg}</td></tr>")
                    + "</table></div>", unsafe_allow_html=True)
    trade_table("Sports trades — live", lt,
                "no live sports bets yet — bot fires on the next 97-99¢ hold")
    trade_table("Gas trades — live", settled + opens,
                "no gas trades yet today")

    # rain trades table
    rrow = []
    for t in reversed((rain_set + rain_open)[-14:]):
        res = t.get("result")
        badge = ("<span class='b b-blu'>OPEN</span>" if res == "open" else
                 ("<span class='b b-grn'>WIN</span>" if res == "win"
                  else "<span class='b b-red'>LOSS</span>"))
        pnl = money(t.get("pnl"), plus=True) if "pnl" in t else "—"
        rrow.append(f"<tr><td>{et_time(t['ts'])}</td>"
                    f"<td><b>{html.escape(t.get('city',''))}</b></td>"
                    f"<td>{t.get('side','').upper()}</td>"
                    f"<td>{t.get('count',0):.0f}ct</td>"
                    f"<td>{t.get('entry',0)*100:.0f}\u00a2</td>"
                    f"<td>{t.get('hours','—')}h</td>"
                    f"<td>{badge}</td>"
                    f"<td class='{pnl_class(t.get('pnl'))}'>{pnl}</td></tr>")
    st.markdown("<div class='sr'><span>Rain trades — live</span><hr/></div>",
                unsafe_allow_html=True)
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Time (ET)</th><th>City</th><th>Side</th><th>Size</th>"
                "<th>Entry</th><th>Hrs left</th><th>Result</th><th>P&L</th></tr>"
                + ("".join(rrow) if rrow else
                   "<tr><td colspan='8' class='ss'>no rain trades yet — fires "
                   "on the next 95-99\u00a2 favourite held 2h inside the final "
                   "8 hours</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # metals trades table
    mrow = []
    for t in reversed((met_set + met_open)[-12:]):
        res = t.get("result")
        badge = ("<span class='b b-blu'>OPEN</span>" if res == "open" else
                 ("<span class='b b-grn'>WIN</span>" if res == "win"
                  else "<span class='b b-red'>LOSS</span>"))
        pnl = money(t.get("pnl"), plus=True) if "pnl" in t else "—"
        mrow.append(f"<tr><td>{et_time(t['ts'])}</td>"
                    f"<td>{html.escape(t.get('ticker','').replace('KX','').replace('15M',''))}</td>"
                    f"<td>{t.get('side','').upper()}</td>"
                    f"<td>{t.get('count',0):.0f}ct</td>"
                    f"<td>{t.get('entry',0)*100:.0f}¢</td>"
                    f"<td>{t.get('rem','—')}</td>"
                    f"<td>{badge}</td>"
                    f"<td class='{pnl_class(t.get('pnl'))}'>{pnl}</td></tr>")
    st.markdown("<div class='sr'><span>Metals trades — live</span><hr/></div>",
                unsafe_allow_html=True)
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Time (ET)</th><th>Market</th><th>Side</th><th>Size</th>"
                "<th>Entry</th><th>Rem</th><th>Result</th><th>P&L</th></tr>"
                + (''.join(mrow) if mrow else
                   "<tr><td colspan='8' class='ss'>no metals trades yet — "
                   "fires on the next unbroken 93-99¢ favourite at 4-8 min left"
                   "</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    # cushion trades table
    crow = []
    for t in reversed((cus_set + cus_open)[-12:]):
        res = t.get("result")
        if res == "stopped":
            _wh = t.get("would_have")
            badge = ("<span class='b b-red'>STOPPED @"
                     f"{(t.get('exit_px') or 0)*100:.0f}\u00a2</span>"
                     + ("<span class='b b-gry'>would have WON</span>"
                        if _wh == "win" else
                        "<span class='b b-grn'>saved</span>"
                        if _wh == "loss" else ""))
        else:
            badge = ("<span class='b b-blu'>OPEN</span>" if res == "open" else
                     ("<span class='b b-grn'>WIN</span>" if res == "win"
                      else "<span class='b b-red'>LOSS</span>"))
        pnl = money(t.get("pnl"), plus=True) if "pnl" in t else "\u2014"
        crow.append(f"<tr><td>{et_time(t['ts'])}</td>"
                    f"<td>{html.escape(t.get('ticker','').replace('KX','').replace('15M',''))}</td>"
                    f"<td>{t.get('side','').upper()}</td>"
                    f"<td>{t.get('count',0):g}ct</td>"
                    f"<td>{t.get('entry',0)*100:.0f}\u00a2</td>"
                    f"<td>{(round(t['rem'],1) if isinstance(t.get('rem'),(int,float)) else '—')}</td>"
                    f"<td>{t.get('z','\u2014')}</td>"
                    f"<td>{badge}</td>"
                    f"<td class='{pnl_class(t.get('pnl'))}'>{pnl}</td></tr>")
    st.markdown("<div class='sr'><span>Legacy crypto cushion trades</span><hr/></div>",
                unsafe_allow_html=True)
    st.markdown("<div class='panel'><table class='tt'>"
                "<tr><th>Time (ET)</th><th>Market</th><th>Side</th><th>Size</th>"
                "<th>Entry</th><th>Rem</th><th>z</th><th>Result</th><th>P&L</th></tr>"
                + (''.join(crow) if crow else
                   "<tr><td colspan='9' class='ss'>no cushion trades yet \u2014 "
                   "fires on the next 85-93\u00a2 favourite with spot \u22651.5\u03c3 "
                   "clear of the strike, 3-10 min left</td></tr>")
                + "</table></div>", unsafe_allow_html=True)

    st.markdown("<div class='sr'><span>Log tail</span><hr/></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='logbox'>{html.escape(chr(10).join(loglines)) or 'no log yet'}</div>",
                unsafe_allow_html=True)
    st.markdown(f"<div class='ss' style='margin-top:8px'>params: band {cfg.get('band_lo')}–{cfg.get('band_hi')} · "
                f"rem {cfg.get('rem_lo')}–{cfg.get('rem_hi')} · spread ≤{cfg.get('max_spread')} · "
                f"maker wait {cfg.get('maker_wait_s')}s · halt file: state/HALT · auto-refresh 30s</div>",
                unsafe_allow_html=True)


render()
