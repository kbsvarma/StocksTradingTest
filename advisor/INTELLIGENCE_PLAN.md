# INTELLIGENCE PLAN — making the advisor a materially smarter idea generator

Status: **FULL BUILD SHIPPED 2026-07-02** — every planned subsystem live in
one push (user directive: build everything, tune later — see TUNING_NOTES.md
for every data-gated decision deliberately left open). Commits intel-wk1 →
intel-M10. Highlights beyond §7: EDGAR layer (companyfacts PIT + Form 4
sweep + insider clusters), candidates slate + fundamental factors (gated),
watchlist machine, themes memory + macro stage (4-stage 08:15 pipeline),
post-mortems + doctrine flow, filing poller, IV snapshots (16:15),
validate2 10y harness (VERDICT: no factor survives FDR-10%; deflated Sharpe
0.79 = walk-forward NOT PROVEN — factor tilts are candidate generators, not
alpha), IBKR quote daemon (SPX/VIX LIVE) + 30s exit-watcher, 8-tab portal +
watchdog. · Plan written 2026-07-01
Provenance: designed via multi-agent deep dive (2 grounding audits, 6 subsystem
designs, 3-critic adversarial panel: quant skeptic / ops realist / PM).
Scope decisions (user, 2026-07-01): IBKR streaming OFF for now (free data:
yfinance + EDGAR + FRED); Claude usage is NOT a constraint (target 10–20
headless sessions/day, evaluate after 1 live week); IPS action tier untouched
(max 1–3 published views/day, 0 valid, adversarial kill loop stays).

---

## 1. Thesis

Today the brain is one cold-start 60-turn Claude session reading 6 price-only
factors. The upgrade is NOT "more AI opinions" — the panel was unanimous that
the LLM's edge here is **synthesis, verification, and veto — not prediction**.
Four capabilities close the gap:

1. **Know more** — estimate revisions, earnings surprises/PEAD, insider
   clusters, point-in-time EDGAR fundamentals. All verified working free from
   this machine (probe: full ~15-endpoint pull for 3 tickers in 8.5s, zero
   rate limits; EDGAR companyfacts 3.7MB/0.64s with UA header; FRED CSV live).
2. **Remember** — per-name dossiers with kill-lists, watchlist triggers,
   market-narrative memory. Guardrail: **facts persist, opinions re-earn** —
   METHODOLOGY's "no legacy opinions" rule stays intact.
3. **Argue with itself for real** — a fresh-context red-team session that sees
   only the draft views and must kill them. Self-graded Loop D is structurally
   weak; an adversary with no sunk cost is the single best intelligence
   upgrade per dev-day (all three critics agreed).
4. **Learn from outcomes** — code-stamped reference prices on every view AND
   every reject, R-multiples, conviction calibration, hit-rate by idea source.
   Nothing else is judgeable until this exists.

PM insight adopted: PEAD and insider-cluster edges are strongest in small/mid
caps where institutions can't bother; a $25k book has zero capacity
constraint — the candidate slate deliberately fishes down-cap.

## 2. Retractions and cut list (decided, do not re-litigate without new data)

- **Retract the +78.6% walk-forward headline** (beta 1.38, no costs,
  survivorship-biased, 11 observations). Never cite it as evidence.
- **Not factors:** short-squeeze ranking (biweekly data of unknown vintage =
  difference of two stale numbers), analyst PT momentum (IC 0.01–0.02,
  coverage churn), yfinance IV skew/term-structure (stale mids → fake
  precision; keep only coarse ATM IV/RV cheap-rich flag).
- **No 30-min intraday scanner / reaction sessions** — delayed data, no edge,
  alert spam erodes trust in real alerts.
- **No score-decay idea inventory** — aging scores create pressure to publish;
  poison for a 0-views-valid system. Ledger is journal rejects + watchlist
  states only.
