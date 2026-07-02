"""Journal → outcome rows: the deterministic substrate for calibration and
attribution. Code computes the numbers; sessions only interpret them.

Origin type is the FIRST row per id (effective() lets resolve rows overwrite
`type`, so anything downstream that filters on effective type alone
mis-classifies resolved views — always derive origin here).

Legacy rows (pre schema-v2) lack ref_px/p_win/source; every function here
degrades gracefully and labels what it could not compute.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.journal import effective, read_all

ET = ZoneInfo("America/New_York")

RESOLVED_STATUSES = ("hit_target", "stopped", "time_stop", "closed")
CONVICTION_P = {"high": 0.70, "medium": 0.55}   # legacy map when p_win absent


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def load_rows() -> tuple[dict[str, dict], dict[str, str]]:
    """(effective state per id, origin type per id)."""
    raw = read_all()
    origin: dict[str, str] = {}
    for e in raw:
        origin.setdefault(e.get("id", "?"), e.get("type", "?"))
    return effective(raw), origin


def is_test_row(e: dict) -> bool:
    return "TEST PROPOSAL" in (e.get("thesis") or "")


def resolved_views() -> list[dict]:
    """Resolved advisor calls with derived outcome fields attached."""
    eff, origin = load_rows()
    out = []
    for eid, e in eff.items():
        if origin.get(eid) != "view" or is_test_row(e):
            continue
        if e.get("status") not in RESOLVED_STATUSES:
            continue
        out.append({**e, "id": eid, **derive_outcome(e)})
    return out


def open_views() -> list[dict]:
    eff, origin = load_rows()
    return [{**e, "id": eid} for eid, e in eff.items()
            if origin.get(eid) == "view" and not is_test_row(e)
            and e.get("status") == "open"]


def rejected_ideas() -> list[dict]:
    eff, origin = load_rows()
    return [{**e, "id": eid} for eid, e in eff.items()
            if origin.get(eid) == "rejected"]


def p_win_of(e: dict) -> float | None:
    p = _num(e.get("p_win"))
    if p is not None:
        return min(0.85, max(0.15, p))
    return CONVICTION_P.get((e.get("conviction") or "").lower())


def derive_outcome(e: dict) -> dict:
    """win flag + realized R. Explicit resolve fields win; otherwise a
    level-approximation from ref/stop/target (labeled as such)."""
    status = e.get("status")
    win = {"hit_target": 1, "stopped": 0}.get(status)
    rr = _num(e.get("realized_return_pct"))
    if win is None and rr is not None:          # time_stop/closed with numbers
        win = 1 if rr > 0 else 0

    realized_r, r_basis = _num(e.get("realized_r")), "explicit"
    if realized_r is None:
        ref = _num(e.get("ref_px")) or _num(e.get("entry_px_high")) \
            or _num(e.get("entry_px_low"))
        stop, tgt = _num(e.get("stop_px")), _num(e.get("target_px"))
        exit_px = _num(e.get("exit_px")) or _num(e.get("hit_px"))
        if ref and stop and ref != stop:
            risk = abs(ref - stop)
            long_ = (e.get("direction") or "long").lower() != "short"
            if exit_px is not None:
                realized_r = ((exit_px - ref) if long_ else (ref - exit_px)) / risk
                r_basis = "hit_px" if _num(e.get("exit_px")) is None else "exit_px"
            elif status == "hit_target" and tgt:
                realized_r = ((tgt - ref) if long_ else (ref - tgt)) / risk
                r_basis = "level-approximated"
            elif status == "stopped":
                realized_r = -1.0
                r_basis = "level-approximated"
    if realized_r is not None:
        realized_r = round(realized_r, 3)
    return {"win": win, "realized_r": realized_r, "r_basis": r_basis,
            "p_win_effective": p_win_of(e)}


def forward_return(ticker: str, ref_px: float, ref_ts: str | None) -> dict | None:
    """Return since ref (vs SPY same window) — the rejected-idea counterfactual.
    Pure yfinance delayed data, labeled."""
    try:
        import yfinance as yf
        px = float(yf.Ticker(ticker).fast_info.last_price)
        spy = None
        days = None
        if ref_ts:
            start = datetime.fromisoformat(ref_ts)
            days = max(0, (datetime.now(ET) - start).days)
            try:
                h = yf.Ticker("SPY").history(start=start.strftime("%Y-%m-%d"))
                if len(h) >= 2:
                    spy = round((float(h.Close.iloc[-1]) / float(h.Close.iloc[0]) - 1) * 100, 2)
            except Exception:
                pass
        return {"px_now": round(px, 4),
                "fwd_return_pct": round((px / ref_px - 1) * 100, 2),
                "spy_return_pct": spy, "days_elapsed": days,
                "src": "yfinance delayed ~15min"}
    except Exception:
        return None
