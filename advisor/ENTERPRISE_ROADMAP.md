# Enterprise roadmap — from research harness to tradeable product

Written 2026-09-03. Companion to `COMMERCIAL_READINESS.md` (legal/commercial
gates) and `INTELLIGENCE_PLAN.md` (intelligence design). This document answers
a narrower question: **what stands between today's system and a bot that can
be sold and trusted with real money?**

Grounded in the system's own deterministic verdict, not opinion:

```
production_status : degraded
actionable_recommendations_allowed : False
commercial_launch_allowed          : False
factor model      : research_only / discovery_only
  out-of-sample deflated Sharpe 0.746  (gate: 0.95)
  out-of-sample FDR-10% survivors: none
  independent live-IC windows: 2       (gate: 12)
  no approved model artifact matching the validation config hash
```

## The honest headline

**The engineering is close to enterprise grade. The alpha is not proven.**

Those are separable problems and they have wildly different timelines. Almost
every "make it production ready" instinct attacks the engineering — which is
already the strong half. The binding constraint is evidential.

A trading product makes exactly one promise: *this process makes money after
costs, and here is the evidence*. Today the system's own validation says the
factor edge is indistinguishable from luck after multiple-testing correction.
That is not a defect — it is the harness working, and it is the single most
credible thing about the project. But it means **any marketing of alpha today
would be a claim the system itself refuses to make.**

---

## TIER 0 — Prove the edge (existential; everything else is secondary)

Nothing below matters commercially until this moves.

| Gate | Now | Needed | Honest ETA |
|---|---|---|---|
| OOS deflated Sharpe | 0.746 | ≥0.95 | unknown — may never clear |
| OOS FDR-10% survivors | 0 | ≥1 | unknown |
| Independent live-IC windows | 2 | 12 | **~1 year** (1 per 21 trading days) |

Actions:
1. **Let the live-IC clock run.** It is the only un-gameable evidence in the
   system. 12 independent windows ≈ 12 months. Start counting from a frozen
   config hash; any parameter change restarts it.
2. **Stop mining the price-factor family.** 10y/107-period validation already
   said no. Additional variants of momentum/reversal on the same universe are
   the definition of the garden of forking paths.
3. **Test the generators that actually have priors** — PEAD/SUE, estimate
   revisions, insider clusters — where the literature effect is documented and
   the system already has PIT plumbing. Down-cap, where a $25k–$5M book has
   capacity that institutions do not.
4. **Measure the kill list as a product.** Attribution already shows kills
   have signal (a killed SNDK short avoided +21%). "Which ideas NOT to take"
   may be more defensible than "which to take" — and it is uncrowded.
5. **Trade paper at production fidelity for 6–12 months** with fills priced at
   quotes that existed at the decision instant (the causal-replay discipline
   already in the doctrine).

**Commercial consequence:** until this clears, the sellable asset is the
*governance harness*, not the signal. See Strategy B below.

---

## TIER 1 — Execution & risk platform (what makes it a "bot")

Today's execution surface is deliberately tiny and is **not** a trading system:
one instrument family (`SPXW/SPX/NDXP`), `max_qty: 1`, one execution per day,
human-approved per trade, via a single broker path. That is a safe hobby
harness, not something that can run capital.

Missing, in dependency order:

1. **Order management (OMS)** — order lifecycle state machine, idempotent
   client order IDs, partial fills, cancel/replace, venue rejects, resubmission
   policy, end-of-day flat/carry rules. Today's path handles one spread.
2. **Execution quality (TCA)** — arrival-price benchmark, implementation
   shortfall, slippage vs quoted mid, fill-rate by order type. Without this you
   cannot know whether the strategy or the execution lost the money. The repo
   already has hard evidence this matters: combo MARKET orders cost 77%
   slippage, which is why LIMIT walk-down is invariant.
3. **Portfolio risk engine** — gross/net exposure, per-name and per-sector
   caps, factor exposure limits, correlation-aware sizing, leverage, drawdown
   governor, and a **kill-switch hierarchy** (per-strategy → per-account →
   global). Today's caps are per-proposal, not portfolio-level.
4. **Position/PnL reconciliation at scale** — broker vs internal state, every
   cycle, with automatic halt on divergence. Exists for one bot; must be a
   platform service.
5. **Corporate actions** — splits, dividends, M&A, ticker changes on live
   positions. Currently unhandled; a split silently corrupts a position.
6. **Multi-broker abstraction + failover** — one broker outage should degrade,
   not stop, the book.

Effort: this is the largest engineering block — **3–6 months** for a credible
single-asset-class OMS/risk stack, longer for multi-asset.

---

## TIER 2 — Data (licensing and survivorship)

1. **Licensing.** yfinance is the backbone and is not commercially licensable.
   Replace with a licensed vendor (Polygon / Databento / Nasdaq / Refinitiv)
   before any customer sees a number. Already blocker #1 in
   `COMMERCIAL_READINESS.md`.
