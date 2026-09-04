"""Form 4 document parser — the actual insider transactions, not just filings.

WHAT WAS WRONG
--------------
`edgar.form4_sweep` walks EDGAR's daily index and stores filing METADATA:
form, company, cik, filed, path, ticker. The `path` points straight at the
document. Nothing ever opened it.

So `detect_clusters` had no transactions to work with and fell back to
**yfinance** for direction — text-matching "purchase" in a scraped free-text
field with a documented 2-14 day lag. A signal built on SEC primary data was
being decided by a scraped secondary source.

WHY IT MATTERS MORE THAN THE PLUMBING SUGGESTS
---------------------------------------------
  Cohen, Malloy & Pomorski (2012), "Decoding Inside Information" (JF) — the
  central result is that insider trades must be separated into ROUTINE (the
  same calendar month year after year, largely scheduled) and OPPORTUNISTIC.
  Routine trades carry essentially no predictive content; the information is
  concentrated almost entirely in the opportunistic subset. Pooling them
  dilutes the signal toward zero.

  Lakonishok & Lee (2001) — insider BUYING is the informative side; selling is
  dominated by diversification and liquidity motives and predicts far less.

You cannot do either split without the transaction code, and the transaction
code only exists in the document.

WHAT THIS EXTRACTS
------------------
Per non-derivative transaction: code, date, shares, price, acquired/disposed,
shares held after, plus the reporting owner's name and role (director /
officer / 10% owner / officer title).

Transaction codes that matter:
    P  open-market or private PURCHASE   <- the informative one
    S  open-market or private SALE
    A  grant/award                        } compensation, not a view
    M  option exercise                    }
    F  shares withheld for tax            }
    G  gift
Only P (and S for the short side) is treated as a directional trade.

ROUTINE vs OPPORTUNISTIC
------------------------
The Cohen-Malloy-Pomorski classification needs several years of history per
insider to establish a calendar pattern; this store starts accruing now, so
`classify_routine` is implemented but reports `insufficient_history` until it
has ROUTINE_MIN_YEARS of filings. In the meantime `plan_10b5_1` is available
immediately — footnotes disclose Rule 10b5-1 plans, and a plan sale is the
canonical scheduled trade.

CLI: python -m advisor.research.form4 [--backfill N] [--clusters]
Writes advisor/data/research/positioning/form4_parsed/dt=YYYY-MM-DD.parquet
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET_TZ = ZoneInfo("America/New_York")
FORM4_DIR = RESEARCH_DIR / "positioning" / "form4"
PARSED_DIR = RESEARCH_DIR / "positioning" / "form4_parsed"
CLUSTERS = RESEARCH_DIR / "positioning" / "insider_clusters.json"
ARCHIVE = "https://www.sec.gov/Archives/"

# informative codes; everything else is compensation or administrative
BUY_CODES = {"P"}
SELL_CODES = {"S"}

ROUTINE_MIN_YEARS = 3        # CMP need a multi-year calendar pattern
CLUSTER_WINDOW_D = 30
CLUSTER_MIN_INSIDERS = 2
CLUSTER_MIN_USD = 50_000

# Measured 2026-09-03 over 833 parsed transactions in a 30-day window:
#   S 268 · A 158 · M 118 · F 108 · G 24 · J 25 · P 18
# Open-market purchases are 2.2% of Form 4 activity, and across the whole
# universe in a month ZERO names had two distinct insiders buying. A
# clusters-only rule therefore never fires. But the purchases that do occur
# are substantial and exactly the kind Lakonishok & Lee (2001) identify as
# informative — an Executive Chairman at $3.74M, a CEO at $903k, a COO at
# $432k. So there are two tiers, recorded separately so attribution can tell
# them apart rather than pooling a strong signal with a weaker one.
CONVICTION_USD = 100_000     # single officer/director buy worth surfacing
TX_RECENCY_D = 45            # by TRANSACTION date, not filing date

_XML_RE = re.compile(rb"<(?:\w+:)?ownershipDocument[\s>].*?</(?:\w+:)?ownershipDocument>",
                     re.S | re.I)
_PLAN_RE = re.compile(r"10b5-?1", re.I)


def _txt(node, *path) -> str | None:
    """Nested <tag><value>X</value></tag> lookup, tolerant of missing nodes."""
    cur = node
    for p in path:
        if cur is None:
            return None
        cur = cur.find(p)
    if cur is None:
        return None
    v = cur.find("value")
    s = (v.text if v is not None else cur.text) or ""
    return s.strip() or None


def _num(node, *path):
    s = _txt(node, *path)
    if s is None:
        return None
    try:
        return float(s.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def parse_document(raw: bytes) -> list[dict]:
    """Parse one Form 4 submission into transaction rows. Never raises."""
    import xml.etree.ElementTree as ElementTree

    m = _XML_RE.search(raw)
    if not m:
        return []
    try:
        root = ElementTree.fromstring(m.group(0))
    except ElementTree.ParseError:
        return []

    issuer_sym = _txt(root, "issuer", "issuerTradingSymbol")
    issuer_cik = _txt(root, "issuer", "issuerCik")
    period = _txt(root, "periodOfReport")

    owner = root.find("reportingOwner")
    name = _txt(owner, "reportingOwnerId", "rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    def _flag(tag):
        s = _txt(rel, tag) if rel is not None else None
        return s in ("1", "true", "TRUE", "True")
    is_dir, is_off = _flag("isDirector"), _flag("isOfficer")
    is_ten = _flag("isTenPercentOwner")
    title = _txt(rel, "officerTitle") if rel is not None else None

    # Rule 10b5-1 plans are disclosed in footnotes (and, on newer forms, in a
    # dedicated element). A plan trade is the canonical SCHEDULED trade.
    body = m.group(0).decode("utf-8", "ignore")
    plan = bool(_PLAN_RE.search(body))

    rows = []
    for table, derivative in (("nonDerivativeTable", False),
                              ("derivativeTable", True)):
        tbl = root.find(table)
        if tbl is None:
            continue
        tag = "nonDerivativeTransaction" if not derivative else "derivativeTransaction"
        for tx in tbl.findall(tag):
            code = _txt(tx, "transactionCoding", "transactionCode")
            shares = _num(tx, "transactionAmounts", "transactionShares")
            price = _num(tx, "transactionAmounts", "transactionPricePerShare")
            ad = _txt(tx, "transactionAmounts", "transactionAcquiredDisposedCode")
            rows.append({
                "ticker": (issuer_sym or "").upper() or None,
                "issuer_cik": int(issuer_cik) if (issuer_cik or "").isdigit() else None,
                "period": period,
                "insider": name,
                "is_director": is_dir, "is_officer": is_off,
                "is_ten_pct": is_ten, "officer_title": title,
                "derivative": derivative,
                "code": code,
                "tx_date": _txt(tx, "transactionDate"),
                "shares": shares, "price": price,
                "acquired_disposed": ad,
                "value_usd": (shares * price) if (shares and price) else None,
                "shares_after": _num(tx, "postTransactionAmounts",
                                     "sharesOwnedFollowingTransaction"),
                "plan_10b5_1": plan,
            })
    return rows


def enrich(days: int = 30, limit: int | None = None, verbose: bool = True) -> dict:
    """Fetch and parse the filings the sweep has indexed but never opened."""
    import pandas as pd
    from advisor.research.edgar import _get

    cutoff = (datetime.now(ET_TZ) - timedelta(days=days)).date().isoformat()
    index = []
    for p in sorted(FORM4_DIR.glob("dt=*.parquet")):
        if p.stem.split("=", 1)[1] < cutoff:
            continue
        try:
            index.append(pd.read_parquet(p))
        except Exception:
            continue
    if not index:
        return {"ok": False, "reason": "no form4 index partitions in window",
                "parsed": 0}
    idx = pd.concat(index, ignore_index=True)
    idx = idx[idx.ticker.notna()].drop_duplicates("path")

    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    already = set()
    for p in PARSED_DIR.glob("dt=*.parquet"):
        try:
            already.update(pd.read_parquet(p)["path"].unique())
        except Exception:
            continue
    todo = [r for r in idx.to_dict("records") if r["path"] not in already]
    if limit:
        todo = todo[:limit]

    rows, errors = [], 0
    for i, r in enumerate(todo):
        try:
            raw = _get(ARCHIVE + r["path"])
            for tx in parse_document(raw):
                tx["path"] = r["path"]
                tx["filed"] = r["filed"]
                # index ticker wins: the sweep already mapped CIK -> universe
                tx["ticker"] = r["ticker"] or tx["ticker"]
                rows.append(tx)
        except Exception as exc:
            errors += 1
            if verbose and errors <= 3:
                print(f"[form4] {r['path']}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
        if verbose and (i + 1) % 100 == 0:
            print(f"[form4] {i + 1}/{len(todo)} documents", flush=True)

    written = 0
    if rows:
        df = pd.DataFrame(rows)
        for day, g in df.groupby("filed"):
            path = PARSED_DIR / f"dt={day}.parquet"
            if path.exists():
                try:
                    g = pd.concat([pd.read_parquet(path), g], ignore_index=True)
                    g = g.drop_duplicates(["path", "insider", "code", "tx_date",
                                           "shares", "price"], keep="last")
                except Exception:
                    pass
            tmp = PARSED_DIR / f"dt={day}.{os.getpid()}.tmp.parquet"
            g.to_parquet(tmp, index=False)
            os.replace(tmp, path)
            written += 1
    res = {"ok": True, "documents": len(todo), "transactions": len(rows),
           "errors": errors, "partitions_written": written,
           "as_of": datetime.now(ET_TZ).isoformat()}
    if verbose:
        print(f"[form4] parsed {len(todo)} docs -> {len(rows)} transactions "
              f"({errors} errors)")
    return res


def classify_routine(df) -> dict:
    """Cohen-Malloy-Pomorski routine/opportunistic split.

    An insider is ROUTINE if they have traded in the same calendar month in
    each of the last ROUTINE_MIN_YEARS years. Reports insufficient_history
    rather than guessing — a classifier fitted on weeks of data would label
    everyone opportunistic and quietly re-introduce the dilution it exists to
    remove.
    """
    import pandas as pd

    if df is None or not len(df):
        return {"usable": False, "reason": "no parsed transactions"}
    dates = pd.to_datetime(df["tx_date"], errors="coerce").dropna()
    if dates.empty:
        return {"usable": False, "reason": "no usable transaction dates"}
    span_years = (dates.max() - dates.min()).days / 365.25
    if span_years < ROUTINE_MIN_YEARS:
        return {"usable": False, "reason": "insufficient_history",
                "span_years": round(span_years, 2),
                "needs_years": ROUTINE_MIN_YEARS,
                "note": ("store began accruing 2026-09; the 10b5-1 plan flag "
                         "is the available proxy until then")}
    routine = set()
    tmp = df.assign(_d=pd.to_datetime(df["tx_date"], errors="coerce"))
    tmp = tmp.dropna(subset=["_d"])
    for insider, g in tmp.groupby("insider"):
        months = g["_d"].dt.month
        years = g["_d"].dt.year
        for m in months.unique():
            if years[months == m].nunique() >= ROUTINE_MIN_YEARS:
                routine.add(insider)
                break
    return {"usable": True, "span_years": round(span_years, 2),
            "n_routine": len(routine), "routine": sorted(routine)}


def detect_clusters(window_days: int = CLUSTER_WINDOW_D,
                    min_insiders: int = CLUSTER_MIN_INSIDERS) -> dict:
    """Insider buy clusters from PARSED transactions. No yfinance.

    A cluster is >=min_insiders DISTINCT insiders making open-market purchases
    (code P) in the same name inside the window. Sales are excluded entirely:
    Lakonishok & Lee (2001) — the buy side is the informative one, selling is
    dominated by diversification and liquidity motives.
    """
    import pandas as pd

    cutoff = (datetime.now(ET_TZ) - timedelta(days=window_days)).date().isoformat()
    frames = []
    for p in sorted(PARSED_DIR.glob("dt=*.parquet")):
        if p.stem.split("=", 1)[1] < cutoff:
            continue
        try:
            frames.append(pd.read_parquet(p))
        except Exception:
            continue
    if not frames:
        return {"as_of": datetime.now(ET_TZ).isoformat(), "clusters": [],
                "source": "form4_parsed",
                "reason": "no parsed partitions in window"}
    df = pd.concat(frames, ignore_index=True)
    # An empty partition concats to a column-less frame, and `df.code` then
    # raises AttributeError rather than returning nothing.
    needed = {"code", "derivative", "ticker", "value_usd", "tx_date"}
    if not len(df) or not needed <= set(df.columns):
        return {"as_of": datetime.now(ET_TZ).isoformat(), "clusters": [],
                "source": "form4_parsed",
                "reason": "no usable rows in window"}
    buys = df[(df.code.isin(BUY_CODES)) & (~df.derivative.fillna(False))
              & (df.ticker.notna()) & (df.value_usd.notna())]
    # Filter on the TRANSACTION date, not the filing date. Late and amended
    # filings carry genuinely old trades — this window caught SYK purchases
    # from 2019-2022 arriving in a 2026 partition; they are not a signal now.
    tx_cutoff = (datetime.now(ET_TZ) - timedelta(days=TX_RECENCY_D)).date().isoformat()
    stale = int((pd.to_datetime(buys["tx_date"], errors="coerce")
                 < pd.Timestamp(tx_cutoff)).sum()) if len(buys) else 0
    buys = buys[pd.to_datetime(buys["tx_date"], errors="coerce")
                >= pd.Timestamp(tx_cutoff)]
    routine = classify_routine(df)

    clusters = []
    for ticker, g in buys.groupby("ticker"):
        insiders = g["insider"].dropna().unique()
        total = float(g["value_usd"].sum())
        insider_roles = g["is_officer"].fillna(False) | g["is_director"].fillna(False)
        is_cluster = len(insiders) >= min_insiders and total >= CLUSTER_MIN_USD
        is_conviction = (total >= CONVICTION_USD and bool(insider_roles.any()))
        if not (is_cluster or is_conviction):
            continue
        opportunistic = g
        if routine.get("usable"):
            opportunistic = g[~g["insider"].isin(set(routine["routine"]))]
        clusters.append({
            "ticker": ticker,
            # the stronger literature signal is the multi-insider cluster;
            # a single large officer buy is real but weaker, so say which
            "tier": "cluster" if is_cluster else "conviction_single",
            "n_buys": int(len(g)),
            "n_insiders": int(len(insiders)),
            "net_value_usd": round(total, 2),
            "opportunistic_value_usd": round(float(
                opportunistic["value_usd"].sum()), 2),
            "n_officer_buys": int(g["is_officer"].fillna(False).sum()),
            "n_director_buys": int(g["is_director"].fillna(False).sum()),
            "plan_10b5_1_share": round(float(
                g["plan_10b5_1"].fillna(False).mean()), 3),
            "top_title": next((str(x) for x in g.sort_values(
                "value_usd", ascending=False)["officer_title"] if x), None),
            "buyers_seen": sorted(str(x) for x in insiders)[:5],
        })
    # clusters outrank single buys at equal size
    clusters.sort(key=lambda c: (c["tier"] != "cluster",
                                 -c["opportunistic_value_usd"]))
    payload = {"as_of": datetime.now(ET_TZ).isoformat(),
               "window_days": window_days,
               "min_insiders": min_insiders,
               "conviction_usd": CONVICTION_USD,
               "tx_recency_days": TX_RECENCY_D,
               "source": "form4_parsed (SEC primary, transaction code P only)",
               "routine_classification": routine,
               "n_parsed_transactions": int(len(df)),
               "n_open_market_buys": int(len(buys)),
               "n_stale_tx_excluded": stale,
               "clusters": clusters}
    CLUSTERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLUSTERS.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, CLUSTERS)
    return payload


def main() -> int:
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 30
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        print(json.dumps(enrich(days=n, limit=lim), indent=2))
    if "--clusters" in sys.argv or "--backfill" not in sys.argv:
        res = detect_clusters()
        print(f"clusters: {len(res['clusters'])} from "
              f"{res.get('n_parsed_transactions', 0)} parsed transactions")
        for c in res["clusters"][:12]:
            print(f"  {c['ticker']:6} {c['n_insiders']} insiders  "
                  f"${c['net_value_usd']:,.0f}  "
                  f"officers={c['n_officer_buys']} "
                  f"10b5-1={c['plan_10b5_1_share']:.0%}")
        print("routine:", res.get("routine_classification", {}).get("reason")
              or f"{res['routine_classification'].get('n_routine')} routine insiders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
