"""Market context snapshot for advisor sessions — full-market discovery scan.

Indices, volatility complex, all 11 sector ETFs, size/factor proxies,
commodities, rates, dollar, crypto. This is the *discovery* input: the brief
ranks what's actually moving instead of starting from any fixed watchlist
(no-legacy-priors rule in advisor/IPS.md).

Source: yfinance daily closes (EOD/delayed — NOT a live quote source).
Label it as such in any user-facing output.

CLI:
    python -m advisor.market_context                # human table
    python -m advisor.market_context --json PATH    # also write JSON
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

UNIVERSE: dict[str, str] = {
    # Indices
    "^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^RUT": "Russell 2000", "^DJI": "Dow",
    # Volatility
    "^VIX": "VIX", "^VIX3M": "VIX 3M",
    # Sector ETFs (all 11)
    "XLK": "Tech", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare",
    "XLY": "Cons Disc", "XLP": "Staples", "XLI": "Industrials", "XLU": "Utilities",
    "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Comm Svcs",
    # Size / factor
    "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "MTUM": "Momentum", "IVE": "Value",
    # Commodities / rates / dollar / crypto
    "GLD": "Gold", "SLV": "Silver", "USO": "Oil", "UNG": "NatGas",
    "TLT": "20y+ Bonds", "IEF": "7-10y Bonds", "UUP": "Dollar",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
}


def _pct(a: float, b: float) -> float | None:
    try:
        return round((a / b - 1.0) * 100, 2) if b else None
    except Exception:
        return None


def build_context() -> dict:
    import yfinance as yf

    out: dict = {
        "as_of": datetime.now(ET).isoformat(),
        "source": "yfinance daily closes (EOD/delayed)",
        "rows": [],
    }
    data = yf.download(
        list(UNIVERSE), period="1y", interval="1d",
        auto_adjust=True, progress=False, group_by="ticker", threads=True,
    )
    for tkr, name in UNIVERSE.items():
        try:
            closes = data[tkr]["Close"].dropna()
            if len(closes) < 30:
                continue
            last = float(closes.iloc[-1])
            row = {
                "ticker": tkr, "name": name, "last": round(last, 2),
                "chg_1d_pct": _pct(last, float(closes.iloc[-2])),
                "chg_5d_pct": _pct(last, float(closes.iloc[-6])) if len(closes) > 6 else None,
                "chg_21d_pct": _pct(last, float(closes.iloc[-22])) if len(closes) > 22 else None,
                "pct_from_52w_high": _pct(last, float(closes.max())),
            }
            out["rows"].append(row)
        except Exception:
            continue

    # VIX term structure — backwardation (ratio > 1) is the stress tell
    vix = {r["ticker"]: r["last"] for r in out["rows"] if r["ticker"] in ("^VIX", "^VIX3M")}
    if "^VIX" in vix and "^VIX3M" in vix and vix["^VIX3M"]:
        out["vix_term_ratio"] = round(vix["^VIX"] / vix["^VIX3M"], 3)

    out["rows"].sort(key=lambda r: abs(r.get("chg_1d_pct") or 0), reverse=True)
    return out


def render(ctx: dict) -> str:
    lines = [
        f"MARKET CONTEXT  {ctx['as_of']}",
        f"source: {ctx['source']}",
        "=" * 72,
        f"{'name':<14}{'last':>10}{'1d%':>8}{'5d%':>8}{'21d%':>8}{'vs52wH':>9}",
    ]
    for r in ctx["rows"]:
        def f(v): return f"{v:+.2f}" if isinstance(v, (int, float)) else "  —"
        lines.append(f"{r['name']:<14}{r['last']:>10}{f(r['chg_1d_pct']):>8}"
                     f"{f(r['chg_5d_pct']):>8}{f(r['chg_21d_pct']):>8}"
                     f"{f(r['pct_from_52w_high']):>9}")
    if "vix_term_ratio" in ctx:
        ratio = ctx["vix_term_ratio"]
        lines.append(f"\nVIX/VIX3M term ratio: {ratio}  "
                     + ("⚠ BACKWARDATION (stress)" if ratio > 1.0 else "(contango — normal)"))
    return "\n".join(lines)


def main() -> int:
    ctx = build_context()
    print(render(ctx))
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ctx, indent=2))
        print(f"\n[market_context] json → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