2. **Survivorship-free history.** Every backtest today is an upper bound —
   current-constituent universe, no delisted names. A licensed vendor with
   point-in-time constituents is the only real fix; the harness already labels
   the bias honestly, which is the right interim behavior.
3. **Vendor redundancy** with automatic failover and per-source SLAs; the
   `source_health` layer is the right shape, it needs a second provider behind
   it.
4. **PIT discipline extension.** EDGAR `filed`-date joins are already correct;
   extend the same rigor to estimates and short interest (currently our own
   accrued snapshots, which is honest but young).

---

## TIER 3 — Platform (multi-tenant, secure, available)

Today: single user, file-based state, one box, LAN shared token, plaintext
`~/.trendbot_env` secrets, no TLS.

1. **Datastore** — Postgres for journal/watchlist/proposals/positions with
   transactions and row-level isolation per tenant. Files are the right call
   for one user and the wrong call for many.
2. **Identity & authz** — OIDC/SSO, RBAC (viewer / analyst / approver /
   admin), per-customer data isolation, session management. A shared LAN token
   is a demo control.
3. **Secrets** — vault/KMS with rotation; no credentials on disk in plaintext.
4. **Immutable audit** — every recommendation, approval, order and override,
   append-only and tamper-evident (WORM retention if RIA/BD applies).
5. **HA/DR** — the box is a single point of failure. Warm standby, automated
   backup with periodic *restore drills* (untested backups are not backups).
6. **TLS everywhere**, no plaintext transport.

---

## TIER 4 — Observability & SRE

Today: log files, a 5-minute watchdog, Telegram pages (now muted).

1. **Metrics** (Prometheus): pipeline stage latency/success, data freshness,
   quote-feed health, order/fill counts, drawdown, model gate state.
2. **Tracing** across the stage DAG — currently a heartbeat JSONL.
3. **SLOs + error budgets**: "brief published by 09:45 on 99% of trading days",
   "quote staleness <60s during RTH p99".
4. **Alert routing/escalation** (PagerDuty-class), not a single chat.
5. **Runbooks per failure mode** — several already exist informally in
   `TUNING_NOTES.md`; formalize.
6. **Chaos/failure injection in CI** — `test_failure_injection.py` is a strong
   start; extend to broker/venue/data outages.

---

## TIER 5 — Model governance

The harness is unusually good here already (fail-closed actionability bound to
a `calibration_id`, release tree hashing, deterministic merge). To reach
institutional standard, add:

1. **Model inventory & versioning** — every deployed model with owner,
   approval record, config hash, and effective dates.
2. **Challenger models** run in parallel; promotion only on evidence.
3. **Drift monitoring** with pre-registered thresholds and automatic demotion.
4. **Segregation of duties** — the approver must not be the author. Today a
   single operator can do both.
5. **Backtest↔live parity harness** — assert the live path reproduces the
   backtest on the same inputs. This catches the single most expensive class of
   quant bug.

---

## TIER 6 — Commercial packaging

Beyond Codex's legal gates: pricing model, SLA/uptime commitments, onboarding,
support, incident comms, and a **performance-reporting policy** that never
presents backtests or paper results as client returns (SEC Marketing Rule).

---

## Two strategies — pick deliberately

**Strategy A — sell the alpha (a bot that trades).**
Blocked by Tier 0 (evidence) and Tier 1 (execution platform) plus regulation.
Realistic horizon: **18–30 months**, and it may fail at Tier 0 on the merits.
Do not market alpha before the model gate clears; the system will contradict
you in writing.

**Strategy B — sell the discipline (research-integrity infrastructure).**
This is the genuinely differentiated asset *today*: a fail-closed research
harness that refuses to publish unvalidated claims, with deterministic
attestation, red-team adversarial review, calibration-bound actionability,
kill-list attribution, and release integrity. Buyers: quant teams, family
offices, RIAs who need to prove their process. It sells the thing that already
works and makes no return claims — so it dodges most of Tier 0 and much of
Tier 5.

**Recommendation:** run B commercially while A accrues evidence. They share
the same codebase, and B funds the year that A needs.

---

## Sequenced plan (next 12 months)

| Phase | Focus | Outcome |
|---|---|---|
| 0–1 mo | Freeze config hash; restart live-IC clock; re-enable brief on a token budget; formalize SLOs | Evidence clock running |
| 1–3 mo | Licensed data vendor for one asset class; Postgres + auth + secrets vault; metrics/tracing | Multi-tenant-capable, licensable |
| 3–6 mo | OMS + portfolio risk engine + TCA + reconciliation service; backtest↔live parity | A real execution platform |
| 6–12 mo | Challenger models, drift automation, segregation of duties, DR drills; Strategy-B packaging | Institutional-grade harness |
| 12+ mo | If and only if the model gate clears: small live capital, tight caps, TCA-measured | Alpha claim earned, not asserted |

## What NOT to do

- Do not market alpha before `actionable_recommendations_allowed` is True.
- Do not loosen the gates to make the dashboard look better. The gates are the
  product.
- Do not add factor variants to the price family hoping one clears FDR.
- Do not put real capital behind a strategy whose fills have never been
  measured against arrival price.