- **No auto-de-weighting of factors on 13-week IC** — whipsaws on ~3
  observations. Drift rule: EWMA IC (halflife 8w), propose change only on
  sign flip or >1σ drop sustained 3 consecutive weeks AND |t|>2.0; user
  approves via Telegram; never auto-applied, never mid-week.
- **One canonical dossier format, one ledger** (the six designs produced four
  dossier schemas and two ledgers; reconciled in §3).
- **Defer CORE 63–126d value sleeve machinery** until any new factor shows
  positive live IC; every published view must still declare its horizon.
- SUE/PEAD caveat: yfinance surprise history is not point-in-time (current
  consensus, backfilled). Treat backtest ICs as inflated; validate
  prospectively from our own snapshots.

## 3. Canonical data architecture (all under advisor/data/)

```
knowledge/
  dossiers/<TICKER>/facts.json       # machine-maintained, ≤8KB, every field {value, as_of, source}
  dossiers/<TICKER>/narrative.md     # Claude-maintained, ≤300 lines: business model, bull/bear
                                     #   (dated bullets), thesis history, KILL LIST w/ revisit-if
  dossiers/<TICKER>/meta.json        # state, staleness (computed by code, not opinion)
  watchlist.json + watchlist_log.jsonl   # state machine: candidate→researched→watchlist→
                                          #   active_view→resolved→post_mortem→dormant
  narrative/current_themes.md        # 3–7 themes, dated evidence, "what kills this theme"
  narrative/themes_archive/YYYY-Www.md
  evidence/evidence.jsonl + index.json   # dated sourced claims; citable only if ≤5td old or
                                          #   intrinsically dated; supersedes-chain for refresh
research/
  snapshots/info/dt=YYYY-MM-DD.parquet   # 16-field .info panel, never backfilled
  estimates/dt=YYYY-MM-DD.parquet        # eps_trend 7/30/60/90d + up/down revisions + rec counts
  events/earnings_calendar.parquet + earnings_history/<TICKER>.parquet
  events/analyst_actions.jsonl
  fundamentals/edgar_facts/<TICKER>.parquet  # long format: concept, value, period_end, FILED, form
  positioning/short_interest.parquet + form4/dt=.parquet + holders/<TICKER>.parquet
  regime_history.jsonl                   # nightly detect_regime() append (fixes no-regime-history)
  factor_history/YYYY-MM-DD.parquet      # nightly full score vectors → maturing live IC series
  signals/dt=YYYY-MM-DD.json             # immutable archived sheets (score vs archive, never recompute)
  candidates_latest.json                 # stratified ~50-name slate (§6)
  _meta/ingest_manifest.json             # per-dataset last_run/rows/errors; 09:00 reads staleness
ledger: rejected ideas are JOURNALED (type:"rejected" + ref_px), not a parallel store
schemas/                                 # JSON-schema per artifact + generic contract_check.py;
                                         # every session prompt cites the schema FILE, never embeds it
```

Point-in-time discipline (non-negotiable):
- EDGAR `filed + 1 trading day` is the only true PIT clock — all fundamental
  backtests route through it.
- Yahoo data has no history — manufacture it: snapshots append-only, stamped
  with observation date, never backfilled, never proxied from current values.
  Revision/SI factors are prospective-only until ~3 months of history accrues.
- Factor sheets immutable once archived; weekly review scores against the
  archive.

## 4. Session topology (~10–13 sessions/day at full build-out)

