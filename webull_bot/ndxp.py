"""NDXP ad-hoc force-entry tool — manual one-shot, NOT a daemon.

This file is fully isolated from the SPX daemon. It does not import or modify
anything in main.py, and the running daemon doesn't import this module.

Reuses (read-only) the existing modules:
    execution, monitor, market_data, ibkr_market_data, alerts, state

Lives on its own state file (spx_spread_bot/data/webull_live/ndxp/state.json)
so it cannot collide with the SPX state.

Usage:
    python -m webull_bot.ndxp --report
        Read-only. Fetches NDX chain, prints top spreads, pushes Telegram
        report. NO ORDER PLACED.

    python -m webull_bot.ndxp --force
        Interactive. Shows recommendations → asks pick → asks 'yes' →
        places exactly ONE force-market order → spawns background monitor.

    python -m webull_bot.ndxp --status
        Print current NDXP position from state file (if any). Read-only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from webull_bot.alerts import (
    alert_entry, alert_force_entry, alert_force_report,
)
from webull_bot.client import build_trade_client
from webull_bot.execution import ExecutionEngine, ALLOWED_OPTION_SYMBOLS
from webull_bot.market_data import (
    find_top_spreads, get_0dte_expiry, get_spx_open, get_spx_price,
    get_vix_open, get_vix_price,
)
from webull_bot.state import BotState, OpenPosition, StateStore

ET = ZoneInfo("America/New_York")


# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    """Load config_ndxp.yaml. Resolves path relative to repo root if cwd differs."""
    here = Path(__file__).resolve().parent
    candidates = [
        Path("webull_bot/config_ndxp.yaml"),
        here / "config_ndxp.yaml",
    ]
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("config_ndxp.yaml not found")


# ── IBKR spot for NDX (separate from SPX so SPX daemon's clientId=42 isn't disturbed) ─

def _get_ndx_spot() -> float | None:
    """Real-time NDX spot from IBKR, falling back to yfinance.

    Uses the auto-bump clientId logic in ibkr_market_data._connect, so this
    coexists with the SPX daemon's persistent clientId=42 session.
    """
    try:
        from webull_bot.ibkr_market_data import get_index_spot_ibkr
        v = get_index_spot_ibkr("NDX")
        if v and v > 1000:
            return v
    except Exception:
        pass
    return None


def _get_ndx_yf() -> float:
    import yfinance as yf
    info = yf.Ticker("^NDX").fast_info
    p = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    if p and p > 1000:
        return float(p)
    h = yf.Ticker("^NDX").history(period="1d", interval="1m")
    if not h.empty:
        return float(h["Close"].iloc[-1])
    raise RuntimeError("Cannot fetch NDX spot")


def _ndx_spot_with_source() -> tuple[float, str]:
    v = _get_ndx_spot()
    if v:
        return v, "IBKR"
    return _get_ndx_yf(), "yfinance"


def _get_ndx_prev_close() -> float | None:
    """Yesterday's NDX closing price via yfinance daily bars. None on failure."""
    try:
        import yfinance as yf
        from datetime import date
        hist = yf.Ticker("^NDX").history(period="3d", interval="1d")
        today = date.today()
        prev = hist[hist.index.date < today]
        if prev.empty:
            return None
        return float(prev["Close"].iloc[-1])
    except Exception:
        return None


# ── Chain scan with strike spacing aware of NDX 25-pt grid ─────────────────────

