NFL WEEKEND BOT — prepared October 16, 2026

Current state: separate observer deployed; VERIFY_ONLY; no NFL financial orders
submitted and no cash transferred. The order-capable executor is staged behind
an explicit readiness gate. Do not describe this as already live.

Server: varma@192.168.3.136
Directory: /home/varma/kalshi_nfl_live_2026_10_16
User service: kalshi-nfl-weekend.service
Python: /home/varma/kalshi/venv/bin/python
Service starts on boot; the user has persistent services enabled. It exits
after the October20 04:00 ET entry cutoff once its own holdings settle. It
does not start, stop, restart, reconfigure or write into any existing bot.
Canceled weather/resting experiments and notification automations stay canceled.

Scope
13 physical NFL games, 26 matched team outcome markets:12 on Sunday Oct18,
one on Monday Oct19. First kickoff Sunday09:30 ET, Houston/Jacksonville.
Collection watches books20min before kickoff and polls scores2min before it.
The strategy enters only in the final5min of Q4; these are not kickoff bets.

Strategy retained from the selected research
Current leader, regular/postseason; completed-minute signal ask90–<95c,
spread<=2c, prior10min volume>=5. Buy5 contracts, one owned position per game,
using the cheapest qualifying equivalent YES-leader or NO-opponent route.
Tie rules must explicitly resolve both sides to50c. Price cap=min(signal
ask+1c,98c), rounded toward lower spending on the market's current tick ladder.
Five contracts means requested quantity; terminal partial fills are retained,
never topped up automatically. Hold through official settlement including OT.
No stop-loss, alternate parameter grid, paid feed or extra fitted sports rule.

Original replay assumed30s score lag and3s execution. This implementation
uses only source versions received before each decision minute, excluding plays
newer than30s and older than120s. It additionally rejects a newer observed play
that contradicts the intended leader. Dispatch/simulated execution must occur
within3s of the signal minute, not3s after an arbitrarily delayed calculation.
Missing completed candles can be observed for10s for diagnosis; this does not
extend the3s execution deadline. Prices above the original cap are never chased.

NFL accounting
Virtual bankroll cap$100; requested size5; max25 contracts/$25 reserved cost;
$20 daily realized losses PLUS outstanding worst-case cost; $2 cash floor.
Fees count toward exposure. Because outstanding risk counts toward the daily
budget, four near95c entries can use most of the$20 and block a fifth. The
25-contract cap is a ceiling, not a guarantee of five concurrent positions.
An additional$25 of shared engine cash is protected for the existing bots.
No new funded account/subaccount was created. Engine0/subaccount0 routing was
checked; available cash was$109.6296 at the initial read-only check. This is
shared account cash, not proof that$100 has been segregated for NFL.

Verification completed now
73 offline tests: source causality/corrections/live unfinished drives, score
and price boundaries, complementary-route deduplication, Decimal fees, risk,
partial fills, ambiguous acknowledgements, terminal zero fills, stream sequence
gaps, stale snapshots, NO-coordinate conversion and fill receipt accounting.
Current API candle schema, fee metadata, team mapping and authentication checked.
After fixing synchronized probe bursts,410 stable streamed/REST comparisons
matched, zero mismatches;37 changing-book comparisons excluded.892 pregame
depth probes had5 contracts available at their observed best ask. These were
liquidity probes, not qualified strategy signals or real fills.
Book-probe p95 fell from5.921s to0.509s; maximum after fix0.519s. This includes
the deliberate public-read rate limiter. Stream receipt lag:median13.74ms,
p95 18.59ms, max48.96ms over2911 timestamped frames. Local depth calculation
p95 0.055ms. A controlled disconnect recovered all26 books in0.838s. These
numbers do not measure an IOC acknowledgement or a real NFL fill.
36 existing Kalshi/checkpoint source hashes and tracked service PIDs unchanged.
See verification.json, schema_checks.json and retained raw receipts for evidence.

What still must pass on Sunday
At least30 clean prospective play first arrivals spanning20min, per game;
95th percentile source-wallclock-to-receipt delay<=30s; current source reads
within8s; no future source timestamps; minimum10 stable in-game book/depth
probes with p95<=3s;100 stable comparisons overall without mismatches; NTP,
fresh fee metadata/catalogue, healthy stream and no dropped raw receipt data.
At least one qualified five-contract simulated execution under the frozen cap
and3s deadline must be observed before arming. Each later game must pass its
own live score-feed checks. Normal source timestamps are provider event times,
not independently observed stadium time; measured delays are explicit bounds.
First receipt, bootstrap exclusions, HTTP cache headers, corrections, polling
gaps and all rejected signals are preserved. No after-game archive is counted
as a measured live first arrival. No positive paper result proves the edge.

There is no automatic live promotion. activate.py without arguments performs a
read-only readiness check. After the above actual live evidence is reviewed,
activate.py --execute rechecks cash/exchange state and offline tests, and writes
a code/config-bound expiring ARMED.json only if all checks pass. Friday's check
must fail the live-evidence requirements. The server collector runs meanwhile.
The service picks up a valid token without restarting existing bots. A local
HALT file blocks new NFL entries, while its own reconciliation continues.

Execution/reconciliation
Authenticated orderbook_delta and fill streams; fresh sequence-valid snapshots
and incremental updates; no paid Pyth channels. Fixed5-contract IOC via V2;
intent committed before POST. Mutations are never retried after a network error.
An unresolved order blocks further NFL risk and is found by unique client ID.
Only terminal canceled+zero count+zero remaining+empty fill receipts allows a
new attempt at a fresh later qualifying minute. Filled/partial orders permanently
own the physical game. A stray resting order can only be canceled by this
bot's exact durable NFL order ID; no blanket cancellation API is used.
WebSocket fills require matching terminal order count/cost/fees; paginated
read-only REST reconciliation is the fallback. Real and simulated results are
reported separately. Live net uses actual entry costs/fees; simulated net adds
1c/contract friction. No real NFL fill receipt has yet been generated.

Useful read-only operations (run within this directory)
python review.py                     save timestamped read-only ledger report
python activate.py                   readiness check, never arms by default
systemctl --user status kalshi-nfl-weekend.service
cat status.json                      refreshed every5s

Research reference
../kalshi_nfl_weekend_2026_10_16/REPORT.txt retains the111 modeled trades,
108W/3L,+$22.1719 and the fresh5-trade loss of$3.2846. They remain modeled,
not live results; this readiness work does not strengthen that statistical edge.

Primary protocol references inspected October16
https://docs.kalshi.com/api-reference/orders/create-order-v2
https://docs.kalshi.com/websockets/orderbook-updates
https://docs.kalshi.com/websockets/user-fills
https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20261018