| Time | Session | Purpose / contract | Turns |
|---|---|---|---|
| ~19:00 | Librarian ×3–5 | Deep dossier research (deep_pull.py CLI + EDGAR filings + web bear-case); writes facts.json/narrative.md. EVENING placement is deliberate: heavy research can never starve the 09:00 money chain of subscription quota | 35 |
| 06:00 | (code) ingest + snapshots + factor build; post-mortem session per newly resolved call → lessons.jsonl | 15 |
| 08:15 | Macro/calendar | Facts + calendar + anomaly explanations ONLY — no regime punditry (zero edge in WebSearch macro opinions) → macro.json | 25 |
| 09:00 | Synthesis | Drafts 0–4 views w/ full numeric levels; does NOT publish/journal/send → views_draft.json | 40 |
| 09:20 | **Red-team** | Fresh context; sees only draft claims + dossiers; verifies every evidence URL, mandatory ≥2 disconfirming searches per view, crowding check vs open calls → survive/kill/amend | 30 |
| 09:35 | Publish | Mechanical merge; journals views AND rejects verbatim w/ ref_px; brief_check gate; Telegram send | 15 |
| Sun | Review + calibration + doctrine | Scores calls, runs calibration.py/attribution.py, proposes prompt/weight diffs → user YES/NO | — |

Orchestration rules (ops critic, adopted wholesale):
- **Sequential only, no parallel claude processes** (FD-limit risk: one session
  already needs ulimit -n 65536). Every stage optional except publish.
- Any stage fails → degrade to today's single-session behavior; brief stamped
  "DEGRADED"; never silent. Hard send deadline 09:45.
- 06:00 pre-flight `claude` auth check → Telegram alert on failure (the known
  /login failure mode now has 10× blast radius).
- One pipeline_runs.jsonl heartbeat row per stage.

## 5. Journal schema v2 (learning loop substrate)

`view` entries add: `ref_px` (code-stamped by `journal --stamp-ref` after the
brief lands — the model cannot fudge it), `source` enum (factor_long/short/
shock, news_loop, insider_cluster, revision_leader, macro_thematic, …),
`p_win` (0.50–0.85; legacy map high=0.70 medium=0.55), `thesis_tags` (fixed
taxonomy). New `rejected` type: ref_px + source + kill reason → counterfactuals.
`resolve` adds: `exit_px, exit_ts, entry_px_used, realized_return_pct,
realized_r, mae_r, mfe_r, holding_days, spy_return_pct, outcome_tag`
(thesis_right_win | lucky_win | thesis_right_loss | thesis_wrong_loss |
never_triggered | expired_flat).
exit_watcher additions: per-call max/min px → excursions.json (MAE/MFE free);
on level hit append machine `resolve_pending` row (alert-only stays true).
Discipline: no calibration conclusions until n≥15 resolved; no conviction-map
changes until n≥30; candidate-generator weighting untouchable until ≥10
resolved calls from that source.

## 6. Candidate slate (replaces raw top-20 momentum handoff)

`candidates.py` → stratified ~50 names: tactical longs/shorts (10+5), PEAD
fresh ≤10d |SUE|>1.5 (≤6), insider clusters (≤5), revision leaders (≤5),
cheap-quality (≤5), new-entrant deltas (≤5), squeeze FLAGS (≤3, flag not
factor). Each entry carries factor z-scores, next earnings date, days since
report, insider/SI stats, FV upside. Multi-bucket confluence ranks first.
Loop A becomes "triage the slate", not "read top-20". The 1–3-view exit gate
is untouched — this widens the funnel's mouth, not its exit.

## 7. Build sequence

**Week 1 — SHIPPED 2026-07-02:**
1. ✅ Learning loop v1: journal v2 (`--stamp-ref` w/ 2-day honesty window,
   `--fields` on resolve, rejected type, data-only merge for stamp/
   resolve_pending), exit_watcher excursions.json (MAE/MFE) +
   resolve_pending rows, research/outcomes.py + calibration.py +
   attribution.py, brief_check v2 warnings, weekly_review.md rewired to
   run the scorers.
2. ✅ Snapshotters: research/ingest/{snapshots,estimates,events,runner}.py
   → snapshots/info + estimates + events parquet (nightly, after signals,
   `--no-ingest` for inline rebuilds), regime_history.jsonl +
   factor_history/ score snapshots in nightly.py. research/peek.py gives
   sessions per-ticker readouts of the parquet stores.
