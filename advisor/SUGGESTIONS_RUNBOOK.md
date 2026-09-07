# Suggestions: operation and acceptance

## What changed

Scoring v3 rewards qualified, directionally aligned evidence. Opposition is explicit and close directional contests are blocked. Rank population size/scope is carried with every contribution. V2 calibration and priors cannot enter v3; automatic generator adjustments need at least 30 nonoverlapping issue-date clusters per generator. First revisions are selected before outcome availability, preventing a later quick winner from replacing an unresolved original.

Daily suggestions are research records. The terminal separates priority research, waiting/needs research, and blocked/stale/completed. Priority requires a strong lead from a sufficiently broad population, independent directional corroboration, fresh sources, known sector and earnings context, modelled costs, and reviewed thesis fields. Missing borrow information blocks short priority. These are conservative research-triage rules, not fitted probabilities or investment-performance claims. Priority is capped at three research ideas and can be empty.

The unchanged ATR baseline remains a disclosed signal benchmark. An operator-reviewed thesis may provide its own entry band, stop, target and 1–126-session horizon with an explicit rationale. Invalid geometry is rejected. Neither plan type is advertised as a validated profitable strategy.

## Release contract

- Price builds include Open/High/Low/Close/Volume, per-file hashes, a price-bar date and schema v4. A run pins one immutable build, including nested cost/factor reads.
- Candidates retain hashed, dated source manifests, per-generator provenance, stale evidence diagnostics and a content-bound release ID. Failed/stale optional generators cannot contribute to scoring. Fresh processing timestamps cannot freshen a carried-forward failed dataset.
- Picks require the latest completed XNYS session (including holidays and half-days), with a 30-minute allowance for the closing bar. Candidate and panel build IDs must match.
- A single writer lock protects issue/resolution writes. `suggestion_releases/<hash>.json` contains the immutable result and issue rows. The ledger is written and fsynced before atomic replacement of `picks_latest.json`, the sole commit point.
- Readers follow the committed release chain. Orphan rows from a pre-commit crash are ignored. Lost issue mirrors are recoverable from committed archives; a partial final journal line is repaired before append. Interior corruption or a digest mismatch is a hard failure.
- Suggestions have episode IDs, revision IDs, predecessor links and material fingerprints. Same-day retries do not issue twice. A changed plan, including a reversion, gets a new revision. Removed daily candidates are reported without falsely declaring their thesis invalidated.

Do not manually edit a committed release or delete its predecessor archives. Back up the research release directory, opportunity archives and picks ledger together. Hashes detect corruption; they are not a replacement for host access control or signatures from an independent authority.

## Reviewing a thesis

Use the currently generated candidate's evidence. Prepare a JSON file containing:

```json
{
  "direction": "long",
  "why_now": "Describe the newly verified change",
  "catalyst": "Describe the dated catalyst",
  "invalidation": "Describe what disproves the scenario",
  "horizon_rationale": "Explain the chosen review window",
  "source_urls": ["https://example.com/replace-with-actual-evidence"],
  "plan": {
    "entry_low": 99,
    "entry_high": 101,
    "stop": 94,
    "target": 115,
    "horizon_td": 10
  }
}
```

These example numbers are fixtures, not a recommendation. With the Advisor environment activated, import the actual reviewed file using `python -m advisor.research.underwriting --ticker XYZ --file /absolute/path/thesis.json --reviewer operator-name --ttl-hours 24`. The command requires fresh, verified candidate sources and binds the review to their evidence hash. It records operator research review, not independent red-team approval. Rebuild candidates and picks to reassess eligibility. Any evidence change or review expiry requires a new review. The maximum review lifetime is seven days.

## Quote and display contract

The quote daemon subscribes to daily suggestions as well as standing views. The terminal does not perform an untimestamped Yahoo fallback. It validates both the daemon snapshot age and each quote's event age, and displays source/type/timestamp. Delayed or unavailable quotes stay in the waiting group. Volume-only and prior-close updates cannot refresh a last-price observation. Both sides of a midpoint must have recent price updates. Yahoo fallback work is bounded to four workers and cannot block IB event processing; removed subscriptions are cancelled, and reconnects rebuild subscriptions.

## Outcome and learning contract

New issue rows record entry bands, issue time, costs, plans and revision identity. `entry_band_daily_v1` starts exposure only on an eligible future bar; it cannot use the elapsed portion of an intraday issue bar. Gap stops use the next open. Unknown within-bar entry/exit order stays ambiguous. Missing opens or short borrow costs cannot produce cost-complete returns. Prices are adjusted daily research bars and results remain simulations, not broker fills.

Unstopped signal comparators mature independently after early exits. The primary scorecard covers the current live scorer; legacy/replay attribution remains separately identified. `ranking_evaluation.json` compares selected names with the entire committed issue-time opportunity population, waits for complete matched windows and summarizes nonoverlapping periods by version. It is a gross signal diagnostic and cannot automatically promote a model. Replay is always labelled backfill, uses neutral priors and does not borrow today's calibration or cost estimates.

## Failure handling

`nightly_status.json` distinguishes required stages from optional failures. Candidate failure prevents a picks run; missing required outputs return a nonzero job exit. `suggestion_health.json` records suggestion-build failure while preserving the last-good release. The terminal labels prior output stale/failed; it does not offer it as current priority research. Both watchdogs check suggestion freshness/integrity and alert on failure/recovery transitions, retrying failed notification delivery.

For a failed refresh, inspect the recorded stage and upstream provenance, restore the dependency, then rerun the research job. For a digest/interior-journal failure, restore a coherent backup and investigate before issuing again. Do not bypass validation by editing health fields. A missing legacy journal mirror alone is recoverable from committed archives; a missing committed archive is not silently recreated.

## Rollout and remaining external acceptance

Run the complete Advisor test suite in the staged environment. The new calendar dependency is pinned in `requirements.lock`. Rebuild the price panel once to obtain Open bars, then run the complete nightly job to produce fresh, bound candidates and picks. A legacy list is deliberately not grandfathered into current priority status. Use the existing staged deployment/rollback workflow; this code change does not itself deploy or enable trading.

The executor remains disabled by default. If separately enabled, it refuses execution without its inline monitor, rejects holidays/closed sessions, omits optional market-data enrichment before protection, and runs fill journaling/notification outside the monitor's critical path. Broker/process-failure recovery still requires a deployed integration exercise; mocked tests cannot establish real fill-to-protection latency.

Local acceptance covers deterministic behavior, publication crash recovery, provenance, replay isolation, operator-review-to-priority flow, simulated entries/exits and actual Streamlit fragment rendering. Commercial/enterprise production acceptance additionally requires licensed data, deployed authentication/TLS and access controls, backup/restore and load/soak exercises on the intended host, broker integration if execution is used, and independent prospective performance/calibration evidence. No numeric enterprise rating or proven-alpha claim is made from software tests.
