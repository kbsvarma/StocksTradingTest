# Frozen SOL perpetual forward paper experiment

Public GET endpoints only; no credentials, account client, order submission, transfer or live mode exists. Current production prediction bot is unaffected.

Strategy: prior24h midpoint channel breakout, opposite12h channel exit,72h maximum hold, twice daily-scaled hourly volatility stop, fixed1h entry/exit delay. Size whole contracts to initial stop-distance risk <=$1 and notional <=$50. These are paper test scales, not a fixed user capital constraint. Rates are an explicitly fixed scenario:12bp taker plus1bp extra cost per side. Funding is taken from public realized funding records and closed trade estimates are reconciled when later records appear.

Persistent SQLite ledger stores fresh live book observations, decisions, pending actions, open position and completed simulations. No backfilled entries. Process lock prevents duplicate runners. Pending entries more than2min late expire; exits wait for available depth and show errors/delays. HTTP dashboard binds only to new-server LAN address port8509; read-only endpoints, no trading controls.

Eleven unit tests cover no retroactive trades, replay idempotence after restart, execution delay, size/depth limits, no future candle influence, funding direction and exit behavior with missing indicators. All35historical research entry signals and stop fractions were cross-checked with the frozen implementation. Live executions now use displayed-depth VWAP plus entry spread/depth checks, so they may differ from candle-only historical fills. Stops are evaluated at hourly observations and simulated at next-hour quotes, not guaranteed stop prices. A$1sizing risk is not a hard loss cap. Fees are modeled, not charged. No real profit is claimed.

The pilot starts at zero trades and waits for new hourly signals. Raw data and paper trading continue under user systemd services; this is a paper bot, not a scheduled chat notification. It does not message the user or auto-enable real trading. Criteria should be assessed on fresh data, including costs, outages, profit concentration and drawdown; no automatic promotion after a time or profit threshold.

Services: kalshi-sol-perp-paper.service and kalshi-sol-perp-paper-ui.service. Status URL: http://192.168.3.136:8509 . Server root: /home/varma/kalshi_sol_perp_paper_2026_09_27 . Stop/start through systemctl --user with those exact service names. All files/state live under this dedicated directory.

Dashboard access from this Mac is http://127.0.0.1:8509 through an SSH local forward. Direct LAN8509 is blocked by the existing firewall, which was not changed. The paper services persist on the server; the SSH tunnel must be reestablished after this Mac or its connection restarts. A running dashboard does not indicate that a trade has occurred.
