# BTC / ETH follow-up research

Start with [REPORT.md](REPORT.md). `PLAN.md`, `PASS2.md` and `PASS3.md` preserve the successive predeclared experiments. Production and the September stable checkpoint are unchanged.

## Offline reproduction

From the repository root, using Python with NumPy, requests and websockets:

```bash
python3 kalshi_btc_eth_quant_2026_10_02/study.py
python3 kalshi_btc_eth_quant_2026_10_02/pass2.py
python3 kalshi_btc_eth_quant_2026_10_02/uncertainty.py
python3 kalshi_btc_eth_quant_2026_10_02/capacity.py
python3 -m unittest discover -s kalshi_btc_eth_quant_2026_10_02 -p 'test_study.py' -v
python3 kalshi_btc_eth_quant_2026_10_02/audit_capture.py
python3 kalshi_btc_eth_quant_2026_10_02/write_report.py
```

The scripts reuse version-hashed helpers and input folders from the earlier research. They write new outputs only here. Pickle files are locally generated trusted data; do not load arbitrary external pickle files. Large per-trade outputs are saved so aggregate numbers can be audited.

`fresh_data/` includes the October 2 supplemental day plus 506 hours of additional BTC benchmark minutes. Earlier raw books and CF history remain in `kalshi_restart_audit_2026_09_12/fresh_data/`. Input hashes are in `coverage.json` and `research_manifest.json`. ETH official-index coverage is not extended into the later period.

## Server collector

Server: `varma@192.168.3.136`.

- Service: `kalshi-btc-eth-research-capture.service` (user service).
- Directory: `/home/varma/kalshi_btc_eth_quant_2026_10_02`.
- Health: `forward_state/status.json`.
- Data: `forward_state/data/*.jsonl.gz` for closed segments, `.part` for the active segment.
- Bounded by500 observed quarter-hour closes,2GiB or7days, whichever occurs first. Size may bind first. It leaves all recorded data intact when it stops.
- No real-money mode exists. The collector sends fixed market-data subscriptions and public GETs only. Authenticated benchmark history also uses GET only. Existing live/paper services are independent.

The recorder restarts a connection at each market rollover. Reconnects/partial segments and sequence gaps are explicit; do not silently fill missing ticks, treat subscribed acknowledgements as proof of usable data, or assume recorded displayed depth guarantees an IOC fill.

The research target is to compare existing valid z2/z3 signals under REST book rechecks versus a fresh WebSocket trigger, with identical caps, size, signal age, duplicate control and risk. No faster live order path or new price rule has been activated. Preserve the original stable design as the return point.
