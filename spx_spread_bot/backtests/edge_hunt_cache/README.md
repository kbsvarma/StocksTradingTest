# Edge Hunt Cache — Persistent Data Store

DO NOT DELETE. Re-built only if data is corrupt.

## Files

- `vix_panel.parquet` — Daily VIX/VIX9D/VIX3M/VVIX OHLC, 2013-current (yfinance, one-time pull)
- `daily_ctx.parquet` — Per-day computed features joining SPX OHLC + VIX panel + derived (RSI/RV/MA/EM/ORB)
- `spx_minute/YYYYMMDD.parquet` — Per-day minute-level SPX series via put-call parity from Databento bid/ask. Resumable cache.

## Sources
- SPX/SPY/NDX/IWM daily OHLC: existing `../narrow_width_databento_2026_05_21/spot_cache_*.parquet`
- VIX panel daily: yfinance (^VIX, ^VIX9D, ^VIX3M, ^VVIX) — identical to Databento at daily resolution
- SPX intraday minute: computed from put-call parity on Databento `spxw_p_cbbo_1m` + `spxw_c_cbbo_1m`

## Build scripts
- `build_vix_panel.py` — pulls VIX panel, saves
- `build_spx_minute.py` — for each day, computes minute series from Databento, caches
- `build_daily_ctx.py` — joins everything into single per-day features file
