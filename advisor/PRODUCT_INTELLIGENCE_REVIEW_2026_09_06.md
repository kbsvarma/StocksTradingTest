# Advisor: from research terminal to accountable calls

Review date: September 6, 2026. Scope: current local working tree, including uncommitted September 4 improvements, existing design documents, saved artifacts, and all Advisor tests. No production deployment, broker interaction, fresh market-data ingestion, or investment recommendation was performed. This document proposes product changes; it does not change runtime policy.

## Verdict

Advisor has a useful research and publication foundation. Its strongest capabilities are deterministic screening, independent adversarial review, source/freshness controls, immutable suggestion revisions, publication integrity, and conservative outcome simulation. Preserve these.

The missing product is an accountable decision engine: detect a material change, explain what the market is missing, choose an expression and entry condition, maintain the call as evidence changes, and measure the result against credible alternatives. More indicators, longer briefs, and more confident language will not deliver that.

The current enterprise roadmap explicitly makes verified research its promise and treats investment performance as secondary. That is a legitimate research product, but it is insufficient for the requested calls product. A calls product must demonstrate decision value, including economic outcomes after modeled costs and the cost of missed opportunities. It can do this without becoming an automated execution system.

Recommended initial product: an analyst copilot for a professional discretionary investor, covering liquid US equities and sector ETFs over several sessions to several weeks. Start with a small set of explicit playbooks and a model call book. Treat portfolio-specific sizing as a separate layer requiring verified customer context. Expand instruments and horizons after each has a supported contract and evaluation method.

## Evidence and priority findings

### 1. The candidate and analyst lanes need a common call contract

`suggestion_policy.py:99` assesses research priority but always returns `actionable: False`. `research/underwriting.py:24` imports a locally reviewed thesis, source URLs, and plan; `research/candidate_provenance.py:49` binds it to the candidate evidence. The daily suggestion lane does not automatically inherit the synthesis/red-team workflow's completed underwriting. Meanwhile, the brief has its own decision keys, probabilities, risk checks, and journal.

This distinction is correctly documented, but leaves the user with ranked suggestions and separately published views rather than one coherent decision lifecycle. Do not solve it by relabeling priority research as actionable.

Build one versioned Call object and explicit adapters from both lanes. Candidate generation nominates; underwriting builds the case; review approves or rejects; deterministic publication issues a call. An automated underwriting worker can prepare cases, with the existing independent review preserved. A reviewer name and HTTPS URL are provenance metadata, not evidence that the source supports a claim.

Acceptance: follow one candidate through evidence, approval, issuance, revision, entry observation, invalidation, and resolution using a single episode ID. Repeated appearances must not inflate the record. Existing publication attestations and historical IDs remain reproducible through migration.

### 2. Intelligence needs an explicit expectations gap

The synthesis prompt asks for a catalyst and why-now thesis, which is a good start. The valuation module remains a heuristic: analyst target, forward EPS multiplied by a generic growth multiple, and a cash-flow-yield anchor. Those estimates do not establish a differentiated forecast or the timing of repricing (`research/fair_value.py:45`).

Require every proposed call to answer:

- What changed, and when did that information become available?
- What does consensus expect, and what is plausibly embedded in the price? These are separate quantities.
- What do we believe differently, and what source or calculation supports the difference?
- What economic mechanism moves revenue, margins, earnings, cash flow, or valuation?
- What event could close the gap within the call horizon?
- Why has the market not already incorporated this evidence?
- What observable fact would invalidate the thesis?

Implement sector-aware operating models for covered names: for example volume × price, backlog conversion, margin sensitivity, share count, and valuation sensitivities. Preserve reported values, estimates, assumptions, and inferred market expectations as separate fields. Missing consensus or weak pricing inference should be visible, never filled with an LLM guess.

The useful LLM work is extracting evidence, comparing versions, generating falsifiable hypotheses, and finding counterarguments. Deterministic code should calculate scenario outputs, costs, exposure, and performance.

