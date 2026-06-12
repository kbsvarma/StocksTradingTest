"""Research universe — full liquid US cross-section.

Composition: S&P 1500 composite (500 large + 400 mid + 600 small, from
Wikipedia constituent tables, weekly refresh) + curated EXTRAS (liquid
non-index names: recent IPOs, large ADRs, crypto-proxies) + the macro/ETF
complex. ~1,550 single names — covers ~90%+ of US market cap with a
maintained sector mapping. (Honest scope note: true Russell-3000 membership
isn't freely available; the S&P 1500 + extras is the best free approximation
and anything liquid enough for a $25k book is in it.)

Cache: advisor/data/research/universe.json (refreshed if older than 7 days).

CLI: python -m advisor.research.universe [--refresh]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = Path(os.environ.get("ADVISOR_DATA_DIR",
                                   str(REPO_ROOT / "advisor" / "data"))) / "research"
CACHE = RESEARCH_DIR / "universe.json"
MAX_AGE_S = 7 * 86400

WIKI = {
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies": ("Symbol", "GICS Sector"),
    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies": ("Symbol", "GICS Sector"),
    "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies": ("Symbol", "GICS Sector"),
}

# Liquid names outside the S&P 1500 worth covering (user/sessions may extend)
EXTRAS = {
    "SPCX": "Industrials",          # SpaceX (IPO 2026-06)
    "COIN": "Financials", "MSTR": "Information Technology",
    "TSM": "Information Technology", "ASML": "Information Technology",
    "BABA": "Consumer Discretionary", "SHOP": "Information Technology",
    "SE": "Communication Services", "MELI": "Consumer Discretionary",
    "NVO": "Health Care", "SAP": "Information Technology",
}

SECTOR_ETF = {
    "Information Technology": "XLK", "Financials": "XLF", "Energy": "XLE",
    "Health Care": "XLV", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Industrials": "XLI", "Utilities": "XLU",
    "Materials": "XLB", "Real Estate": "XLRE", "Communication Services": "XLC",
}

MACRO_ETFS = ["SPY", "QQQ", "IWM", "RSP", "SMH", "GLD", "SLV", "USO", "UNG",
              "TLT", "IEF", "HYG", "UUP", "EEM", "EFA", "FXI",
              "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB",
              "XLRE", "XLC", "BTC-USD", "ETH-USD", "^VIX", "^VIX3M", "^TNX"]


def _fetch_wiki() -> dict[str, str]:
    import urllib.request

    import pandas as pd
    out: dict[str, str] = {}
    for url, (sym_col, sec_col) in WIKI.items():
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (advisor-research; personal use)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        from io import StringIO
        tables = pd.read_html(StringIO(html))
        for t in tables:
            if sym_col in t.columns and sec_col in t.columns:
                for _, r in t.iterrows():
                    sym = str(r[sym_col]).strip().replace(".", "-")  # BRK.B → BRK-B
                    sec = str(r[sec_col]).strip()
                    if sym and sym != "nan":
                        out[sym] = sec
                break
    return out


def load(refresh: bool = False) -> dict:
    """Returns {"stocks": {ticker: sector}, "etfs": [...], "as_of": ts}."""
    if not refresh and CACHE.exists():
        try:
            u = json.loads(CACHE.read_text())
            if time.time() - u.get("fetched_unix", 0) < MAX_AGE_S:
                return u
        except Exception:
            pass
    stocks = {}
    try:
        stocks = _fetch_wiki()
    except Exception as exc:
        print(f"[universe] wiki fetch failed ({exc})", file=sys.stderr)
        if CACHE.exists():   # stale cache beats nothing
            return json.loads(CACHE.read_text())
        raise
    stocks.update(EXTRAS)
    u = {"stocks": stocks, "etfs": MACRO_ETFS, "sector_etf": SECTOR_ETF,
         "n_stocks": len(stocks), "fetched_unix": time.time()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(u))
    os.replace(tmp, CACHE)
    return u


if __name__ == "__main__":
    u = load(refresh="--refresh" in sys.argv)
    print(f"universe: {u['n_stocks']} stocks + {len(u['etfs'])} ETFs/macro  "
          f"(fetched {time.strftime('%F %T', time.localtime(u['fetched_unix']))})")
    from collections import Counter
    for sec, n in Counter(u["stocks"].values()).most_common():
        print(f"  {sec:<28} {n}")
