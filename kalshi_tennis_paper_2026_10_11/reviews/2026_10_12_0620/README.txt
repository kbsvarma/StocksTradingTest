TENNIS 90-SECOND PROSPECTIVE PAPER TEST
Started 2026-10-11 13:34:37 America/New_York (17:34:37 UTC).

The user authorized paper testing the 90-second tennis candidate, verifying
feed delays and executable depth, and assessing whether its edge survives.
This isolated observer uses public GET endpoints only, loads no trading
credentials and cannot submit exchange orders. Existing bots are untouched.

RUNNING SERVICE AND DATA
Server: varma@192.168.3.136
Identity: ~/.ssh/id_ed25519_migration_192_168_3_136
Directory: /home/varma/kalshi_tennis_paper_2026_10_11
Python: /home/varma/kalshi/venv/bin/python
Service: systemctl --user status kalshi-tennis90-paper.service
Log: journalctl --user -u kalshi-tennis90-paper.service --since today
Database: paper.sqlite (WAL; use SQLite backup for consistent copies).
Atomic readout: status.json; raw receipts compressed in the database.
Dashboard: http://127.0.0.1:8516/ on this Mac through an SSH forward.
Direct LAN port 8516 is blocked by the existing firewall. No firewall change.
Reopen the forward if the Mac connection restarts:
ssh -fNT -i ~/.ssh/id_ed25519_migration_192_168_3_136 -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8516:127.0.0.1:8516 varma@192.168.3.136
The server observer continues if the Mac sleeps or the dashboard disconnects.
Create HALT_PAPER in the experiment directory to halt new paper attempts;
holdings still reconcile. Stop the named service to stop all collection.
No activation/promotion path exists in this experiment.

FROZEN DESIGN
ATP/WTA best-of-three singles; one contract per match, holding to settlement.
Infer serve strengths from the pre-match YES midpoint 30 minutes before
scheduled start, with the original 5-minute fallback. Record this quote
prospectively; it must arrive before the first point. Handle competitor
mapping explicitly. Use ATP .65 / WTA .60 base serve probabilities; update
with a 100-point prior and earlier serve points. Model and fit functions are
AST-identical to the historical study; model/config hashes are enforced.
At each completed minute use the last score at least 90 seconds old and at
most 120 seconds old. Half-blend its model probability with market midpoint.
Require >=6c predicted edge after quadratic taker fees plus 1c allowance,
ask 10-90c, spread <=5c, and >=5 contracts traded in prior ten minutes.
Rank both YES/NO sides of both competitors; one entry per match per scenario.
Max price signal ask +2c, capped at92c; actual displayed depth required.
$100 independent simulated bank per scenario; max25 contracts/$25 reserved,
$20 daily loss budget including open and pending worst-case loss; $2 cash floor.

AS-OF INPUT AND LATENCY AUDIT
Poll public game_stats every10s around scheduled play. Save complete payload
versions, request start, local first receipt, HTTP duration, Date/Age headers,
provider wall_clock, first-seen point versions and corrections. Decisions
only use a full snapshot already received by their minute boundary, so late
arrivals cannot be inserted into earlier decisions. Revisions preserve the
previous snapshot. A changed timestamp/key can appear as a new point;
complete snapshot history is retained for the review to detect removals too.
First-observed delay = local first receipt minus provider point timestamp.
Report median/p95, fraction above90/120s, and polling gaps. Startup/backfill,
>30s successful-poll gaps, revisions and future timestamps are excluded from
clean latency estimates. These exclusions are counted. Empty startup polls
do not turn previously old points into fresh latency observations.
This measures observed feed delay including polling/network time. It cannot
independently validate the provider clock or the real-world time of a point.
NTP and monotonic-vs-wall-clock checks protect local timestamp consistency.