def _scan_top_spreads_ibkr(spx_price: float, otm_pct: float, spread_width: float,
                            expiry: str, top_n: int = 4):
    """Try IBKR for NDX option chain; return list of dicts or None on failure."""
    try:
        from webull_bot.ibkr_market_data import _connect, _valid
        from ib_insync import Option
        ib = _connect()
        if ib is None:
            return None

        exp_ibkr = expiry.replace("-", "")
        # NDX strikes are spaced by 25pt typically — build candidates on the 25-grid
        target_short = round(spx_price * (1.0 - otm_pct) / 25) * 25
        candidate_shorts = sorted({target_short + 25 * i for i in range(-5, 6)})
        candidate_longs = {s - spread_width for s in candidate_shorts}
        all_strikes = sorted(set(candidate_shorts) | candidate_longs)

        # NDXP trading class on CBOE
        contracts = [
            Option("NDX", exp_ibkr, k, "P", exchange="SMART",
                   tradingClass="NDXP", currency="USD", multiplier="100")
            for k in all_strikes
        ]
        q = ib.qualifyContracts(*contracts)
        qualified = {c.strike: c for c in q if getattr(c, "conId", 0)}
        if not qualified:
            return None

        tickers = {k: ib.reqMktData(c, "", False, False) for k, c in qualified.items()}
        ib.sleep(2.5)

        def mid_of(t):
            b = _valid(t.bid); a = _valid(t.ask)
            m = (b + a) / 2.0 if (b and a) else (_valid(t.last) or _valid(t.close))
            return b, a, (round(m, 2) if m else None)

        leg_quotes = {}
        for k, t in tickers.items():
            leg_quotes[k] = mid_of(t)
            try: ib.cancelMktData(qualified[k])
            except Exception: pass

        results = []
        for short_k in candidate_shorts:
            long_k = short_k - spread_width
            sb, sa, sm = leg_quotes.get(short_k, (None, None, None))
            lb, la, lm = leg_quotes.get(long_k, (None, None, None))
            if sm is None or lm is None:
                continue
            net_mid = round(sm - lm, 2)
            if net_mid <= 0:
                continue
            spread_bid = round((sb or 0) - (la or 0), 2) if (sb and la) else net_mid
            spread_ask = round((sa or 0) - (lb or 0), 2) if (sa and lb) else net_mid
            results.append({
                "short_strike": float(short_k),
                "long_strike":  float(long_k),
                "expiry":       expiry,
                "mid":          net_mid,
                "bid":          spread_bid,
                "ask":          spread_ask,
            })
        if not results:
            return None
        results.sort(key=lambda r: r["mid"], reverse=True)
        return results[:top_n]
    except Exception:
        return None


# Lightweight namespace mimic of SpreadQuote so existing alert_force_report works
class _Spread:
    def __init__(self, short_strike, long_strike, expiry, mid, bid, ask):
        self.short_strike = short_strike
        self.long_strike  = long_strike
        self.expiry       = expiry
        self.mid          = mid
        self.bid          = bid
        self.ask          = ask


def _scan_top_spreads(spx_price: float, cfg: dict, expiry: str, top_n: int = 4):
    """IBKR-first scan with yfinance fallback. Returns (list[_Spread], source_label)."""
    r = _scan_top_spreads_ibkr(spx_price, cfg["otm_pct"], cfg["spread_width"], expiry, top_n)
    if r:
        return [_Spread(**d) for d in r], "IBKR"
    # yfinance fallback
    yf_spreads = find_top_spreads(
        spx_price=spx_price,
        otm_pct=cfg["otm_pct"],
        spread_width=cfg["spread_width"],
        yf_options_symbol=cfg["yf_options_symbol"],
        expiry=expiry,
        top_n=top_n,
    )
    return yf_spreads, "yfinance"


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_report() -> None:
    """Print + Telegram-push a force report. Read-only. No orders."""
    cfg = _load_cfg()
    today = date.today()
    expiry = get_0dte_expiry(cfg["yf_options_symbol"])
    if expiry is None:
        msg = "NDXP: no 0DTE expiry for today."
        print(msg)
        alert_force_report(spx=0, spx_pct_from_open=0, vix=0,
            vix_delta_from_open=0, vix_in_zone=False, spreads=[], source="n/a")
        return

    ndx, src_spot = _ndx_spot_with_source()
    vix = get_vix_price()
    ndx_open = get_spx_open(today, cfg["yf_price_symbol"])
    vix_open = get_vix_open(today)

    spreads, src_chain = _scan_top_spreads(ndx, cfg, expiry, top_n=4)
    ndx_pct = ((ndx - ndx_open) / ndx_open * 100) if ndx_open else 0
    ndx_prev = _get_ndx_prev_close()
    ndx_pct_pc = ((ndx - ndx_prev) / ndx_prev * 100) if ndx_prev else None
    vix_delta = (vix - vix_open) if vix_open else 0
    vix_zone = "✓ in zone (12-25)" if 12 <= vix <= 25 else "✗ outside zone"

    if ndx_pct_pc is not None:
        ndx_line = (f"NDX : {ndx:,.2f}   {ndx_pct:+.2f}% from open"
                    f"   {ndx_pct_pc:+.2f}% from prev close")
    else:
        ndx_line = f"NDX : {ndx:,.2f}   {ndx_pct:+.2f}% from open"

    print()
    print(ndx_line)
    print(f"VIX : {vix:.2f}     {vix_zone}   {vix_delta:+.2f} from open")
    print(" #   Strikes              OTM%    Mid    Bid    Ask")
    best_mid = 0.0
    for i, s in enumerate(spreads, 1):
        otm = (ndx - s.short_strike) / ndx * 100
        print(f" {i}   {int(s.short_strike)}/{int(s.long_strike)}P    {otm:.1f}%   {s.mid:.2f}   {s.bid:.2f}   {s.ask:.2f}")
        if s.mid > best_mid:
            best_mid = s.mid
    src_label = src_chain if src_chain == src_spot else f"{src_chain} (chain) / {src_spot} (spot)"
    print(f" data: {src_label}")

    # Reuse SPX alert helper — pass NDX-specific label/title
    alert_force_report(
        spx=ndx, spx_pct_from_open=ndx_pct,
        spx_pct_from_prev_close=ndx_pct_pc,
        vix=vix, vix_delta_from_open=vix_delta,
        vix_in_zone=(12 <= vix <= 25),
        spreads=spreads, source=src_label,
        min_credit=cfg["min_credit"],
        symbol_label="NDX",
        title="NDXP FORCE ENTRY REPORT",
    )
    time.sleep(2)


