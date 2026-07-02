"""deep_pull — full per-ticker research pull → dossier facts.json.

The depth tool for librarian/research sessions: one command pulls everything
free data offers on a name (identity, key numbers, quarterly trend, estimate
momentum, events, positioning incl. insider flow, analyst actions, news) and
writes the machine half of the dossier:

    advisor/data/knowledge/dossiers/<TICKER>/facts.json   (this tool owns it)
    advisor/data/knowledge/dossiers/<TICKER>/narrative.md (stub if missing —
        Claude owns it: bull/bear cases, thesis history, KILL LIST)
    advisor/data/knowledge/dossiers/<TICKER>/meta.json    (state + staleness)

Facts persist, opinions re-earn: facts.json is data with as-of dates, never
judgment. Sessions must not cite stale facts as current (meta.staleness).

CLI: python -m advisor.research.deep_pull --ticker DECK [--write-dossier] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOSSIERS = REPO_ROOT / "advisor" / "data" / "knowledge" / "dossiers"

NARRATIVE_STUB = """# {ticker} — narrative (Claude-maintained; ≤300 lines, prune when over)

## Business model
(unwritten)

## Bull case
<!-- dated bullets only; undated bullets are invalid and get pruned -->

## Bear case
<!-- dated bullets only -->

## Thesis history
<!-- dated one-liners linking journal ids -->

