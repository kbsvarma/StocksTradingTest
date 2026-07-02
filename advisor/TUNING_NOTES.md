# TUNING NOTES — data-gated decisions we deliberately did NOT make at build time

Everything here needs accrued data or live observation before it can be
decided honestly. Each item has a TRIGGER — do not act before it fires.
(Companion to INTELLIGENCE_PLAN.md; started 2026-07-02 during the full build.)

## Contract strictness
- [ ] **Promote brief_check v2 warnings → errors** (`source`, `p_win`,
      `thesis_tags`, `time_stop`, `yf_ticker`, `sizing`).
      TRIGGER: first 5 consecutive pipeline runs with zero v2 warnings.

## Learning loop (hard gates already printed by the tools)
- [ ] **Calibration conclusions** — none before n≥15 resolved;
      **conviction-map changes** none before n≥30 (calibration.py prints gate).
- [ ] **Candidate-generator weighting** — untouchable below 10 resolved calls
      per source; 10–19 = watch; ≥20 = proposable to user (attribution.py).
- [ ] **Red-team kill/amend rate** — healthy band 20-60%.
      TRIGGER: assess after 5 live runs; <10% = red-team is rubber-stamping
      (tighten prompt); >70% = synthesis bar too low or red-team noise.
- [ ] **stamp-ref honesty window** (currently 2 days) — revisit only if a
      legitimately-published view ever misses its stamp.

## Factors (all new factors ship ZERO-WEIGHTED behind the IC gate)
- [ ] **est_revision factor weight** — prospective only.
      TRIGGER: ≥13 weeks of estimates/dt= snapshots AND ic_monitor shows
      13w live IC > 0 with |t| ≥ 1.5. Then propose a starter weight (≤0.10)
      to the user; never auto-apply.
- [ ] **SUE/PEAD factor weight** — Yahoo surprise history is NOT
      point-in-time (current consensus, backfilled) → backtest ICs inflated.
      TRIGGER: ≥8 earnings-seasons-worth of OUR OWN estimate snapshots
      joined at print time, or validate2 PIT-joined IC > 0.
- [ ] **insider_net as ranked factor** — ships as candidate-source/flag only.
      TRIGGER: ≥6 months of Form 4 sweep history + cluster hit-rate in
      attribution ≥ coin-flip at n≥10.
- [ ] **Value/quality (CORE sleeve) weights** — 63-126d horizon, near-zero
      21d IC by design. TRIGGER: validate2 10y run complete AND user decides
      the book wants a slow sleeve at all (PM critique: horizon mismatch
      with a $25k 1-3-view book).
- [ ] **Regime weight re-tune** — EWMA IC halflife 8w; propose only on sign
      flip or >1σ drop sustained 3 consecutive weeks AND |t|>2.0.
- [ ] **13-week auto-de-weight rule** — REJECTED by quant panel (whipsaws on
      ~3 obs). Do not re-add without 26w+ window and t-gate.

## Pipeline cadence & cost
- [ ] **Dossier budget** (librarian --max, currently 5/night) and
      **librarian turn cap** (45). TRIGGER: first week's token usage vs
      subscription limits; raise to 8-10 names only if headroom is real.
- [ ] **SKIP_REDTEAM_AFTER_S** (45 min) + stage timeouts.
      TRIGGER: p95 stage durations from logs/pipeline_runs.jsonl after
      10 runs; tighten so p95 brief-send ≤ 09:45 ET.
- [ ] **Macro stage value** — if synthesis rarely uses macro.json beyond the
      calendar, fold S3 back into synthesis and save a session.
- [ ] **Event-session budget** (0-3/day cap). TRIGGER: first week of poller
      hits — if mostly noise (routine 8-Ks), restrict form types further.

## Quote daemon & watcher
- [ ] **Watcher cadence on local store** (30s once daemon live).
      TRIGGER: one week of daemon uptime ≥99% during RTH; else keep 300s
      yfinance fallback primary.
- [ ] **Delayed-data honesty** — stocks/futures via IBKR API are ~15min
      delayed (no live sub). TRIGGER for upgrade: user buys live US
      equities/futures API data (~$14.50/mo total from the 10089-error
      dialog) → flip those symbols to live, relabel sources.
- [ ] **IBKR read-only mode** — KEEP ON permanently (advisor is data-only).

## Validation
- [x] **validate2 first run (2026-07-02, config 107 periods/36 tests):**
      NO factor survives FDR-10% all-sample; momentum family suggestive only
      in 2024+ OOS (t≈1.8, same regime the weights were tuned in); rev_1m
      significantly NEGATIVE oos (t=-2.56 — vindicates zeroing it in
      risk_on); walk-forward net 786.9% vs SPY 248% but deflated Sharpe 0.79
      < 0.95 = NOT PROVEN. STANDING CONCLUSION: price-factor tilts are
      candidate generators, never cite them as proven alpha. Re-run monthly
      (panel refresh) — a factor earns weight discussion only if it survives
      FDR on the growing OOS window.
- [ ] **validate2 OOS freeze** — weights frozen on data ≤2023; 2024-2026
      touched once for the final report. Any post-hoc tweak restarts the
      freeze clock. Log every run's config hash.
- [ ] **Survivorship** — current-constituent universe; yfinance cannot price
      delisted names, so the bias is REPORTED, not fixed. Do not present
      backtest numbers without the label.

## Portal
- [ ] **Auth posture** — token gate ships OFF (set ADVISOR_PORTAL_TOKEN in
      the terminal plist env to arm it); decide LAN-open vs
      localhost+Tailscale after user tries phone access.
- [ ] **Calibration dashboard** renders "insufficient data" until n≥15 —
      by design, not a bug.
- [ ] **Terminal CPU** — re-measure idle % after tape moves to quote store;
      target <5% (was 26% on yfinance polling).
- [ ] **SCOR naive hit-rate metric** counts ambiguous "closed" statuses as
      losses (top row) — the calibration panel below is the honest number;
      unify once outcome_tags accumulate.
- [ ] **Events/watchdog Telegram volume** — review after week 1; both dedup
      per (check|filing, day) but combined ping count is unproven.
