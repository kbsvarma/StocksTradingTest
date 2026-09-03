# Advisor Terminal commercial-readiness gates

The runtime is deliberately fail-closed for investment action. Passing its
technical trust checks does **not** mean the product is legally or commercially
ready to sell.

## Hard launch gates

1. Replace or contractually license every Yahoo/yfinance-derived dataset used
   in a customer-facing product. Yahoo's developer terms distinguish services
   that permit commercial use from services restricted from paid/SaaS use:
   https://legal.yahoo.com/us/en/yahoo/guidelines/ydn/index.html
2. Obtain display and redistribution rights for every exchange/IBKR market-data
   field shown to third parties. A personal API subscription is not a customer
   redistribution license.
3. Complete U.S. federal/state investment-adviser and, where applicable,
   broker-dealer/FINRA counsel review before describing the output as advice or
   recommendations. SEC guidance treats automated advice as a means of
   providing advisory service, not a separate category:
   https://www.sec.gov/about/divisions-offices/division-investment-management/electronic-filing-investment-advisers-iard/frequently-asked-questions-form-adv-iard
4. Implement per-customer identity, authorization, portfolio/risk profile,
   privacy consent, retention/deletion, immutable access/audit logs, TLS, key
   rotation and incident response. The current LAN shared-token deployment is
   an interview/demo environment only.
5. Establish written model governance: owner, inventory, versioned approvals,
   challenger tests, drift thresholds, override process, incident taxonomy and
   rollback evidence.
6. Establish a compliant performance-reporting policy. Backtests, targets and
   extracted results must never be presented as actual client performance.

## Product claims currently permitted by the code

- `research_idea`: scenario-based research with explicit invalidation and
  uncalibrated probability labeling.
- `actionable_idea`: blocked unless the probability is empirically calibrated
  on at least 30 resolved scored observations and portfolio context is verified.
- Live execution: disabled by default and requires independent config **and**
  environment opt-in.

## Technical controls currently implemented

- Exact-file model write permissions; no model stage can mutate the journal,
  watchlist, proposal queue, notifications, or final publication.
- Deterministic draft/red-team merge, schema/risk/evidence/language validation,
  atomic journal batch, commit receipt, final-artifact hash attestation, and
  prior-publication preservation on failure.
- Staged deployment tests, release-tree hashing, post-restart service/HTTP
  health checks, automatic rollback, locked dependencies, and systemd sandboxing.
- On-demand generation uses a one-shot 60-second arm, cooldown, single-flight
  locks, credential-isolated service execution, and an append-only control log.
- Actionability remains false until current publication attestation, runtime,
  release, data, journal, portfolio, and empirical-calibration gates all pass.
- A machine-readable provider inventory reports freshness, purpose and
  redistribution status. Critical price, SEC, FRED and quote sources must be
  healthy before a view can be classified as actionable.
- The technical-state layer computes 15 independent trend, momentum,
  volatility, participation and risk diagnostics over the full liquid panel;
  it is explicitly descriptive until prospective evidence promotes it.
- SEC-filed growth/quality diagnostics provide an independent fundamental
  cross-check that does not rely on Yahoo profile fields and retains filing
  dates for point-in-time auditability.