def cmd_status() -> None:
    """Print NDXP position state, if any."""
    cfg = _load_cfg()
    store = StateStore(cfg["state_file"])
    state = store.load()
    print(f"trading_date     : {state.trading_date}")
    print(f"trade_taken_today: {state.trade_taken_today}")
    if state.open_position:
        op = state.open_position
        print(f"open_position    : {int(op.short_strike)}/{int(op.long_strike)}P  qty={op.quantity}  credit={op.entry_credit:.2f}  stop={op.stop_price:.2f}")
        print(f"  iids: short={op.short_iid}  long={op.long_iid}")
    else:
        print("open_position    : none")
    print(f"record           : {state.wins}W {state.losses}L  total_pnl=${state.total_pnl:.2f}")


def cmd_force() -> None:
    """Interactive force-entry. Place exactly ONE NDXP BPS contract.

    Per user-mandated protocol (memory/feedback_force_order_protocol.md):
    - Scans ALL existing option positions first (any symbol)
    - Shows them with status
    - Requires explicit 'yes' to proceed when other positions exist
    - Then triple-confirms via report → pick → final yes
    """
    cfg = _load_cfg()
    today = date.today()
    expiry = get_0dte_expiry(cfg["yf_options_symbol"])
    if expiry is None:
        print("[NDXP] no 0DTE expiry available — aborting.")
        return

    # ── Step 0: existing-position safety gate ────────────────────────────
    print("[NDXP] checking existing option positions ...")
    trade_client = build_trade_client()
    try:
        all_h = []
        last = None
        for _ in range(5):
            r = (trade_client.account.get_account_position(
                    account_id=cfg["account_id"], last_instrument_id=last)
                 if last else
                 trade_client.account.get_account_position(account_id=cfg["account_id"]))
            d = r.json()
            hs = d.get("holdings", [])
            all_h.extend(hs)
            if not d.get("has_next") or not hs: break
            last = hs[-1]["instrument_id"]

        existing_opts = [
            h for h in all_h
            if h.get("symbol") in ALLOWED_OPTION_SYMBOLS
            and h.get("instrument_type") == "OPTION"
        ]
    except Exception as exc:
        print(f"[NDXP] could not verify existing positions ({exc}) — aborting for safety.")
        return

    if existing_opts:
        print()
        print("="*62)
        print("  ⚠  EXISTING OPTION POSITIONS")
        print("="*62)
        for h in existing_opts:
            sym = h.get("symbol")
            iid = h.get("instrument_id")
            qty = h.get("qty")
            cost = h.get("unit_cost")
            lp   = h.get("last_price")
            upl  = h.get("unrealized_profit_loss")
            print(f"  {sym}  iid={iid}  qty={qty}  unit_cost={cost}  last={lp}  upl={upl}")
        print("="*62)
        print(f"  You have {len(existing_opts)} open option leg(s).")
        print("  Placing a new NDXP BPS adds correlated equity-index exposure.")
        print()
        gate = input(
            "  Knowing this, type 'yes' to proceed with a NEW NDXP entry, "
            "anything else to abort: "
        ).strip().lower()
        if gate != "yes":
            print("[NDXP] aborted — existing-positions gate declined.")
            return
        print(f"[NDXP] gate cleared — proceeding with {len(existing_opts)} existing leg(s).")
    else:
        print("[NDXP] no existing option positions ✓")

    # ── Step 1: scan + show report ───────────────────────────────────────
    ndx, src_spot = _ndx_spot_with_source()
    vix = get_vix_price()
    ndx_open = get_spx_open(today, cfg["yf_price_symbol"])
    vix_open = get_vix_open(today)
    spreads, src_chain = _scan_top_spreads(ndx, cfg, expiry, top_n=4)

    if not spreads:
        print("[NDXP] no spreads available — aborting.")
        return

    ndx_pct = ((ndx - ndx_open) / ndx_open * 100) if ndx_open else 0
    ndx_prev = _get_ndx_prev_close()
    ndx_pct_pc = ((ndx - ndx_prev) / ndx_prev * 100) if ndx_prev else None
    vix_delta = (vix - vix_open) if vix_open else 0
    vix_zone = "✓ in zone (12-25)" if 12 <= vix <= 25 else "✗ outside zone"

    if ndx_pct_pc is not None:
        ndx_line = (f"NDX : {ndx:,.2f}   {ndx_pct:+.2f}% from open"
                    f"   {ndx_pct_pc:+.2f}% from prev close")
    else:
        ndx_line = f"NDX : {ndx:,.2f}   {ndx_pct:+.2f}% from open"

    print()
    print(ndx_line)
    print(f"VIX : {vix:.2f}     {vix_zone}   {vix_delta:+.2f} from open")
    print(" #   Strikes              OTM%    Mid    Bid    Ask")
    for i, s in enumerate(spreads, 1):
        otm = (ndx - s.short_strike) / ndx * 100
        print(f" {i}   {int(s.short_strike)}/{int(s.long_strike)}P    {otm:.1f}%   {s.mid:.2f}   {s.bid:.2f}   {s.ask:.2f}")
    print(f" data: {src_chain}")

    # ── Step 2: ask for pick ─────────────────────────────────────────────
    print()
    pick_raw = input(f"Pick a number (1-{len(spreads)}) or 'no' to abort: ").strip().lower()
    if pick_raw in ("no", "n", "", "q"):
        print("[NDXP] aborted.")
        return
    try:
        pick = int(pick_raw)
        spread = spreads[pick - 1]
    except (ValueError, IndexError):
        print(f"[NDXP] invalid pick '{pick_raw}' — aborted.")
        return

    # ── Step 3: confirmation ─────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  ⚠  CONFIRM NDXP MARKET ORDER")
    print(f"{'='*62}")
    print(f"  Symbol      : NDXP")
    print(f"  Expiry      : {spread.expiry}")
    print(f"  Short put   : {int(spread.short_strike)} (sell to open)")
    print(f"  Long put    : {int(spread.long_strike)} (buy to open)")
    print(f"  Width       : {int(spread.short_strike - spread.long_strike)} pts")
    print(f"  Qty         : 1 contract")
    print(f"  Order type  : MARKET (aggressive limit)")
    print(f"  Credit est  : {spread.mid:.2f} mid  |  {spread.bid:.2f} bid  |  {spread.ask:.2f} ask")
    print(f"  Approx margin: ~${(spread.short_strike - spread.long_strike) * 100 - spread.mid * 100:.0f}")
    print(f"{'='*62}")
    confirm = input("\n  Type 'yes' to place, anything else to abort: ").strip().lower()
    if confirm != "yes":
        print("[NDXP] aborted — no order placed.")
        return

    # ── Step 4: stale-quote guard ───────────────────────────────────────
    max_drift = float(cfg.get("force_entry_max_drift_pct", 0.30))
    try:
        ndx_now, _ = _ndx_spot_with_source()
    except Exception as exc:
        print(f"[NDXP] aborted — could not re-fetch NDX ({exc})")
        return
    drift = abs(ndx_now - ndx) / ndx * 100
    print(f"[NDXP] stale-quote check  scan={ndx:.2f}  now={ndx_now:.2f}  drift={drift:.2f}% (max {max_drift:.2f}%)")
    if drift > max_drift:
        print(f"[NDXP] ⚠  ABORTED — NDX moved {drift:.2f}% since scan.")
        return

    # ── Step 5: refuse EXACT-duplicate guard ──────────────────────────────
    # User already confirmed at Step 0 with the full list of existing positions.
    # This last-line guard only refuses if the NEW order would EXACTLY duplicate
    # an existing NDXP position with the same short/long strikes — that's a
    # blind double-up which is never desired even with explicit confirmation.
    # Different strikes or different symbols are fine (user already consented).
    ex = ExecutionEngine(trade_client, cfg["account_id"])
    state_now = StateStore(cfg["state_file"]).load()
    if state_now.open_position:
        op = state_now.open_position
        if (op.symbol in ("NDXP", "NDX")
            and float(op.short_strike) == spread.short_strike
            and float(op.long_strike) == spread.long_strike
            and op.expiry == spread.expiry):
            print(f"[NDXP] ABORTED — exact-duplicate of state-saved position "
                  f"{int(op.short_strike)}/{int(op.long_strike)}P  expiry={op.expiry}")
            return

    # ── Step 6: lock state BEFORE sending ───────────────────────────────
    store = StateStore(cfg["state_file"])
    state = store.load()
    state.trading_date = today.isoformat()
    state.trade_taken_today = True
    store.save(state)
    print("[NDXP] state locked, placing market order ...")

    # ── Step 7: place ONE order ─────────────────────────────────────────
    fill = ex.place_spread_market(
        symbol="NDXP",
        expiry=spread.expiry,
        short_strike=spread.short_strike,
        long_strike=spread.long_strike,
        quantity=1,
    )
    print(f"\n[NDXP] {fill.status}  {fill.detail}")

    if not fill.filled:
        print("[NDXP] not filled — rolling back trade_taken_today.")
        state.trade_taken_today = False
        store.save(state)
        alert_force_entry(
            spread=f"{int(spread.short_strike)}/{int(spread.long_strike)}P",
            qty=1, fill_price=0.0, filled=False,
        )
        return

    # ── Step 8: save position ───────────────────────────────────────────
    stop_price = round(fill.fill_price * cfg["stop_multiplier"], 2)
    pos = OpenPosition(
        symbol="NDXP",
        expiry=spread.expiry,
        short_strike=spread.short_strike,
        long_strike=spread.long_strike,
        quantity=1,
        entry_credit=fill.fill_price,
        stop_price=stop_price,
        entry_spx=ndx,
        entry_vix=vix,
        entry_ts=datetime.now(ET).isoformat(),
        client_order_id=fill.client_order_id,
        yf_options_symbol="NDX",
        short_iid=fill.short_iid,
        long_iid=fill.long_iid,
    )
    state.open_position = pos
    store.save(state)
    print(f"[NDXP] state saved  credit={fill.fill_price:.2f}  stop={stop_price:.2f}")
    print(f"[NDXP] leg iids: short={fill.short_iid}  long={fill.long_iid}")

    alert_entry(
        spread=f"{int(spread.short_strike)}/{int(spread.long_strike)}P",
        qty=1, credit=fill.fill_price,
        width=int(spread.short_strike - spread.long_strike),
        spx=ndx, vix=vix, stop_price=stop_price,
    )

    # ── Step 9: spawn detached background monitor ───────────────────────
    monitor_cmd = [
        sys.executable, "-m", "webull_bot.ndxp", "--monitor",
    ]
    log_path = Path(cfg.get("log_dir", "webull_bot/logs/ndxp"))
    log_path.mkdir(parents=True, exist_ok=True)
    out = open(log_path / "monitor.out", "ab")
    err = open(log_path / "monitor.err", "ab")
    p = subprocess.Popen(
        monitor_cmd, stdout=out, stderr=err,
        start_new_session=True,  # detach
    )
    print(f"[NDXP] background monitor started (PID {p.pid}) → {log_path}/monitor.out")


