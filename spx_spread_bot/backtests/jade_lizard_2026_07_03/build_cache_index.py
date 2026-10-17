"""Build an offline index of the local Databento raw cache (no network).

Scans backtests/ten_year/raw_dbn_cache/*.dbn.zst, reads DBN metadata only
(dataset, schema, stype_in, start, end, symbols) and writes cache_index.parquet
plus a symbol->files inverted index (symbol_index.json.zst not needed; parquet ok).

Run: /opt/anaconda3/bin/python3 backtests/jade_lizard_2026_07_03/build_cache_index.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import databento as db
import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "ten_year" / "raw_dbn_cache"
OUT = HERE / "cache_index.parquet"


def main() -> None:
    files = sorted(CACHE.glob("*.dbn.zst"))
    rows = []
    bad = []
    t0 = time.time()
    for i, f in enumerate(files):
        try:
            m = db.DBNStore.from_file(f).metadata
            rows.append(
                dict(
                    path=f.name,
                    dataset=m.dataset,
                    schema=str(m.schema),
                    stype_in=str(m.stype_in) if m.stype_in is not None else "",
                    start_ns=int(m.start),
                    end_ns=int(m.end) if m.end is not None else 0,
                    symbols=json.dumps(list(m.symbols)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            bad.append((f.name, str(exc)))
        if i % 2000 == 0:
            print(f"{i}/{len(files)} {time.time()-t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT)
    print(f"indexed {len(df)} files, {len(bad)} unreadable, in {time.time()-t0:.0f}s")
    if bad:
        (HERE / "cache_index_bad.json").write_text(json.dumps(bad, indent=1))
    print(df.groupby(["schema"]).size())


if __name__ == "__main__":
    main()