### 3. Start with three testable playbooks

Proposed scope, not claims of established edge:

| Playbook | Evidence needed | Decision produced | Failure condition |
|---|---|---|---|
| Post-earnings continuation | Report/guidance versus pre-event consensus, estimate changes, price/volume response, sector response | Enter after a specified continuation or pullback trigger | Operating evidence reverses or the defined price/time condition fails |
| Fundamental revision and catalyst | Dated operating model revisions, valuation sensitivity, catalyst window | Accumulate within an underwritten entry range | The metric driving the revision deteriorates or catalyst expires |
| Sector repricing | Identified macro shock, sector earnings sensitivity, cross-asset and relative-price evidence | Prefer a named sector expression or wait for confirmation | The causal driver reverses or apparent discrepancy is explained |

Each playbook needs a version, supported universe, required data, explicit entry/exit rules, costs, horizon, evidence standard, and comparator. Existing momentum, insider, activist, and technical generators can nominate candidates into these playbooks. Adding a generator should require showing incremental value over those already present.

Evaluate playbooks before combining their scores. Combining many selected signals can amplify backtest selection bias; this is an established concern in [Novy-Marx's multiple-signal backtesting research](https://www.nber.org/papers/w21329).

### 4. Repair the probability and payoff contract

`brief_check.py:339` restricts p_win to 0.50–0.85. `brief_check.py:179` requires reward/risk ≥2 and expected value ≥0.15R. With those constraints, the formula p×RR−(1−p) is already at least 0.50R. The expectancy threshold therefore adds no independent economic discrimination once the other constraints pass.

This contract also excludes potentially useful lower-hit-rate, larger-payoff strategies. More fundamentally, a binary full-target/full-stop formula does not represent time exits, partial moves, gaps, or options decay.

`research/calibration.py:96` gates actionability on at least 30 explicit observations, Brier below 0.25, and matching approval. Approval is a real additional control. However, this code does not fit a probability calibration mapping or bind eligibility to a specific playbook, forecast model, instrument, and horizon. A pooled Brier threshold does not establish that a new call's stated probability is calibrated. An imbalanced outcome base rate can also make a historical-rate predictor better than the 50% baseline.

Define the prediction event precisely: probability of positive net return by horizon, target-before-stop, and catalyst occurrence are different predictions. Use cohort-specific evaluation, a training-period base-rate comparator, held-out probability calibration, uncertainty intervals, and version-bound approvals. Pool sparse cohorts only through an explicit validated method. Keep analyst scenario weights labeled as judgment.

Replace the binary expectancy field with payoff scenarios including time expiry and adverse gaps. Report costs and sensitivity to assumption error. Sample floors remain useful minimums, but 30 observations alone are not proof of stable decision quality. Do not loosen current gates until the replacement contract and its validation are ready.

### 5. Trade expression is internally inconsistent

`prompts/redteam.md` requires puts or put spreads for shorts; synthesis also supports options. Yet `brief_check.py:221` accepts only equity/ETF sizing and computes capital as quantity × underlying entry midpoint. That is not an options contract. A short case cannot coherently satisfy the intended prompt policy and validator without resolving this mismatch.

The default suggestion plan is also a 21-session horizon with a 1.5 ATR stop and 3 ATR target (`research/suggestion_templates.py:9`). Reviewed plans can override it, so it is no longer true that all ideas must use the template. The remaining gap is generating and validating thesis-specific plans systematically.

For the first release, support long equity/ETF entry, hold, trim/exit, avoid, and conditional-watch decisions. Bearish analysis can justify avoiding or reducing an existing position. Add options only with a dedicated schema for expiry, strikes, legs, executable quotes, spread, liquidity, premium, payoff, and time/volatility sensitivity. Stock borrowing needs its own availability and cost evidence. Rank supported expressions by scenario outcomes and feasibility rather than defaulting to the most dramatic payoff.

### 6. Turn events into maintained intelligence

`events_poller.py:1` polls filings for held/watched names every 20 minutes during regular hours and sends an alert. It does not determine materiality or revise the thesis. Its market-hours helper also uses weekdays and clock time rather than the exchange calendar. After-hours information waits for a later allowed poll.

Add an event-to-thesis dependency map. A new filing, earnings release, estimate revision, or price trigger should identify affected claims, retrieve the changed evidence, recalculate dependent scenarios, and propose maintain/upgrade/downgrade/withdraw. Material changes receive independent review and a new immutable revision. Use supported feed timestamps and measured latency; do not promise real-time intelligence from a delayed polling feed.

Alerts should say “guidance changed; the margin assumption behind this call no longer holds; call withdrawn,” not merely “new filing.” Market-sensitive calls awaiting reassessment should visibly enter review-required state.

### 7. Strengthen claim verification beyond numeric matching

`research/fabrication_audit.py:195` compares recognized model-written numbers with frozen context. It explicitly notes that evidence URLs are counted but not resolved by that audit. The red-team prompt separately instructs source fetching; that is useful, but the numeric audit is not comprehensive source-entailment verification.

Store source snapshots or licensed references, publication/retrieval times, document versions, excerpts, units, periods, and transformation lineage for each load-bearing claim. Check that the source supports the specific assertion, not just that a URL exists. Track claim coverage and unsupported assertions alongside detected contradictions. A low contradiction rate over a small checked subset must not look like universal factual accuracy.

### 8. Make performance about following the call

The September 4 implementation already adds immutable episodes, entry-band simulation, gap handling, ambiguity labels, opportunity-set archives, and nonoverlapping comparisons. Reuse this work. `research/ranking_evaluation.py` correctly calls its output gross ranking diagnostics, and `research/entry_simulation.py` distinguishes modeled entries from actual fills.

Unify evaluation across suggestions and brief calls. Keep separate records for signal returns, published-policy simulations, and actual user executions if provided. Report activation rate, net simulated expectancy, payoff distribution, drawdown, adverse/favorable excursion, holding time, benchmark-relative return, and uncertainty by playbook/version. Include no-entry cases and unresolved cases in coverage reporting.

Use identical-window sector/market baselines, a simple registered strategy, the original candidate population, and an ablation without each proposed intelligence component. Evaluate rejected ideas under the same contemporaneous policy and costs; do not advertise isolated avoided losers while ignoring rejected winners. Measure red-team contribution rather than targeting a 20–60% kill/amend rate, as current prompts suggest.

Outcome resolution should be mechanically timely even when the narrative postmortem is late. Track unclosed and unscorable episodes as product failures, not silently omit them.

## The user-facing product

The first screen should answer “What changed, what should I do, and what needs my attention?” Put three groups first: new/changed calls, active calls needing attention, and conditional opportunities. Put generators, model diagnostics, and service logs in drill-down views. Preserve a clear distinction between a valid no-call decision and a data or pipeline failure.

Every call card should contain action, current trigger state, horizon, thesis, expectations gap, sources, entry constraint, scenario exits, invalidation, contrary evidence, next review trigger, and versioned performance context. Portfolio users additionally see incremental exposure, common macro drivers, and any mandate conflict.

Illustrative interface content only — fictional instrument and numbers, not a recommendation:

> **EXAMPLE — conditional long; waiting for entry**  
> Buy only within 98–100 after the specified post-event confirmation; otherwise wait. Horizon: 15 sessions.  
> Why now: verified guidance revision changes the margin scenario; the pre-event consensus snapshot has not yet incorporated it.  
> Scenario target: 108; price invalidation: 95; expiry: a stated session date. Gap losses can exceed the stop distance.  
> Thesis invalidation: management withdraws the margin assumption. Contrary evidence: demand remains weak in a named segment.  
> Next action: reassess after the estimate refresh. Probability: unavailable pending cohort validation. Sources and calculation assumptions expand below.

The finished product can issue direct calls when the applicable evidence and product authorization support them. It should also be decisive about hold, reduce, exit, avoid, and wait. Call count is not a quality metric.

## Enterprise delivery

The terminal currently has an optional shared query-token gate (`terminal.py:52`), rather than enterprise identity. Add SSO, role/tenant isolation, customer mandates, versioned approvals and overrides, durable storage, restore tests, secrets management, and API/webhook delivery around the call lifecycle. Keep customer holdings separate from a shared model book and isolate access to source content according to entitlement.

Prioritize data by playbook: reliable prices/corporate actions/calendar, then historical consensus and revisions, then transcripts and detailed operating evidence. Procurement should verify timestamps, historical revisions, coverage, service guarantees, and display/derived-data rights. For example, [LSEG describes I/B/E/S broker estimates](https://www.lseg.com/en/data-catalogue/company-data/ibes-estimates/broker-estimates) as providing analyst/consensus and guidance context; suitability and licensing still need contractual verification.

For a commercial launch, retain the existing data-rights and advisory-business review workstream. Product wording alone does not settle the operating model. Performance presentation should distinguish modeled from actual results; the [SEC marketing-rule guide](https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/investment-adviser-marketing) describes conditions relevant to hypothetical performance for advisers within its scope.

## Implementation sequence and acceptance

These are work packages, not promises that investment edge can be proved on a calendar.

1. **Unify the decision contract.** Reconcile supported instruments, actions, forecast events, IDs, model-book versus customer context, and revision states. Adapt both existing lanes. Acceptance: end-to-end replay reproduces the displayed call and outcome without policy bypasses.
2. **Build one complete playbook.** Start with post-earnings continuation and an expectations-gap packet. Automate retrieval, calculations, underwriting draft, review, and trigger registration. Acceptance: a fixed benchmark set of historical cases reconstructs only information available at decision time; prospective issues capture all evidence and non-qualifications.
3. **Maintain calls on events.** Add materiality routing and dependency-based reassessment. Acceptance: replayed critical events produce the right review/withdrawal transitions, with measured latency and delivery deduplication.
4. **Prove incremental decision value.** Freeze a challenger version, compare against registered baselines with cost-aware matched outcomes and clustered uncertainty, and publish both successes and failures. Acceptance: a written promotion decision with enough independent evidence for the actual claim being made. Insufficient evidence means continue observation, not declare success.
5. **Harden enterprise distribution.** Pilot role-based teams, entitlement-aware data, APIs, customer context, recovery, and audit export. Acceptance: tenant-isolation tests, restore drill, reproducible call history, and complete operational/business launch review. Data procurement and identity design can begin earlier in parallel with these work packages.

The first engineering investment should be the shared call contract plus one fully underwritten playbook. Keep the existing scoring and publication infrastructure, but make its output a decision with maintained evidence and an honest record.

## Verification and limits

- Executed `/opt/anaconda3/bin/python -m pytest advisor/tests -q`: **395 passed in 3.62 seconds**. Pytest emitted an existing asyncio fixture-scope deprecation warning.
- Inspected current source, including uncommitted work; the earlier September 4 review describes pre-fix findings and was not treated as the current implementation.
- Local `calibration_latest.json` is dated July 16 and reports zero scored resolutions. The local journal contains 273 rows, including four original view rows and 135 rejection rows; its latest timestamp is August 3. These are artifact observations, not claims about the deployed system or current strategy performance.
- Local `candidates_latest.json` is dated September 3. `picks_latest.json`, `research/suggestion_releases`, and `research/underwriting` are absent in this checkout. The new suggestion workflow is tested in code but its live operation and outcome quality were not demonstrated by these local artifacts.
- Passing tests establishes regression behavior, not investment profitability. No runtime files, prompts, execution settings, or existing work were changed for this review.