## Kill list
<!-- every rejected thesis: date · kill reason · revisit-if condition -->
"""


def _f(v):
    import math
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return round(v, 4) if isinstance(v, float) else v
    return None


def _quarterly(tk) -> list[dict]:
    import pandas as pd
    out = []
    try:
        inc = tk.quarterly_income_stmt
        cf = tk.quarterly_cashflow
        if inc is None or inc.empty:
            return out
        for col in list(inc.columns)[:5]:
            row = {"quarter": str(pd.Timestamp(col).date())}
            def g(df, *names):
                if df is None or getattr(df, "empty", True):
                    return None
                for n in names:
                    if n in df.index and col in df.columns:
                        return _f(float(df.loc[n, col]))
                return None
            row["revenue"] = g(inc, "Total Revenue", "Operating Revenue")
            row["net_income"] = g(inc, "Net Income",
                                  "Net Income Common Stockholders")
            row["gross_profit"] = g(inc, "Gross Profit")
            row["fcf"] = g(cf, "Free Cash Flow")
            if row["revenue"] and row["gross_profit"]:
                row["gross_margin"] = round(row["gross_profit"] / row["revenue"], 3)
            out.append(row)
    except Exception:
        pass
    return out


def _insiders(tk) -> dict | None:
    import pandas as pd
    try:
        df = tk.insider_transactions
        if df is None or df.empty:
            return None
        datecol = next((c for c in ("Start Date", "StartDate", "Date")
                        if c in df.columns), None)
        cutoff = datetime.now(ET).date() - timedelta(days=90)
        buys = sells = 0
        net_value = 0.0
        latest = []
        for _, r in df.iterrows():
            try:
                d = pd.Timestamp(r[datecol]).date() if datecol else None
            except Exception:
                d = None
            if d and d < cutoff:
                continue
            text = str(r.get("Text") or r.get("Transaction") or "").lower()
            val = r.get("Value")
            val = float(val) if isinstance(val, (int, float)) and pd.notna(val) else 0.0
            if "purchase" in text or "buy" in text:
                buys += 1
                net_value += val
            elif "sale" in text or "sell" in text:
                sells += 1
                net_value -= val
            if len(latest) < 5:
                latest.append({"date": str(d) if d else None,
                               "insider": str(r.get("Insider") or "")[:40],
                               "position": str(r.get("Position") or "")[:30],
                               "text": str(r.get("Text") or "")[:60],
                               "value": _f(val) or None})
        return {"window_days": 90, "n_buys": buys, "n_sells": sells,
                "net_value_usd": round(net_value, 0), "latest": latest}
    except Exception:
        return None


def _analyst_actions(tk) -> list[dict]:
    import pandas as pd
    out = []
    try:
        df = tk.upgrades_downgrades
        if df is None or df.empty:
            return out
        cutoff = datetime.now(ET).date() - timedelta(days=60)
        for idx, r in df.head(25).iterrows():
            try:
                d = pd.Timestamp(idx).date()
            except Exception:
                d = None
            if d and d < cutoff:
                continue
            out.append({"date": str(d) if d else None,
                        "firm": str(r.get("Firm") or "")[:30],
                        "action": str(r.get("Action") or "")[:15],
                        "to": str(r.get("ToGrade") or "")[:20],
                        "from": str(r.get("FromGrade") or "")[:20]})
            if len(out) >= 10:
                break
    except Exception:
        pass
    return out


def _news(tk) -> list[dict]:
    out = []
    try:
        for item in (tk.news or [])[:10]:
            c = item.get("content", item)   # yfinance nests under 'content' in newer versions
            title = c.get("title")
            if not title:
                continue
            url = ((c.get("canonicalUrl") or {}).get("url")
                   if isinstance(c.get("canonicalUrl"), dict)
                   else c.get("link") or c.get("url"))
            prov = (c.get("provider") or {})
            out.append({"title": title[:140],
                        "date": str(c.get("pubDate") or c.get("providerPublishTime") or "")[:19],
                        "provider": (prov.get("displayName") if isinstance(prov, dict)
                                     else str(prov))[:30],
                        "url": url})
    except Exception:
        pass
    return out


def pull(ticker: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(ticker)
    info = {}
    try:
        info = tk.info or {}
    except Exception:
        pass
    now = datetime.now(ET)
    facts: dict = {
        "ticker": ticker,
        "as_of": now.isoformat(),
        "source": "yfinance via deep_pull (delayed/EOD; short interest bi-monthly)",
        "identity": {
            "name": info.get("longName"), "sector": info.get("sector"),
            "industry": info.get("industry"),
            "business": (info.get("longBusinessSummary") or "")[:500],
        },
        "key_numbers": {k: _f(info.get(k)) for k in (
            "currentPrice", "marketCap", "beta", "trailingPE", "forwardPE",
            "pegRatio", "priceToBook", "freeCashflow", "totalDebt", "totalCash",
            "ebitda", "returnOnEquity", "grossMargins", "operatingMargins",
            "profitMargins", "earningsGrowth", "revenueGrowth",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "sharesOutstanding")},
        "quarterly": _quarterly(tk),
        "positioning": {
            **{k: _f(info.get(k)) for k in (
                "shortPercentOfFloat", "shortRatio", "sharesShort",
                "heldPercentInstitutions", "heldPercentInsiders")},
            "insiders_90d": _insiders(tk),
        },
        "analyst": {
            **{k: _f(info.get(k)) for k in (
                "targetMeanPrice", "targetLowPrice", "targetHighPrice",
                "numberOfAnalystOpinions")},
            "recommendation": info.get("recommendationKey"),
            "actions_60d": _analyst_actions(tk),
        },
        "news_recent": _news(tk),
    }
    # estimate momentum + events from the PIT snapshot store when available
    # (dated observations beat a live re-scrape), live fallback otherwise
    try:
        from advisor.research.peek import peek
        p = peek(ticker)
        facts["estimate_momentum"] = {
            "src": f"PIT snapshot {p.get('snapshot_date')}",
            **(p.get("estimate_momentum") or {})}
        facts["events"] = {"next_earnings": p.get("next_earnings"),
                           "last_surprises": p.get("last_surprises")}
        if p.get("insider_cluster"):
            facts["insider_cluster"] = p["insider_cluster"]
    except Exception:
        pass
    # EDGAR as-filed fundamentals + recent filings (fetch facts on first miss)
    try:
        from advisor.research import edgar
        if not (edgar.FACTS_DIR / f"{ticker}.parquet").exists():
            edgar.fetch_facts(ticker)
        fs = edgar.facts_summary(ticker)
        if fs:
            facts["edgar_facts"] = fs
        facts["recent_filings"] = edgar.recent_filings(ticker, limit=8)
    except Exception:
        pass
    return facts


def write_dossier(facts: dict) -> Path:
    d = DOSSIERS / facts["ticker"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "facts.json.tmp"
    tmp.write_text(json.dumps(facts, indent=2, default=str))
    tmp.replace(d / "facts.json")
    narrative = d / "narrative.md"
    if not narrative.exists():
        narrative.write_text(NARRATIVE_STUB.format(ticker=facts["ticker"]))
    meta_p = d / "meta.json"
    try:
        meta = json.loads(meta_p.read_text())
    except Exception:
        meta = {"state": "researched", "created": facts["as_of"]}
    meta["facts_refreshed"] = facts["as_of"]
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(meta_p)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--write-dossier", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    facts = pull(a.ticker.upper())
    if a.write_dossier:
        d = write_dossier(facts)
        print(f"dossier → {d.relative_to(REPO_ROOT)}/facts.json "
              f"(narrative.md {'exists' if (d / 'narrative.md').stat().st_size > 800 else 'stub'})")
    if a.json or not a.write_dossier:
        print(json.dumps(facts, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