PAPER FILLS AND IMPORTANT LIMITS
Normal: wait3s after the actual decision, then request two order books at
least1s apart. Stress: wait60s and perform the same checks. Both books must
offer one whole contract within the original cap, aggregating fractional
depth. Never count zero/insufficient depth, crossed/invalid books, inactive
markets, >5s HTTP responses, cached books with Age>1s or missed8s execution
deadlines as fills. Book depth is read from opposite-side bids. Price the
paper entry at the worst of the signal ask and both book VWAPs; charge fees
plus1c normal or2c stress. A failed attempt can retry on a later minute.
Pending attempts reserve cash/risk; durable event ownership prevents duplicate
fills across restarts. Only confirmed market settlement releases holding cash.
These are depth-supported SIMULATED fills, not actual exchange executions.
Two snapshots cannot establish queue priority, intervening cancellations,
IOC acceptance, or that displayed liquidity would have filled a real order.
Fees use the current quadratic schedule rounded to0.0001 dollars; no account
fee credits/rebates are assumed. Fee-type/multiplier changes stop that series.

Differences from the historical candle replay are deliberate execution tests:
quotes/points must be received prospectively; decisions wait for completed
minute candles and have a20s deadline; execution delay starts at actual
decision receipt, not a backdated candle boundary; book persistence is required.
No entries are reconstructed after an outage or missed pre-match quote.
Daily limits release profits on observed settlement receipt, which can lag
the historical official settlement timestamp. Drawdown in status.json is
realized, not intramatch mark-to-market. Do not present it as total risk.

PREDECLARED REVIEW
No parameter tuning on this forward sample. Initial operational report at20
settled normal trades. Main review requires >=14 calendar days AND >=100
settled normal trades (earliest October25), with no claim that100 alone proves
an edge. Stop new entries automatically after28 days (November8 at12:34 EST),
then finish settlements. Existing holdings recover and reconcile on restart.
At28 days, inadequate coverage/trades means insufficient evidence, not a pass.
Report normal and delayed-stress results, fees, friction, fill rates, missed
signals/reasons, ATP/WTA splits, daily frequency including zero-trade days,
realized drawdown, latency/correction distributions, outages and exclusions.
Use two-day block resampling for descriptive uncertainty and paired-day
comparison; count matches, not minute decisions, as trades. Positive net in
both cases is necessary for recommending further work, not sufficient proof.
If stress loses, sample uncertainty is wide, or execution/source coverage
fails, say so explicitly. No automatic real-money promotion.

HISTORICAL CONTEXT (NOT FORWARD RESULTS)
Sep12-Oct9 selected90s variant:226 trades,129W/97L,+$17.0602 after fees/1c,
8.07/day, realized DD$3.0613. Extra60s execution and2c:201,+$8.2819,
DD$4.3054; latest week in that stress was -$0.4130. Retrospective selection
after18 tennis parameter cells; not an untouched holdout or verified alpha.
Source: ../kalshi_cross_market_edges_2026_10_11/REPORT.txt and source ledgers.

VALIDATION AT DEPLOYMENT
29 offline tests passed locally and on server: original model parity, score
as-of/age constraints, revisions, startup/gap censoring, fractional depth,
cap rejection, risk reservations, idempotent fills/settlement, restart expiry,
vanished second book, isolated scenarios, GET whitelist and a mocked full
decision-to-attempt sequence. Public canary receipts separately verify both
tours' fee types, competitor mapping, candle schema and fixed-point books;
they never count as forward trades or live point-delay observations.
Initial catalogue:34 matches, earliest scheduled23:00 ET October11;
zero forward trades and zero live point-delay samples at deployment.

FOLLOW-UP
Codex heartbeat: review-tennis-90-second-paper-test, every6 hours in this thread.
Inspect only when the desktop scheduler is available; server collection runs
independently. Report meaningful failures and the20-trade/main-review milestones;
remain quiet on unchanged routine checks. Persist milestone reports in
review_state.json. Do not retune the frozen model or mix later variants into
this experiment. Follow-up authority is paper operations and analysis only.

PRIMARY API REFERENCES
https://docs.kalshi.com/api-reference/live-data/get-live-data
https://docs.kalshi.com/api-reference/market/get-market-orderbook
https://docs.kalshi.com/getting_started/fee_rounding
