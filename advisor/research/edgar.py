"""SEC EDGAR layer — the only truly point-in-time free data in the stack.

Four capabilities:
  1. CIK map        — ticker ↔ CIK from SEC's company_tickers.json (weekly cache)
  2. companyfacts   — ~20 core us-gaap/dei concepts per ticker, LONG format
                      with the `filed` date on every row (the PIT clock:
                      backtests join on filed+1td, dossiers use latest)
  3. submissions    — recent filings per ticker (8-K/10-Q/Form 4 watcher feed)
  4. form4 sweep    — market-wide daily-index scan: which names had insider
                      filings yesterday (attention flag), then DIRECTION from
                      yfinance insider_transactions on just those names →
                      cluster detector (≥2 distinct insiders net buying, 30d)

Etiquette: mandatory User-Agent, ≤8 req/s (SEC limit is 10), gzip accepted.
Storage: advisor/data/research/fundamentals/edgar_facts/{TICKER}.parquet,
         advisor/data/research/positioning/form4/dt=YYYY-MM-DD.parquet,
         advisor/data/research/positioning/insider_clusters.json

CLI:
  python -m advisor.research.edgar --facts DECK          # fetch+store one name
  python -m advisor.research.edgar --form4-sweep         # yesterday's index
  python -m advisor.research.edgar --clusters            # recompute clusters
  python -m advisor.research.edgar --filings DECK        # recent submissions
"""
from __future__ import annotations

import gzip
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
UA = "Varma Kammili varmakbs001@gmail.com"
FACTS_DIR = RESEARCH_DIR / "fundamentals" / "edgar_facts"
FORM4_DIR = RESEARCH_DIR / "positioning" / "form4"
CLUSTERS = RESEARCH_DIR / "positioning" / "insider_clusters.json"
CIK_MAP = RESEARCH_DIR / "_meta" / "cik_map.json"

_last_req = 0.0
REQ_INTERVAL = 0.13          # ~8 req/s


def _get(url: str, timeout: float = 30.0) -> bytes:
    global _last_req
    wait = REQ_INTERVAL - (time.time() - _last_req)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip",
        "Host": url.split("/")[2]})
    _last_req = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


# ── CIK map ──────────────────────────────────────────────────────────────────

def cik_map(max_age_days: int = 7) -> dict[str, int]:
    try:
        cached = json.loads(CIK_MAP.read_text())
        age = (datetime.now(ET)
               - datetime.fromisoformat(cached["as_of"])).days
        if age <= max_age_days:
            return {k: int(v) for k, v in cached["map"].items()}
    except Exception:
        pass
    raw = json.loads(_get("https://www.sec.gov/files/company_tickers.json"))
    m = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
    CIK_MAP.parent.mkdir(parents=True, exist_ok=True)
    tmp = CIK_MAP.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"as_of": datetime.now(ET).isoformat(), "map": m}))
    tmp.replace(CIK_MAP)
    return m


def cik_of(ticker: str) -> int | None:
    return cik_map().get(ticker.upper().replace("-", ""))


# ── companyfacts (point-in-time fundamentals) ───────────────────────────────

# concept → list of us-gaap tag fallbacks (mapping varies by filer)
CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "rnd": ["ResearchAndDevelopmentExpense"],
}
DEI_CONCEPTS = {"shares_outstanding": ["EntityCommonStockSharesOutstanding"],
                "public_float": ["EntityPublicFloat"]}


def fetch_facts(ticker: str) -> "object | None":
    """Fetch + store companyfacts as long parquet. Returns the DataFrame."""
    import pandas as pd
    cik = cik_of(ticker)
    if cik is None:
        return None
    try:
        raw = json.loads(_get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            timeout=60))
    except Exception as exc:
        print(f"[edgar] {ticker}: companyfacts fetch failed — {exc}",
              file=sys.stderr)
        return None
    rows = []
    for taxonomy, mapping in (("us-gaap", CONCEPTS), ("dei", DEI_CONCEPTS)):
        tax = raw.get("facts", {}).get(taxonomy, {})
        for concept, tags in mapping.items():
            for tag in tags:
                node = tax.get(tag)
                if not node:
                    continue
                for unit, obs in (node.get("units") or {}).items():
                    for o in obs:
                        if o.get("val") is None or not o.get("filed"):
                            continue
                        rows.append({
                            "concept": concept, "tag": tag, "unit": unit,
                            "value": o["val"], "period_end": o.get("end"),
                            "period_start": o.get("start"),
                            "filed": o["filed"], "form": o.get("form"),
                            "fy": o.get("fy"), "fp": o.get("fp"),
                            "frame": o.get("frame")})
                break        # first tag that exists wins
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["fetched"] = datetime.now(ET).isoformat()
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(FACTS_DIR / f"{ticker.upper()}.parquet", index=False)
    return df


