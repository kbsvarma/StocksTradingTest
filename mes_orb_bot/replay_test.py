"""
replay_test.py — Deterministic state-machine harness.

Replays pre-built 1-minute bar scenarios through ORBStrategy + RiskManager
without any broker connection.  All exit logic (VWAP_STOP, bracket TARGET,
HARD_CLOSE) is tested alongside the broker stop exit.

Usage:
    python replay_test.py                  # run all scenarios
    python replay_test.py vwap_stop        # run one scenario by name
    python replay_test.py --list           # list available scenarios

Exit priority (matches bot.py _handle_in_trade):
  broker STOP / TARGET → VWAP_STOP → HARD_CLOSE

Fill simulation (conservative):
  Entry   : filled at the open of the bar AFTER the signal bar
  Stop    : bar.low  <= stop_price  (long)  → filled at stop_price
  Target  : bar.high >= target_price (long)  → filled at target_price
  Both    : stop wins (conservative)
  VWAP    : checked only when bars_in_trade >= VWAP_MIN_BARS AND stop/target not hit

Timing (matches config.py):
  Range window  : 09:30–09:59  (30 bars, locked at 10:00)
  Entry cutoff  : 15:15
  Hard close    : 15:15
  Safety target : entry ± 3 × width  (3:1 R:R — validated OOS configuration)
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional
from zoneinfo import ZoneInfo

import config
import logger as L
from strategy import Bar, ORBStrategy
from risk import RiskManager


TZ = ZoneInfo(config.TIMEZONE)

# ── Time helpers ───────────────────────────────────────────────────────────────

def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)

_RANGE_START_MIN = _min(config.RANGE_START_TIME)   # 570 = 09:30
_RANGE_END_MIN   = _min(config.RANGE_END_TIME)     # 600 = 10:00
_ENTRY_CUTOFF    = _min(config.ENTRY_CUTOFF_TIME)  # 915 = 15:15
_HARD_CLOSE_MIN  = _min(config.HARD_CLOSE_TIME)    # 915 = 15:15

# ── Bar factory helpers ────────────────────────────────────────────────────────

def _dt(h: int, m: int) -> datetime:
    return datetime(2025, 1, 2, h, m, 0, tzinfo=TZ)

def bar(h: int, m: int, o: float, hi: float, lo: float, c: float, v: int = 5000) -> Bar:
    return Bar(_dt(h, m), o, hi, lo, c, v)

def bars_seq(
    start_h: int, start_m: int, count: int,
    o: float, hi: float, lo: float, c: float, v: int = 5000,
) -> List[Bar]:
    """Generate `count` consecutive 1-minute bars. Handles hour overflow."""
    result = []
    for i in range(count):
        total = start_h * 60 + start_m + i
        result.append(bar(total // 60, total % 60, o, hi, lo, c, v))
    return result


# ── Scenarios ──────────────────────────────────────────────────────────────────
#
# All standard scenarios share:
#   Range H=5005, L=4995, width=10pts, locked at 10:00
#   Safety target LONG : entry + 3×10 = entry+30  (SAFETY_TARGET_MULT=3.0)
#   Safety target SHORT: entry - 3×10 = entry-30
#   Hard close / entry cutoff: 15:15

@dataclass
class Scenario:
    name: str
    description: str
    prev_close: float
    vix: Optional[float]
    bars: List[Bar]
    expected_outcome: str   # WIN | LOSS | NO_TRADE | HALTED


def _make_scenarios() -> dict:
    s = {}

    # ── long_win ───────────────────────────────────────────────────────────────
    # LONG breakout at 10:00; price drifts up all day above VWAP.
    # VWAP_STOP never fires (close=5018 always > VWAP).
    # Broker stop/target never hit. Exits at HARD_CLOSE 15:15 → WIN.
    #
    # 10:02–15:15 = minutes 602–915 → 314 drift bars
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5002)
    sig  = [bar(10, 0,  5004, 5010, 5003, 5007)]          # close=5007 > H=5005 → LONG
    ent  = [bar(10, 1,  5006, 5010, 5005, 5008)]          # fill @ open=5006
    drft = bars_seq(10, 2, 314, 5015, 5022, 5013, 5018)   # close=5018 well above VWAP
    # bars_seq(10,2,314): 10:02(602) … 15:15(915)  ← 15:15 bar triggers hard close
    s["long_win"] = Scenario(
        name="long_win",
        description="LONG breakout → price drifts up → HARD_CLOSE at 15:15 (WIN)",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + sig + ent + drft,
        expected_outcome="WIN",
    )

    # ── long_stop_loss ─────────────────────────────────────────────────────────
    # LONG entry at 5006; first real in-trade bar has a spike low below stop.
    # close=5002 > VWAP≈5001.67 so VWAP_STOP does NOT fire.
    # broker stop fires (low=4988 < 4995). EXIT @ stop=4995 → LOSS.
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5002)
    sig  = [bar(10, 0,  5004, 5010, 5003, 5007)]
    ent  = [bar(10, 1,  5006, 5006, 5006, 5006)]           # entry bar: price holds at open
    spk  = [bar(10, 2,  5005, 5006, 4988, 5002)]           # spike: low=4988<stop=4995; close=5002>VWAP
    s["long_stop_loss"] = Scenario(
        name="long_stop_loss",
        description="LONG entry → spike low through stop (4988<4995); close>VWAP → STOP @ 4995 (LOSS)",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + sig + ent + spk,
        expected_outcome="LOSS",
    )

    # ── no_trade ───────────────────────────────────────────────────────────────
    # Range locks at 10:00; price never closes outside 4995-5005 all day.
    # At 15:15 entry-cutoff fires → DAILY_DONE / NO_TRADE.
    #
    # 10:00–15:15 = minutes 600–915 → 316 no-signal bars
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5002)
    flat = bars_seq(10, 0, 316, 5003, 5005, 4995, 5002)    # close always inside range
    s["no_trade"] = Scenario(
        name="no_trade",
        description="Price stays inside range all day → entry cutoff 15:15 → NO_TRADE",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + flat,
        expected_outcome="NO_TRADE",
    )

    # ── vix_halt ───────────────────────────────────────────────────────────────
    s["vix_halt"] = Scenario(
        name="vix_halt",
        description="VIX=30 ≥ threshold (25) → HALTED before range builds",
        prev_close=4995.0,
        vix=30.0,
        bars=[bar(9, 29, 5000, 5001, 4999, 5000)],
        expected_outcome="HALTED",
    )

    # ── gap_halt ───────────────────────────────────────────────────────────────
    # prev_close=5000, today_open=5060 → gap=1.2% > GAP_THRESHOLD_PCT=1% → HALTED
    s["gap_halt"] = Scenario(
        name="gap_halt",
        description="1.2% overnight gap (5000→5060) → HALTED at open",
        prev_close=5000.0,
        vix=14.5,
        bars=[bar(9, 30, 5060, 5070, 5055, 5065)],
        expected_outcome="HALTED",
    )

    # ── narrow_range ───────────────────────────────────────────────────────────
    # Range width = 0.75pts < MIN_OPENING_RANGE_POINTS=5.0 → HALTED after lock.
    # (ATR not set in replay → fixed fallback applies)
    rng  = bars_seq(9, 30, 30, 5000, 5000.5, 4999.75, 5000.25)
    lock = [bar(10, 0, 5001, 5002, 4999, 5001)]
    s["narrow_range"] = Scenario(
        name="narrow_range",
        description="Opening range width=0.75pts < 5pt fixed minimum → HALTED",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + lock,
        expected_outcome="HALTED",
    )

    # ── hard_close ─────────────────────────────────────────────────────────────
    # No signal until 14:00; LONG entry at 14:01; drift bars to 15:15.
    # VWAP stays below close throughout. Hard close 15:15 → WIN.
    #
    # 10:00–13:59 = minutes 600–839 → 240 no-signal bars
    # 14:02–15:15 = minutes 842–915 → 74 drift bars
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5002)
    flat = bars_seq(10, 0, 240, 5003, 5005, 4995, 5002)    # 10:00–13:59
    sig  = [bar(14, 0,  5004, 5010, 5003, 5007)]           # LONG signal at 14:00
    ent  = [bar(14, 1,  5006, 5008, 5004, 5007)]           # fill @ 5006
    drft = bars_seq(14, 2, 74, 5014, 5020, 5012, 5016)     # 14:02–15:15 (74th bar = 15:15)
    s["hard_close"] = Scenario(
        name="hard_close",
        description="Late LONG entry at 14:00; drift above VWAP → HARD_CLOSE at 15:15 (WIN)",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + flat + sig + ent + drft,
        expected_outcome="WIN",
    )

    # ── vwap_stop ──────────────────────────────────────────────────────────────
    # LONG entry at 5006; VWAP≈5001.67 after entry bar.
    # First in-trade bar: close=5001 < VWAP → VWAP_STOP fires → LOSS.
    # Broker stop NOT hit (low=4998 > stop=4995).
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5004)     # typical≈5001.33
    sig  = [bar(10, 0,  5004, 5010, 5003, 5007)]
    ent  = [bar(10, 1,  5006, 5010, 5004, 5007)]            # entry bar SKIP
    vst  = [bar(10, 2,  5003, 5005, 4998, 5001)]            # close=5001 < VWAP≈5001.66
    s["vwap_stop"] = Scenario(
        name="vwap_stop",
        description="LONG entry → close drops below VWAP (5001<5001.7) → VWAP_STOP (LOSS)",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + sig + ent + vst,
        expected_outcome="LOSS",
    )

    # ── long_target ────────────────────────────────────────────────────────────
    # LONG entry at 5006; range H=5005, L=4995, width=10.
    # target = entry + SAFETY_TARGET_MULT × width = 5006 + 3×10 = 5036.
    # Entry bar 10:01: high=5010 < 5036, low=5004 > 4995 → neither bracket leg hit.
    # In-trade bar 10:02: high=5040 >= 5036 → TARGET fires before VWAP check.
    # pnl=30pts, gross=$600, net=$582 → WIN.
    # Validates SAFETY_TARGET_MULT=3.0 (changed from 15.0 in quant review).
    rng  = bars_seq(9, 30, 30, 5000, 5005, 4995, 5002)
    sig  = [bar(10, 0,  5004, 5010, 5003, 5007)]            # close=5007 > H+1tick → LONG
    ent  = [bar(10, 1,  5006, 5010, 5004, 5007)]            # fill @ open=5006 (entry bar SKIP)
    tgt  = [bar(10, 2,  5010, 5040, 5030, 5036)]            # high=5040 >= target=5036 → TARGET
    s["long_target"] = Scenario(
        name="long_target",
        description="LONG entry → price surges to 3×-range target (5036) → TARGET exit (WIN)",
        prev_close=4995.0,
        vix=14.5,
        bars=rng + sig + ent + tgt,
        expected_outcome="WIN",
    )

    return s


# ── Replay engine ──────────────────────────────────────────────────────────────

@dataclass
class ReplayResult:
    scenario: str
    outcome: str      # WIN | LOSS | NO_TRADE | HALTED | ERROR
    expected: str
    passed: bool
    close_reason: Optional[str] = None
    pnl_pts: Optional[float] = None
    gross_usd: Optional[float] = None
    net_usd: Optional[float] = None
    state_log: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def run_scenario(scenario: Scenario, verbose: bool = True) -> ReplayResult:
    strategy = ORBStrategy()
    # Replay tests verify state-machine mechanics, not direction filtering.
    # allowed_direction=None (both sides active), daily_atr=None (fixed fallback).
    risk = RiskManager()

    result = ReplayResult(
        scenario=scenario.name,
        outcome="NO_TRADE",
        expected=scenario.expected_outcome,
        passed=False,
    )

    risk.set_prev_close(scenario.prev_close)
    risk.set_vix(scenario.vix)

    from enum import Enum
    class RS(Enum):
        STARTUP        = "STARTUP"
        RANGE_BUILDING = "RANGE_BUILDING"
        WAITING_ENTRY  = "WAITING_ENTRY"
        IN_TRADE_LONG  = "IN_TRADE_LONG"
        IN_TRADE_SHORT = "IN_TRADE_SHORT"
        DAILY_DONE     = "DAILY_DONE"
        HALTED         = "HALTED"
        CLOSED         = "CLOSED"

    state = RS.STARTUP
    direction: Optional[str]  = None
    entry_price: Optional[float] = None
    stop_price: Optional[float]  = None
    target_price: Optional[float] = None
    fill_next_bar: bool = False
    entry_bar_skip: bool = False   # True on fill bar — skip in-trade logic for that bar
    today_open_set: bool = False

    def transition(new_s, reason: str) -> None:
        nonlocal state
        msg = f"  {state.value:22s} → {new_s.value:22s} | {reason}"
        result.state_log.append(msg)
        if verbose:
            print(msg)
        state = new_s

    def record_exit(exit_px: float, reason: str) -> None:
        nonlocal entry_price, direction
        pnl_pts = (exit_px - entry_price) if direction == "LONG" else (entry_price - exit_px)
        gross   = pnl_pts * config.MULTIPLIER * config.CONTRACTS
        commission = config.COMMISSION_PER_CONTRACT * 2 * config.CONTRACTS
        net     = gross - commission
        risk.record_trade(gross)
        result.pnl_pts    = pnl_pts
        result.gross_usd  = gross
        result.net_usd    = net
        result.close_reason = reason
        result.outcome    = "WIN" if pnl_pts > 0 else "LOSS"
        note = (f"  EXIT {reason} @ {exit_px:.2f} | "
                f"pnl={pnl_pts:+.4f}pts "
                f"gross=${gross:+.2f} commission=${commission:.2f} net=${net:+.2f}")
        result.notes.append(note)
        if verbose:
            print(note)
        transition(RS.DAILY_DONE, f"{reason} @ {exit_px:.2f}")

    # ── Main bar loop ──────────────────────────────────────────────────────────
    for b in scenario.bars:
        t    = b.timestamp.time()
        bmin = t.hour * 60 + t.minute

        # ── Capture today's open ───────────────────────────────────────────────
        if not today_open_set and bmin >= _RANGE_START_MIN:
            risk.set_today_open(b.open)
            today_open_set = True

        # ── VIX check fires on the 09:29 bar ──────────────────────────────────
        if bmin == (_RANGE_START_MIN - 1):    # 09:29
            vix_res = risk.check_vix()
            if not vix_res.passed:
                transition(RS.HALTED, f"VIX FAIL: {vix_res.reason}")
                result.outcome = "HALTED"
                break

        # ── Accumulate VWAP for every bar from 09:30 onwards ──────────────────
        if bmin >= _RANGE_START_MIN:
            strategy.update_vwap(b)

        # ── STARTUP → RANGE_BUILDING ───────────────────────────────────────────
        if state == RS.STARTUP and bmin >= _RANGE_START_MIN:
            gap_res = risk.check_gap()
            if not gap_res.passed:
                transition(RS.HALTED, f"Gap FAIL: {gap_res.reason}")
                result.outcome = "HALTED"
                break
            strategy.reset()
            # reset clears VWAP accumulators — re-add this bar immediately
            strategy.update_vwap(b)
            transition(RS.RANGE_BUILDING, "Pre-open filters passed")

        # ── RANGE_BUILDING ─────────────────────────────────────────────────────
        if state == RS.RANGE_BUILDING:
            if bmin < _RANGE_END_MIN:
                strategy.add_range_bar(b)
            else:
                rng = strategy.lock_range(b.timestamp)
                if rng is None:
                    transition(RS.HALTED, "No range bars collected")
                    result.outcome = "HALTED"
                    break
                w_res = risk.check_range_width(rng.width)
                if not w_res.passed:
                    transition(RS.HALTED, f"Width FAIL: {w_res.reason}")
                    result.outcome = "HALTED"
                    break
                transition(RS.WAITING_ENTRY,
                           f"Range locked H={rng.high} L={rng.low} W={rng.width:.2f}pts")
                # Fall through: evaluate this bar as a signal candidate too
                sig = strategy.evaluate_signal(b)
                if sig:
                    direction = sig
                    fill_next_bar = True
                    note = f"  Signal={sig} at {t.strftime('%H:%M')} close={b.close:.2f}"
                    result.notes.append(note)
                    if verbose:
                        print(note)
                continue   # don't double-process as WAITING_ENTRY below

        # ── WAITING_ENTRY ──────────────────────────────────────────────────────
        if state == RS.WAITING_ENTRY:
            if fill_next_bar:
                entry_price  = b.open
                stop_price, target_price = strategy.compute_levels(direction, entry_price)
                strategy.set_entry(direction, entry_price)
                entry_bar_skip = True
                fill_next_bar  = False
                new_state = RS.IN_TRADE_LONG if direction == "LONG" else RS.IN_TRADE_SHORT
                transition(new_state,
                           f"Entry FILLED @ {entry_price:.2f} | "
                           f"stop={stop_price:.2f} target={target_price:.2f} | "
                           f"VWAP={strategy.session_vwap:.2f}")
                # Fall through to IN_TRADE block below (but entry_bar_skip=True will skip exits)

            elif bmin >= _ENTRY_CUTOFF:
                transition(RS.DAILY_DONE, f"Entry cutoff {config.ENTRY_CUTOFF_TIME}")
                result.outcome = "NO_TRADE"
                continue

            else:
                sig = strategy.evaluate_signal(b)
                if sig:
                    direction = sig
                    fill_next_bar = True
                    note = f"  Signal={sig} at {t.strftime('%H:%M')} close={b.close:.2f}"
                    result.notes.append(note)
                    if verbose:
                        print(note)
                continue   # don't fall into IN_TRADE on non-fill bars

        # ── IN_TRADE ───────────────────────────────────────────────────────────
        if state in (RS.IN_TRADE_LONG, RS.IN_TRADE_SHORT) and entry_price is not None:
            if entry_bar_skip:
                # Entry bar: fill just happened; in real bot _handle_in_trade
                # is NOT called for the fill bar (it's a callback).
                # Only run broker stop/target check (standing orders go live).
                entry_bar_skip = False
                stop_hit   = (b.low  <= stop_price  if direction == "LONG" else b.high >= stop_price)
                target_hit = (b.high >= target_price if direction == "LONG" else b.low  <= target_price)
                if stop_hit and not target_hit:
                    record_exit(stop_price, "STOP"); break
                elif target_hit and not stop_hit:
                    record_exit(target_price, "TARGET"); break
                elif stop_hit and target_hit:
                    record_exit(stop_price, "STOP"); break   # conservative
                # else: no hit on entry bar, continue
            else:
                # Normal in-trade bar
                strategy.increment_trade_bar()

                stop_hit   = (b.low  <= stop_price  if direction == "LONG" else b.high >= stop_price)
                target_hit = (b.high >= target_price if direction == "LONG" else b.low  <= target_price)
                hard_close = (bmin >= _HARD_CLOSE_MIN)

                vwap_stop  = strategy.check_vwap_stop(b)
                warn_exit  = strategy.check_warning_signal(b)

                # Broker stop/target fire if price reaches them (regardless of VWAP)
                if stop_hit and not target_hit:
                    record_exit(stop_price, "STOP"); break
                elif target_hit and not stop_hit:
                    record_exit(target_price, "TARGET"); break
                elif stop_hit and target_hit:
                    record_exit(stop_price, "STOP"); break

                # Dynamic software exits (only when stop/target not hit)
                if vwap_stop:
                    record_exit(b.close, "VWAP_STOP"); break
                if warn_exit:
                    record_exit(b.close, "WARNING_EXIT"); break
                if hard_close:
                    record_exit(b.close, "HARD_CLOSE"); break

        # ── EOD ────────────────────────────────────────────────────────────────
        if state == RS.DAILY_DONE and bmin >= 960:
            transition(RS.CLOSED, "Session ended")
            break

    result.passed = (result.outcome == result.expected)
    return result


# ── Runner ─────────────────────────────────────────────────────────────────────

def main() -> None:
    scenarios = _make_scenarios()

    args = sys.argv[1:]
    if "--list" in args:
        print("\nAvailable scenarios:")
        for name, sc in scenarios.items():
            print(f"  {name:20s} — {sc.description}")
        return

    if args and args[0] != "--list":
        requested = args[0]
        if requested not in scenarios:
            print(f"Unknown scenario: {requested!r}")
            print(f"Available: {', '.join(scenarios)}")
            sys.exit(1)
        run_names = [requested]
    else:
        run_names = list(scenarios.keys())

    print("\n" + "=" * 72)
    print("  MES ORB Bot — Replay Test Harness  (new design)")
    print(f"  range={config.RANGE_START_TIME}–{config.RANGE_END_TIME}  "
          f"safetyTgt×{config.SAFETY_TARGET_MULT}  "
          f"hardClose={config.HARD_CLOSE_TIME}  "
          f"contracts={config.CONTRACTS}  "
          f"VWAP={'ON' if config.VWAP_TRAILING_STOP else 'OFF'}  "
          f"WarnExit={'ON' if config.WARNING_SIGNAL_EXIT else 'OFF'}")
    print("=" * 72)

    results = []
    for name in run_names:
        sc = scenarios[name]
        print(f"\n{'─' * 72}")
        print(f"  Scenario : {name}")
        print(f"  Desc     : {sc.description}")
        print(f"  Expected : {sc.expected_outcome}")
        print(f"{'─' * 72}")

        r = run_scenario(sc, verbose=True)
        results.append(r)

        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"\n  Outcome  : {r.outcome}")
        print(f"  Result   : {status}")
        if r.pnl_pts is not None:
            print(f"  P&L      : {r.pnl_pts:+.4f}pts  "
                  f"gross=${r.gross_usd:+.2f}  "
                  f"net=${r.net_usd:+.2f}")
        if r.close_reason:
            print(f"  Closed   : {r.close_reason}")

    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    print("\n" + "=" * 72)
    print(f"  RESULTS: {passed}/{total} scenarios passed")
    if passed < total:
        print("\n  FAILED scenarios:")
        for r in results:
            if not r.passed:
                print(f"    {r.scenario}: expected={r.expected} got={r.outcome} "
                      f"closedBy={r.close_reason or 'N/A'}")
    print("=" * 72 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
