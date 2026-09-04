"""Schedule 13D/13G — who just took a 5%+ stake, and whether they mean to act.

WHY
---
  Brav, Jiang, Partnoy & Thomas (2008), "Hedge Fund Activism, Corporate
  Governance, and Firm Performance" (JF) — 13D announcements by activist
  hedge funds are associated with substantial positive abnormal returns
  around the filing, and the effect is not fully reversed over the following
  year.

The 13D/13G distinction IS the signal, not a detail:
    13D  filed when crossing 5% WITH INTENT TO INFLUENCE control (10 days)
    13G  the PASSIVE counterpart — index funds, pure investment intent
Brav et al. is about 13D. Treating them alike would pool an activist event
with Vanguard rebalancing.

Compared with congressional trading — the other "follow someone who knows
something" idea — this has a 10-day filing deadline rather than 45, names the
exact stake rather than a dollar bucket, and has far better-replicated
evidence behind it.

FORMAT NOTES (both cost real time to find)
------------------------------------------
1. The EDGAR daily index form type is `SCHEDULE 13D`, not `SC 13D`, and it
   CONTAINS A SPACE. Splitting the row on whitespace and taking field 0
   yields "SCHEDULE" and silently finds nothing — 98 filings in one day were
   invisible that way. The index is fixed-width: form type is columns 0-11.
2. EDGAR lists each filing under BOTH the filer and the subject company, so
   accessions appear multiple times. Dedupe by path, then read the
   SEC-HEADER, which names SUBJECT COMPANY and FILED BY explicitly rather
   than leaving you to guess which CIK is which.

Since the SEC's structured-filing requirement the document carries
`<percentOfClass>` per reporting person, so the stake is read, not regexed
out of prose.

WHAT THIS DELIBERATELY DOES NOT CLAIM
-------------------------------------
Brav et al. studied ACTIVIST HEDGE FUND 13Ds. A private-equity sponsor filing
a 13D on a company it floated (Advent / Definitive Healthcare, 58.54% — a real
example from the first day sampled) is a different animal carrying no such
prediction. Separating the two needs Item 4 "Purpose of Transaction" prose,
which this does not attempt.

So the generator ships `standalone: False`: it contributes cross-family
confluence and accrues an attributed track record, but never LEADS a pick
until its own cell in `by_lead_bucket` earns it. That is the pre-registration
discipline applied to itself rather than described.

CLI: python -m advisor.research.schedule13 [--sweep N] [--signals]
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
STORE = RESEARCH_DIR / "positioning" / "schedule13"
OUT = RESEARCH_DIR / "positioning" / "schedule13_signals.json"
ARCHIVE = "https://www.sec.gov/Archives/"

FORM_PREFIX = "SCHEDULE 13"
FORM_COL = 12                # fixed-width form-type field
WINDOW_D = 45                # how long a stake stays "recent"
MIN_PCT = 5.0                # below the filing threshold is a data error
# Above this a holder is a control/sponsor position, not the activist event
# Brav et al. describe. Kept but flagged rather than dropped.
CONTROL_PCT = 25.0

_SUBJ_RE = re.compile(
    r"SUBJECT COMPANY:.*?COMPANY CONFORMED NAME:\s*(?P<name>[^\n]+?)\s*\n"
    r".*?CENTRAL INDEX KEY:\s*(?P<cik>\d+)", re.S)
_FILER_RE = re.compile(
    r"FILED BY:.*?COMPANY CONFORMED NAME:\s*(?P<name>[^\n]+?)\s*\n", re.S)
_TYPE_RE = re.compile(r"CONFORMED SUBMISSION TYPE:\s*([^\n]+)")
_PCT_RE = re.compile(r"<percentOfClass[^>]*>([^<]+)</percentOfClass>", re.I)


def sweep(day: datetime | None = None) -> list[dict]:
    """Unique SCHEDULE 13D/G accessions from one daily index."""
    from advisor.research.edgar import _get, _prev_business_day

    day = day or _prev_business_day()
    q = (day.month - 1) // 3 + 1
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/"
           f"QTR{q}/form.{day.strftime('%Y%m%d')}.idx")
    try:
        text = _get(url, timeout=60).decode("latin-1")
    except Exception:
        return []
    lines = text.splitlines()
    sep = next((i for i, l in enumerate(lines) if l.startswith("----")), None)
    if sep is None:
        return []
    seen, rows = set(), []
    for line in lines[sep + 1:]:
        # fixed width: the form type contains a space, so slice it, never split
        if not line.startswith(FORM_PREFIX):
            continue
        form = line[:FORM_COL].strip() or line.split("  ")[0].strip()
        path = line.rsplit(None, 1)[-1]
        if path in seen:
            continue                       # indexed under filer AND subject
        seen.add(path)
        rows.append({"form": form, "path": path,
                     "filed": day.date().isoformat()})
    return rows


def parse_document(raw: bytes) -> dict | None:
    """Subject, filer, form type and stake from one 13D/G submission."""
    text = raw.decode("latin-1", "ignore")
    head = text[:text.find("</SEC-HEADER>")] if "</SEC-HEADER>" in text else text[:8000]
    subj = _SUBJ_RE.search(head)
    if not subj:
        return None
    filer = _FILER_RE.search(head)
    ftype = _TYPE_RE.search(head)
    form = (ftype.group(1).strip() if ftype else "").upper()
    pcts = []
    for s in _PCT_RE.findall(text):
        try:
            v = float(s.strip().replace("%", ""))
        except ValueError:
            continue
        if 0 < v <= 100:
            pcts.append(v)
    return {
        "subject_cik": int(subj.group("cik")),
        "subject_name": subj.group("name").strip()[:80],
        "filer_name": (filer.group("name").strip()[:80] if filer else None),
        "form": form,
        # 13D = intent to influence; 13G = passive. The whole point.
        "is_13d": "13D" in form,
        "is_amendment": form.endswith("/A"),
        "pct_of_class": max(pcts) if pcts else None,
        "n_reporting_persons": len(pcts),
    }


def enrich(days: int = 10, verbose: bool = True) -> dict:
    """Sweep recent business days, fetch each filing, store parsed rows."""
    import pandas as pd
    from advisor.research.edgar import _get, cik_map
    from advisor.research.universe import load as load_universe

    STORE.mkdir(parents=True, exist_ok=True)
    already = set()
    for p in STORE.glob("dt=*.parquet"):
        try:
            already.update(pd.read_parquet(p)["path"].unique())
        except Exception:
            continue

    u = load_universe()
    cmap = cik_map()
    cik_to_ticker = {}
    for t in u["stocks"]:
        c = cmap.get(t.upper().replace("-", ""))
        if c:
            cik_to_ticker[c] = t

    day = datetime.now(ET_TZ)
    rows, fetched, errors = [], 0, 0
    for _ in range(days):
        day -= timedelta(days=1)
        if day.weekday() > 4:
            continue
        for item in sweep(day):
            if item["path"] in already:
                continue
            try:
                doc = parse_document(_get(ARCHIVE + item["path"]))
                fetched += 1
            except Exception as exc:
                errors += 1
                if verbose and errors <= 3:
                    print(f"[sched13] {item['path']}: {exc}", file=sys.stderr)
                continue
            if not doc:
                continue
            doc.update({"path": item["path"], "filed": item["filed"]})
            doc["ticker"] = cik_to_ticker.get(doc["subject_cik"])
            rows.append(doc)
        if verbose:
            print(f"[sched13] {day.date()}: {len(rows)} parsed so far", flush=True)

    written = 0
    if rows:
        df = pd.DataFrame(rows)
        for d, g in df.groupby("filed"):
            path = STORE / f"dt={d}.parquet"
            if path.exists():
                try:
                    g = pd.concat([pd.read_parquet(path), g], ignore_index=True)
                    g = g.drop_duplicates("path", keep="last")
                except Exception:
                    pass
            tmp = STORE / f"dt={d}.{os.getpid()}.tmp.parquet"
            g.to_parquet(tmp, index=False)
            os.replace(tmp, path)
            written += 1
    res = {"ok": True, "fetched": fetched, "parsed": len(rows),
           "in_universe": sum(1 for r in rows if r.get("ticker")),
           "errors": errors, "partitions_written": written,
           "as_of": datetime.now(ET_TZ).isoformat()}
    if verbose:
        print(f"[sched13] {fetched} filings -> {len(rows)} parsed, "
              f"{res['in_universe']} in universe")
    return res


def signals(window_days: int = WINDOW_D) -> dict:
    """Recent 5%+ stakes in universe names, 13D ranked above 13G."""
    import pandas as pd

    cutoff = (datetime.now(ET_TZ) - timedelta(days=window_days)).date().isoformat()
    frames = []
    for p in sorted(STORE.glob("dt=*.parquet")):
        if p.stem.split("=", 1)[1] < cutoff:
            continue
        try:
            frames.append(pd.read_parquet(p))
        except Exception:
            continue
    if not frames:
        payload = {"as_of": datetime.now(ET_TZ).isoformat(), "stakes": [],
                   "reason": "no parsed partitions in window"}
        _write(payload)
        return payload

    df = pd.concat(frames, ignore_index=True)
    # An empty partition concats to a column-less frame, and `df.ticker` then
    # raises AttributeError rather than returning nothing.
    if not len(df) or not {"ticker", "pct_of_class"} <= set(df.columns):
        payload = {"as_of": datetime.now(ET_TZ).isoformat(), "stakes": [],
                   "reason": "no usable rows in window"}
        _write(payload)
        return payload
    df = df[df.ticker.notna() & df.pct_of_class.notna()]
    df = df[df.pct_of_class >= MIN_PCT]

    stakes = []
    for ticker, g in df.groupby("ticker"):
        g = g.sort_values("filed")
        top = g.loc[g.pct_of_class.idxmax()]
        has_13d = bool(g.is_13d.any())
        new_13d = bool((g.is_13d & ~g.is_amendment).any())
        pct = float(top.pct_of_class)
        stakes.append({
            "ticker": ticker,
            "pct_of_class": round(pct, 2),
            "form": str(top.form),
            "is_13d": has_13d,
            "new_13d": new_13d,
            # a sponsor/control block is not the activist event Brav et al.
            # measured; flagged rather than dropped so attribution can split it
            "control_block": pct >= CONTROL_PCT,
            "filer": (str(top.filer_name) if top.filer_name else None),
            "subject_name": str(top.subject_name),
            "filed": str(top.filed),
            "n_filings": int(len(g)),
        })
    # a NEW 13D outranks an amended one, which outranks a passive 13G
    stakes.sort(key=lambda s: (not s["new_13d"], not s["is_13d"],
                               -s["pct_of_class"]))
    payload = {"as_of": datetime.now(ET_TZ).isoformat(),
               "window_days": window_days,
               "min_pct": MIN_PCT, "control_pct": CONTROL_PCT,
               "n_parsed": int(len(df)),
               "source": "EDGAR SCHEDULE 13D/13G, structured percentOfClass",
               "stakes": stakes}
    _write(payload)
    return payload


def _write(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, OUT)


def main() -> int:
    if "--sweep" in sys.argv:
        i = sys.argv.index("--sweep")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 10
        print(json.dumps(enrich(days=n), indent=2))
    res = signals()
    print(f"stakes: {len(res['stakes'])} in universe "
          f"from {res.get('n_parsed', 0)} parsed filings")
    for s in res["stakes"][:15]:
        kind = ("NEW 13D" if s["new_13d"] else "13D/A" if s["is_13d"] else "13G")
        flag = " [control block]" if s["control_block"] else ""
        print(f"  {s['ticker']:6} {s['pct_of_class']:6.2f}%  {kind:8} "
              f"{(s['filer'] or '?')[:34]:34}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
