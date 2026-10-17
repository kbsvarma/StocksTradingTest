"""IBKR free borrow / shortable data — the short-pressure lever.

Source: anonymous FTP, user 'shortstock', host ftp2.interactivebrokers.com,
file usa.txt. Pipe-delimited:
    #SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|

Signals it gives us:
  - FEERATE   = annualized cost to borrow/short (%). HIGH fee = crowded short /
                hard-to-borrow = squeeze pressure. The key positioning signal.
  - AVAILABLE = shares available to borrow (">10000000" when plentiful). LOW =
                hard to borrow.

IMPORTANT: this is a *current* snapshot (dated in the #BOF header). IBKR does
NOT archive it, so there is NO history for backtesting — we collect it forward
(daily) and use it live / in paper trading. Treat as forward-only data.
"""
from __future__ import annotations

import socket
from ftplib import FTP
from pathlib import Path

import pandas as pd

_HOSTS = ("ftp2.interactivebrokers.com", "ftp3.interactivebrokers.com")
OUT = Path(__file__).resolve().parent / "data" / "borrow"


def fetch_borrow(country: str = "usa.txt", timeout: int = 25) -> pd.DataFrame:
    """Fetch + parse the current IBKR shortable file. Returns a DataFrame with
    symbol, name, rebate, fee_rate, available (float; NaN when ">N" capped),
    avail_raw, and asof (the file's snapshot date)."""
    socket.setdefaulttimeout(timeout)
    lines: list[str] = []
    last_err = None
    for host in _HOSTS:
        try:
            f = FTP(host)
            f.login("shortstock", "")
            f.set_pasv(True)
            f.retrlines(f"RETR {country}", lines.append)
            f.quit()
            break
        except Exception as e:  # try next host
            last_err = e
            lines = []
    if not lines:
        raise RuntimeError(f"could not fetch IBKR borrow file: {last_err}")

    asof = None
    if lines[0].startswith("#BOF"):
        parts = lines[0].split("|")
        asof = parts[1] if len(parts) > 1 else None

    rows = []
    for L in lines:
        if L.startswith("#") or not L.strip():
            continue
        p = L.split("|")
        if len(p) < 8:
            continue

        def _f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        avail_raw = p[7]
        rows.append(
            dict(
                symbol=p[0],
                name=p[2],
                rebate=_f(p[5]),
                fee_rate=_f(p[6]),
                available=_f(avail_raw.replace(">", "")),
                avail_raw=avail_raw,
            )
        )
    df = pd.DataFrame(rows)
    df["asof"] = asof
    return df


def snapshot_and_save(country: str = "usa.txt") -> tuple[pd.DataFrame, Path]:
    """Fetch and persist a dated snapshot (so daily collection builds history)."""
    df = fetch_borrow(country)
    OUT.mkdir(parents=True, exist_ok=True)
    asof = (df["asof"].iloc[0] if len(df) and df["asof"].iloc[0] else "unknown").replace(".", "-")
    path = OUT / f"borrow_{asof}.parquet"
    df.to_parquet(path)
    return df, path


if __name__ == "__main__":
    df, path = snapshot_and_save()
    print(f"saved {len(df)} names  asof={df['asof'].iloc[0]}  -> {path}")
    hard = df[df.fee_rate.fillna(0) > 1].sort_values("fee_rate", ascending=False)
    print(f"\nhard-to-borrow (fee > 1%/yr): {len(hard)} names  "
          f"(these are the crowded-short candidates)")
    print(hard[["symbol", "name", "fee_rate", "avail_raw"]].head(15).to_string(index=False))