3. ✅ Red-team split: prompts/{synthesis,redteam,publish}.md,
   orchestrator.py (sequential, preflight auth ping, legacy fallback,
   UNREDTEAMED stamp, 45-min deadline guard, publish retry, telegram
   failure alerts, heartbeats to logs/pipeline_runs.jsonl, post-publish
   stamp-ref), research/redteam_check.py, run_brief.sh rewired.
   NOTE deviation: time_stop/yf_ticker/sizing/source/p_win are WARNINGS for
   the first clean week (promote to errors after), not immediate errors.
4. ✅ Dossiers v1 + librarian: research/deep_pull.py (facts.json builder,
   verified on live data), knowledge/dossiers/<T>/{facts.json,narrative.md,
   meta.json}, research/librarian_queue.py, prompts/librarian.md,
   run_librarian.sh + com.stockstest.advisor-librarian (19:00 ET weekdays,
   loaded). METHODOLOGY doctrine section added. 26 tests pass
   (7 new in tests/test_learning_loop.py).

**Weeks 2–3 (~6 days):** EDGAR layer (companyfacts ~20 concepts, timeboxed;
Form 4 daily-index market sweep as candidate source — mind QTR-path 403);
watchlist state machine + numeric revisit-triggers; themes/narrative memory +
Loop B rewrite (start from themes, verify, edit); macro session split;
candidates.py slate; new factors behind prospective IC gate (SUE/PEAD,
est_revision); brief.json v2 (calendar, ledger_top, narrative delta) +
Telegram blocks; post-mortem sessions; terminal: dossier tab + calibration
dashboard (graceful-empty until data accrues).

**Deferred until the 1-week trial says continue:** validate2.py full overhaul
(FDR/block-bootstrap/costs/deflated Sharpe/10y panel — honest but validates
factors with no history yet), vol_check IV-rank rebuild from accrued
snapshots, event-triggered sessions, parallel dossier runners, full DAG
orchestrator.

## 8. Pre-registered 1-week trial scorecard (no mid-week parameter changes)

1. 100% of views AND rejects ref-price-stamped from day 1 — non-negotiable.
2. Funnel counts: candidates → dossiers → drafts → red-team → published,
   with drop reasons at each stage.
3. Red-team kill/amend rate 20–60% with cited counter-evidence
   (0% = theater, 100% = broken).
4. Evidence audit: sample published claims daily; % wrong or stale.
5. Forward-return tracking armed on the WHOLE slate — published-vs-slate
   spread at 21/63d is the eventual pick-quality verdict; week 1 starts the
   clock.
6. Every view watcher-armed within minutes; zero orphans.
7. Token cost per published view vs the old single session.
8. Zero-view days still occur gracefully — the high bar surviving the new
   machinery is itself a pass/fail test.

Budget realism: ~3.5–4.5M tokens/day at full build-out (~10–13 sessions).
Evening librarian placement protects the morning chain; if subscription caps
bite during the trial week, that is exactly the data point to collect before
optimizing.

## 9. Key existing files touched

- `advisor/prompts/daily_brief.md` → split into stage prompts (synthesis /
  red-team / publish); add accountability memory (cite prior similar calls +
  outcomes), mandatory disconfirmation line, new-data checklist (est momentum,
  insider 90d, short %float), crowding check.
- `advisor/journal.py`, `advisor/brief_check.py`, `advisor/exit_watcher.py`,
  `advisor/prompts/weekly_review.md`, `advisor/run_brief.sh` (becomes thin
  caller of orchestrator), `advisor/research/factors.py`, `advisor/research/
  nightly.py`, `advisor/research/fair_value.py` (EDGAR inputs + persisted FV
  history), `advisor/vol_check.py` (persist verdicts), `advisor/terminal.py`
  (dossier tab, calibration dashboard), `advisor/METHODOLOGY.md` (facts-vs-
  opinions guardrail, slate triage, kill-list protocol).