def cmd_monitor() -> None:
    """Foreground monitor loop. Used by --force as a detached child process."""
    from webull_bot.logger import BotLogger
    from webull_bot.monitor import PositionMonitor

    cfg = _load_cfg()
    trade_client = build_trade_client()
    ex = ExecutionEngine(trade_client, cfg["account_id"])
    store = StateStore(cfg["state_file"])
    state = store.load()
    if state.open_position is None:
        print("[NDXP monitor] no open position — exiting.")
        return

    logger = BotLogger(log_dir=cfg.get("log_dir", "webull_bot/logs/ndxp"))
    monitor = PositionMonitor(
        execution=ex,
        store=store,
        logger=logger,
        monitor_interval_seconds=cfg.get("monitor_interval_seconds", 2),
        eod_close_time=cfg.get("eod_close_time", "15:45"),
    )
    outcome = monitor.run_until_closed(state)
    print(f"[NDXP monitor] closed: {outcome.reason}  pnl=${outcome.pnl_usd:.0f}")


def main() -> None:
    p = argparse.ArgumentParser(description="NDXP ad-hoc force-entry tool.")
    p.add_argument("--report",  action="store_true", help="Show recommendations (read-only)")
    p.add_argument("--force",   action="store_true", help="Interactive: pick + confirm + place ONE order")
    p.add_argument("--status",  action="store_true", help="Print current NDXP position state")
    p.add_argument("--monitor", action="store_true", help="(internal) Run the SL monitor loop in foreground")
    args = p.parse_args()

    if args.report:    cmd_report()
    elif args.force:   cmd_force()
    elif args.status:  cmd_status()
    elif args.monitor: cmd_monitor()
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
