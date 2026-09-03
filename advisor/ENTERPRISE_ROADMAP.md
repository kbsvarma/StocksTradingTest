# Enterprise roadmap — an intelligence agent, not an execution bot

Written 2026-09-03, re-scoped after the product intent was clarified: this is
and always was an **intelligence agent** — it produces research, ideas and
kills for a human to act on. It is not an OMS and will not become one.

Companion to `COMMERCIAL_READINESS.md` (legal gates) and
`INTELLIGENCE_PLAN.md` (intelligence design).

## What "enterprise grade" means once execution is out of scope

Dropping execution removes the largest engineering block (OMS, TCA, portfolio
risk engine, corporate actions, multi-broker failover — 3–6 months of work)
**and** it lowers the evidence bar in a specific, defensible way:

- A **fund** must prove risk-adjusted return after costs. That is the deflated
  Sharpe / FDR gate the factor model currently fails (DSR 0.746 vs 0.95).
- An **intelligence product** must prove something different and far more
  attainable: *its claims are true, sourced, non-fabricated, and its stated
  confidence is calibrated.*

So the factor model sitting at `research_only / discovery_only` is **not a
blocker** for this product — it is the correct label for a candidate
generator. It nominates names; the research loops and the red-team decide.
That distinction is already encoded in the code and should stay.

**Restated product promise:** *"Every claim is dated, sourced and independently
re-verified; ideas that fail the bar are shown with the reason; stated
confidence is measured against outcomes."* Nothing in that promise requires a
proven Sharpe ratio.

---

## TIER 0 — Trustworthy output (the whole product)

For an LLM-driven research agent the existential risk is **fabrication**, not
drawdown. One invented earnings number destroys the product.

Existing strengths: evidence URLs with retrieval timestamps, an independent
red-team stage that refetches sources, deterministic merge, language
validation, exact-file write permissions so no model stage can mutate the
journal.

Gaps to close:

1. **Claim-level provenance, enforced.** Every quantitative assertion in a
   published brief should carry a machine-checkable link to either (a) a
   fetched source with timestamp or (b) a deterministic computation from a
   named artifact + build id. Anything unattributed should fail publication,
   not merely warn.
2. **Automated fabrication audit.** Sample published claims daily, re-fetch,
   and score agreement. Track a **fabrication rate** as a headline metric —
   this is the number a buyer actually cares about.
3. **Numeric provenance.** Distinguish model-typed numbers from
   pipeline-computed numbers in the artifact schema; render them differently
   in the UI. A number the model typed should never look like a measured one.
4. **Source quality tiering.** Filing/exchange/official > vendor > press >
   aggregator, with the tier shown next to each claim.
5. **Contradiction detection** across a brief and against the prior day.

## TIER 1 — Calibration & research benchmarking

The honest measurement layer already exists (Brier gates at n≥15/30,
by-generator attribution, reject counterfactuals). What it lacks is a
**baseline**: good relative to what?

1. **Benchmark the ideas** against naive comparators — sector ETF, equal-weight
   slate, analyst consensus, and "do nothing".
2. **Benchmark the kills.** Attribution already shows the kill list carries
   signal (a killed SNDK short avoided a +21% run-up). For a research product
   "what not to touch" is a legitimate, uncrowded deliverable — measure and
   sell it explicitly.
3. **Coverage/recall metrics** — of names that moved materially, how many did
   the slate surface beforehand? Recall is a research KPI; P&L is not.
4. Keep the sample gates exactly as they are. They are the credibility.

## TIER 2 — Coverage, freshness, licensing

1. **Licensed data** — yfinance is not commercially licensable and is the
   backbone. This remains blocker #1 for any paid product.
2. **Deeper primary sources** — filings full text, earnings-call transcripts,
   8-K/Form-4 streams (EDGAR plumbing already exists and is genuinely PIT).
3. **Survivorship-free history** for any published study.
4. **Breadth** — US equities today; international/ETF/credit as demand dictates.
5. Vendor redundancy behind the existing `source_health` layer.

## TIER 3 — Platform (multi-tenant intelligence service)

Today: one user, file state, one box, LAN shared token, plaintext secrets.

1. Postgres for journal/watchlist/evidence with per-tenant isolation.
2. OIDC/SSO + RBAC (viewer / analyst / approver / admin).
3. Secrets vault with rotation; TLS everywhere.
4. Immutable, tamper-evident audit of every published claim.
5. HA + tested restore drills.
6. **Per-user context** — the agent should know each subscriber's holdings,
   constraints and risk profile. Today there is one hardcoded book.
7. **Delivery surfaces** — API, webhooks, scheduled digests, per-user
   watchlists. Today: one brief and one dashboard.

## TIER 4 — Observability & SRE

Metrics (stage latency, data freshness, fabrication rate, publication
success), tracing across the stage DAG, SLOs ("brief published by 09:45 on 99%
of trading days"), escalation routing, runbooks, and extension of the existing
failure-injection tests to data/provider outages.

## TIER 5 — Governance & regulatory

1. Model inventory/versioning, challengers, drift thresholds, automatic
   demotion.
2. **Segregation of duties** — today one operator can author *and* approve.
3. Backtest↔live parity harness.
4. **Regulatory reality:** publishing security-specific buy/sell ideas to
   others can constitute investment advice regardless of the "research"
   label. Counsel review governs whether this ships as *research/education*
   (publisher's exemption territory) or as a regulated advisory service. This
   is a product-defining decision, not a footnote.

---

## Sequenced plan (intelligence-agent scope)

| Phase | Focus | Outcome |
|---|---|---|
| 0–1 mo | Claim-level provenance enforcement; fabrication-rate metric; SLOs | The trust claim becomes measurable |
| 1–3 mo | Baselines + coverage/recall; kill-list product; licensed data for one asset class | Research quality is benchmarked, not asserted |
| 3–6 mo | Postgres + auth + secrets + per-user context; API/digest delivery | Multi-tenant service |
| 6–9 mo | Governance (challengers, drift, segregation of duties); counsel review | Institutionally defensible |
| ongoing | Live-IC clock keeps accruing in the background | If the factor edge ever clears, it is a bonus, not the premise |

## What NOT to do

- Do not rebuild toward execution. It was never the product.
- Do not loosen the publication gates to make a demo look better — the gates
  are the product.
- Do not claim proven alpha. The agent's value is disciplined, verifiable
  research, and that claim survives scrutiny; a Sharpe claim currently would
  not.
- Do not let the factor model's `discovery_only` label read as a failure. For
  a candidate generator it is the accurate and appropriate status.
