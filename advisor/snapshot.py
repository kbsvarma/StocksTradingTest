"""Read-only portfolio snapshot for advisor sessions (Tier 0).

Gathers: bot state.json, live Webull option holdings (combo endpoint — same
read-only path as reconcile.py), and trades.csv performance stats.

THIS MODULE MUST NEVER IMPORT EXECUTION CODE. Tier 0 is read-only by
construction: there is no code path from here to an order.

Every section carries a `source` label (data-source observability rule).

CLI:
    python -m advisor.snapshot                 # human-readable to stdout
    python -m advisor.snapshot --json PATH     # also write machine-readable JSON
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_webull_cfg() -> dict:
    return yaml.safe_load((REPO_ROOT / "webull_bot" / "config.yaml").read_text())


def _resolve(p: str) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else REPO_ROOT / pp


def read_state(cfg: dict) -> dict:
    out = {"source": "state.json"}
    try:
        raw = json.loads(_resolve(cfg["state_file"]).read_text())
        out.update({
            "open_position": raw.get("open_position"),
            "pending_order": bool(raw.get("pending_order")),
            "trade_taken_today": raw.get("trade_taken_today"),
            "trading_date": raw.get("trading_date"),
            "record": {"total_trades": raw.get("total_trades"),
                       "wins": raw.get("wins"), "losses": raw.get("losses"),
                       "total_pnl_usd": raw.get("total_pnl")},
        })
    except Exception as exc:
        out["error"] = str(exc)
    return out


def read_broker(cfg: dict) -> dict:
    """Live Webull holdings via the account_v2 combo endpoint. Read-only."""
    out = {"source": "webull account_v2 (live API)", "positions": []}
    try:
        from webull_bot.client import build_trade_client
        from webull_bot.execution_v2 import PositionSnapshotter
        snap = PositionSnapshotter(build_trade_client(), cfg["account_id"])
        combos = snap.snapshot_combos() if hasattr(snap, "snapshot_combos") else []
        if not combos and hasattr(snap, "snapshot"):
            combos = list(snap.snapshot().values())
        for c in combos:
            try:
                last = abs(float(c.get("last_price", 0) or 0))
            except (ValueError, TypeError):
                last = 0.0
            if last < 0.05:
                continue  # expired/worthless remnant
            legs = c.get("legs") or []
            strikes = sorted(
                (float(l.get("option_exercise_price", 0) or 0) for l in legs),
                reverse=True,
            )
            out["positions"].append({
                "symbol": c.get("symbol"),
                "strategy": c.get("option_strategy"),
                "strikes": strikes or None,
                "qty": c.get("quantity"),
                "cost": c.get("cost_price"),
                "last": c.get("last_price"),
                "unrealized_pnl": c.get("unrealized_profit_loss"),
            })
    except Exception as exc:
        out["error"] = str(exc)
    return out


def read_performance(cfg: dict) -> dict:
    """Aggregate trades.csv — realized performance of the SPX book."""
    out = {"source": "trades.csv (realized fills)"}
    path = _resolve(cfg["trade_csv"])
    if not path.exists():
        out["error"] = f"missing: {path}"
        return out
    try:
        rows = list(csv.DictReader(path.open()))
    except Exception as exc:
        out["error"] = str(exc)
        return out

    today = date.today()
    def _pnl(r):
        try:
            return float(r.get("PnL USD", 0) or 0)
        except ValueError:
            return 0.0
    def _d(r):
        try:
            return date.fromisoformat(r.get("Date", ""))
        except ValueError:
            return None

    pnls = [_pnl(r) for r in rows]
    out.update({
        "n_trades": len(rows),
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p <= 0),
        "total_pnl_usd": round(sum(pnls), 2),
        "pnl_today_usd": round(sum(_pnl(r) for r in rows if _d(r) == today), 2),
        "pnl_7d_usd": round(sum(_pnl(r) for r in rows
                                if (_d(r) or date.min) >= today - timedelta(days=7)), 2),
        "pnl_30d_usd": round(sum(_pnl(r) for r in rows
                                 if (_d(r) or date.min) >= today - timedelta(days=30)), 2),
        "last_trades": [
            {k: r.get(k) for k in ("Date", "Short Strike", "Long Strike",
                                   "Entry Credit", "Exit Price", "PnL USD",
                                   "Exit Reason")}
            for r in rows[-5:]
        ],
    })
    return out


def build_snapshot() -> dict:
    cfg = _load_webull_cfg()
    return {
        "as_of": datetime.now(ET).isoformat(),
        "account_book": "SPX 0DTE BPS (Webull live, Mac)",
        "state": read_state(cfg),
        "broker": read_broker(cfg),
        "performance": read_performance(cfg),
    }


def render(s: dict) -> str:
    lines = [f"PORTFOLIO SNAPSHOT  {s['as_of']}", "=" * 56]
    st = s["state"]
    lines.append(f"[bot state — {st['source']}]")
    if st.get("error"):
        lines.append(f"  ⚠ {st['error']}")
    else:
        op = st.get("open_position")
        lines.append(f"  open_position: "
                     + (f"{op.get('short_strike')}/{op.get('long_strike')}P "
                        f"credit=${op.get('entry_credit')} stop=${op.get('stop_price')}"
                        if op else "(none)"))
        rec = st.get("record") or {}
        lines.append(f"  record: {rec.get('wins')}W {rec.get('losses')}L  "
                     f"total=${rec.get('total_pnl_usd')}")
    br = s["broker"]
    lines.append(f"[broker holdings — {br['source']}]")
    if br.get("error"):
        lines.append(f"  ⚠ broker query failed: {br['error']}")
    elif not br["positions"]:
        lines.append("  (no active option positions)")
    else:
        for p in br["positions"]:
            lines.append(f"  {p['symbol']} {p['strategy']} {p['strikes']} qty={p['qty']} "
                         f"cost=${p['cost']} last=${p['last']} upl=${p['unrealized_pnl']}")
    pf = s["performance"]
    lines.append(f"[performance — {pf['source']}]")
    if pf.get("error"):
        lines.append(f"  ⚠ {pf['error']}")
    else:
        lines.append(f"  {pf['n_trades']} trades  {pf['wins']}W {pf['losses']}L  "
                     f"total=${pf['total_pnl_usd']}")
        lines.append(f"  P&L today=${pf['pnl_today_usd']}  7d=${pf['pnl_7d_usd']}  "
                     f"30d=${pf['pnl_30d_usd']}")
    return "\n".join(lines)


def main() -> int:
    snap = build_snapshot()
    print(render(snap))
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=2, default=str))
        print(f"\n[snapshot] json → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