def facts_summary(ticker: str) -> dict | None:
    """Latest-filed value per concept + yahoo cross-check anchor (for dossiers)."""
    import pandas as pd
    p = FACTS_DIR / f"{ticker.upper()}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    out = {"src": "EDGAR companyfacts (as-filed, PIT)", "concepts": {}}
    for concept, g in df.groupby("concept"):
        # quarterly rows only where sensible (10-Q/10-K), latest filed wins
        g = g.sort_values(["filed", "period_end"])
        last = g.iloc[-1]
        out["concepts"][concept] = {
            "value": float(last["value"]),
            "period_end": last["period_end"], "filed": last["filed"],
            "form": last["form"]}
    return out


# ── submissions (per-name filing feed) ───────────────────────────────────────

def recent_filings(ticker: str, forms: tuple = ("8-K", "10-Q", "10-K", "4"),
                   limit: int = 15) -> list[dict]:
    cik = cik_of(ticker)
    if cik is None:
        return []
    try:
        raw = json.loads(_get(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json", timeout=30))
    except Exception:
        return []
    rec = raw.get("filings", {}).get("recent", {})
    out = []
    for form, date, acc, doc in zip(rec.get("form", []),
                                    rec.get("filingDate", []),
                                    rec.get("accessionNumber", []),
                                    rec.get("primaryDocument", [])):
        if form not in forms:
            continue
        acc_nodash = acc.replace("-", "")
        out.append({"form": form, "filed": date,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                           f"{acc_nodash}/{doc}"})
        if len(out) >= limit:
            break
    return out


# ── market-wide Form 4 sweep ─────────────────────────────────────────────────

def _prev_business_day(d: datetime | None = None) -> datetime:
    d = d or datetime.now(ET)
    d = d - timedelta(days=1)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def form4_sweep(day: datetime | None = None) -> dict:
    """Parse the daily index for Form 4 filings; keep universe names only."""
    import pandas as pd
    from advisor.research.universe import load as load_universe
    day = day or _prev_business_day()
    q = (day.month - 1) // 3 + 1
    ymd = day.strftime("%Y%m%d")
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/"
           f"QTR{q}/form.{ymd}.idx")
    try:
        text = _get(url, timeout=60).decode("latin-1")
    except Exception as exc:
        return {"ok": False, "error": f"{exc}", "url": url}
    lines = text.splitlines()
    sep = next((i for i, l in enumerate(lines) if l.startswith("----")), None)
    if sep is None:
        return {"ok": False, "error": "no separator row", "url": url}
    rows = []
    for l in lines[sep + 1:]:
        # data row: <form> <company...> <cik> <yyyymmdd> <edgar/path>
        # (header wraps lines, so parse from the RIGHT — last 3 fields are fixed)
        if not l.strip():
            continue
        try:
            head, cik, filed, path = l.rsplit(None, 3)
        except ValueError:
            continue
        form = head.split(None, 1)[0]
        if form not in ("4", "4/A") or not cik.isdigit():
            continue
        rows.append({"form": form,
                     "company": head[len(form):].strip()[:60],
                     "cik": int(cik),
                     "filed": day.date().isoformat(),
                     "path": path})
    u = load_universe()
    cmap = cik_map()
    cik_to_ticker = {}
    for t in u["stocks"]:
        c = cmap.get(t.upper().replace("-", ""))
        if c:
            cik_to_ticker[c] = t
    for r in rows:
        r["ticker"] = cik_to_ticker.get(r["cik"])
    df = pd.DataFrame(rows)
    FORM4_DIR.mkdir(parents=True, exist_ok=True)
    path = FORM4_DIR / f"dt={day.date().isoformat()}.parquet"
    if len(df):
        df.to_parquet(path, index=False)
    in_uni = df[df.ticker.notna()] if len(df) else df
    return {"ok": True, "rows": len(df), "in_universe": len(in_uni),
            "tickers": sorted(set(in_uni.ticker)) if len(in_uni) else [],
            "path": str(path)}


def detect_clusters(window_days: int = 30, min_insiders: int = 2) -> dict:
    """Cluster detector: universe names with Form 4 attention (daily sweeps)
    whose yfinance insider data shows >=2 DISTINCT insiders net buying in the
    window. Direction comes from yfinance (2-14d lag, see TUNING_NOTES) —
    the daily index alone has no buy/sell direction."""
    import pandas as pd
    from collections import Counter
    cutoff = (datetime.now(ET) - timedelta(days=window_days)).date().isoformat()
    filing_counts: Counter = Counter()
    for p in sorted(FORM4_DIR.glob("dt=*.parquet")):
        if p.stem.split("=", 1)[1] < cutoff:
            continue
        try:
            df = pd.read_parquet(p)
            filing_counts.update(df[df.ticker.notna()].ticker)
        except Exception:
            continue
    # pre-filter: a cluster needs multiple filings — don't direction-check
    # (yfinance call) the hundreds of names with routine single filings
    flagged = {t for t, n in filing_counts.items() if n >= 3}
    clusters = []
    if flagged:
        from advisor.research.deep_pull import _insiders
        import yfinance as yf
        for t in sorted(flagged):
            try:
                ins = _insiders(yf.Ticker(t))
            except Exception:
                continue
            if not ins:
                continue
            # distinct buyers within window
            buyers = {x["insider"] for x in ins.get("latest", [])
                      if "purchase" in (x.get("text") or "").lower()
                      or "buy" in (x.get("text") or "").lower()}
            if ins["n_buys"] >= min_insiders and ins["net_value_usd"] > 0 \
                    and len(buyers) >= min(min_insiders, len(buyers) or 1):
                clusters.append({"ticker": t, "n_buys": ins["n_buys"],
                                 "n_sells": ins["n_sells"],
                                 "net_value_usd": ins["net_value_usd"],
                                 "buyers_seen": sorted(buyers)[:5]})
    out = {"as_of": datetime.now(ET).isoformat(),
           "window_days": window_days,
           "n_names_with_filings": len(filing_counts),
           "n_direction_checked": len(flagged),
           "clusters": sorted(clusters, key=lambda x: -x["net_value_usd"]),
           "method": f"Form4 daily-index attention (>=3 filings/{window_days}d) "
                     f"+ yfinance direction; cluster = >={min_insiders} distinct "
                     f"insiders net buying (candidate source, NOT a ranked factor)"}
    CLUSTERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLUSTERS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(CLUSTERS)
    return out


# ── batch facts for the active set ───────────────────────────────────────────

def facts_sweep(tickers: list[str], max_age_days: int = 6) -> dict:
    """Refresh edgar facts for the ACTIVE set (factor-sheet names, open calls,
    dossier names) — full-universe weekly sweeps are deliberately out of scope
    (bandwidth); widen via TUNING_NOTES if ever needed."""
    import pandas as pd
    done = skipped = failed = 0
    for t in tickers:
        p = FACTS_DIR / f"{t.upper()}.parquet"
        if p.exists():
            try:
                fetched = pd.read_parquet(p, columns=["fetched"]).fetched.iloc[-1]
                if (datetime.now(ET) - datetime.fromisoformat(fetched)).days <= max_age_days:
                    skipped += 1
                    continue
            except Exception:
                pass
        if fetch_facts(t) is not None:
            done += 1
        else:
            failed += 1
    return {"fetched": done, "fresh_skipped": skipped, "failed": failed}


def main() -> int:
    args = sys.argv[1:]
    if "--facts" in args:
        t = args[args.index("--facts") + 1].upper()
        df = fetch_facts(t)
        if df is None:
            print(f"no facts for {t}")
            return 1
        print(json.dumps(facts_summary(t), indent=2))
        return 0
    if "--filings" in args:
        t = args[args.index("--filings") + 1].upper()
        print(json.dumps(recent_filings(t), indent=2))
        return 0
    if "--form4-sweep" in args:
        res = form4_sweep()
        print(json.dumps({k: v for k, v in res.items() if k != "tickers"}, indent=2))
        print("universe tickers:", ", ".join(res.get("tickers", [])[:40]))
        return 0 if res.get("ok") else 1
    if "--clusters" in args:
        res = detect_clusters()
        print(json.dumps(res, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
