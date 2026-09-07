# On-demand investment investigator

> Current connection status: the user requested removal of the investigator’s personal Claude connection. It has been removed. Source collection and deterministic analysis remain active; model-driven research/synthesis is disconnected. The model workflow described below records the implemented design, not a currently connected service. No Claude or Codex login is required for the investigator.


Implemented September 6, 2026. The product starts with a question about a
security, collects dated evidence, calculates reproducible signals, pursues
high-consequence uncertainties, and produces an investment judgment with a
counter-thesis and falsifiers. It does not equate news retrieval with insight.

## Intelligence coverage

The executable catalog (`investigator/catalog.py`) covers 43 source routes
across 18 dimensions. It distinguishes automatic collectors, authenticated
provider adapters, adaptive web research and imported licensed evidence.
Registration is not a claim that every route supplied data in a particular run.
Every report contains its actual collection results, failed sources and gaps.

The principal source families are original company disclosures; management
remarks and Q&A; estimates and revisions; price/volume; short positioning and
borrow; options; ownership; news and social narratives; customer, competitor
and supplier disclosures; regulatory/legal/trial records; macro/credit;
industry statistics; contracts; fund flows; and alternative operating data.

No source is universally best. SEC statements are strong for reported
accounting; issuer releases can precede filings; customers can challenge an
issuer's demand narrative; regulators establish actual rule status; market
quotes establish price; borrowing records establish lender conditions. A
report must explain which source answers its particular question.

Automatic collection includes SEC submissions, comparable XBRL periods and
filing/exhibit text, Yahoo price/profile/estimates/recommendations/calendar/
news/options, a matched SPY history, FRED observations, and issuer IR/release
navigation. Finnhub news and FMP estimates run when their API keys are present.
Licensed borrow, options-flow and alternative observations use explicit source
envelopes. Deep research can seek the remaining routes through web search and
source reads, then verifies returned excerpts against fetched page bodies.

## The temporal contract

Each observation retains:

- Publication/acceptance time: when this statement became public.
- Retrieval time: when this investigator actually possessed it.
- Measurement start/end: the fiscal quarter, settlement date or measured window.
- Observation time: the market/estimate snapshot or data observation date.
- Event time: the economic event when independently identifiable.
- Clock quality, source URL, source family, evidence ID and original payload.

Date-only publication is conservatively represented at the end of that UTC
date; unknown publication is never replaced with retrieval time. Related-story
widgets and page modification times cannot set a story's publication clock.
Future knowledge is excluded. Stale or unverified-period data is context-only.
Source time eligibility is checked again when publishing and viewing reports.

Current fundamentals select the latest measurement period, then its latest
available filing; a newly filed old quarter cannot displace it. Prior-year
comparisons require matching duration and units. Cash conversion requires the
same income/cash-flow window. Trailing cash flow uses fiscal year + current YTD
− corresponding prior YTD, with contiguous fiscal boundaries and matching
units. Missing pieces produce no fabricated TTM result.

The report distinguishes current observations from historical comparisons.
Historical facts can support an explicitly labeled comparison, but cannot be
the sole current premise. A repeated article about an old event is not a fresh
catalyst. Provider-estimated future dates are not issuer-confirmed events.

## Reproducible analytical layer

Implemented calculations include comparable-quarter revenue/profit/EPS/share
changes, margins, receivables/inventory divergence, matched cash conversion,
FCF and SBC-adjusted FCF proxies, EPS revision magnitude and breadth, 20/50/200
session averages, RSI, ATR, drawdown, volatility, extreme daily moves, volume
and liquidity, and same-window market-relative strength.

Options diagnostics retain snapshot limitations: dated expiry, volume ratio,
OI, indicative ATM IV and a bounded two-sided straddle estimate when valid.
They do not infer dealer gamma, opening direction or informed buying from
volume/OI. Short interest ages from settlement; a new download does not make
an old position report current. Short-sale volume is never substituted for
outstanding short interest.

Reverse valuation computes the cash-flow growth needed to reconcile a dated
share-count/current-reference-price equity value proxy with period-reconciled
annual cash flow. It displays discount-rate sensitivities and assumptions,
not an invented target price. Corporate actions after the share-count date
block the proxy. Banks, insurers and REITs require sector-specific valuation;
the generic reverse FCF calculation is suppressed for them.

## Competing hypotheses and research priority

Twelve hypothesis families are explicitly explored: quality compounding,
expectations reset, oversold recovery, trend continuation, short squeeze,
crowded long, neglected turnaround, lottery speculation, accounting
deterioration, financing stress, second-order industry effects and catalyst
repricing. Each has required evidence dimensions and a question that could
confirm or falsify the mechanism.

An RSI extreme is context, not a directional thesis. Heavy shorting requires
borrow, catalyst and price evidence before becoming a squeeze case. Strong
reported growth alongside falling estimates is a conflict, not a bullish
headline. Receivables outrunning sales is a diligence flag, not proof of fraud.
High volatility can identify speculative risk, not prove irrational pricing.

Research priority is ordinal: consequence/materiality, contradictions and
missing decision-changing premises. It is not an additive buy score or a
probability. Multiple filings from one issuer and multiple vendors relaying
consensus are not counted as independent confirmations. Syndicated news is
clustered; its copies cannot manufacture signal breadth.

Sector lenses cover semiconductors, software, banks, biopharma, energy,
consumer, industrials and real estate. For semiconductors, the investigator
specifically asks about customer capex versus funded purchases, supplier/HBM/
packaging capacity, export restrictions, product transitions, customer
concentration, receivables, financing and ecosystem investments. Related-company
SEC collection follows source-verified relationships discovered in research.

## Agent loop and report

1. Collect structured and original-source evidence; retain failed-route status.
2. Calculate signals and create a prioritized hypothesis/sector question queue.
3. Research both bullish and bearish explanations, read source bodies and
   verify source publication dates and exact excerpts.
4. Follow important sourced customer/supplier/competitor relationships; run a
   second targeted research round against unresolved questions and contradictions.
5. Synthesize substantial insights: what changed, economic mechanism, what is
   priced in, strongest counterargument, horizon and invalidation.
6. Run a fresh adversarial model pass against the proposal and source evidence.
7. Enforce deterministic citation, excerpt and clock checks; reject unsupported
   insights and suppress any action that depends on rejected reasoning.
8. Publish an immutable report, source snapshot, calculation set, research trail,
   rejected claims and explicit unresolved questions.

The research action can be a buy candidate, wait-for-trigger, avoid-new-entry,
reduce candidate or no edge found. Conditions and invalidation accompany the
judgment. Model challenge is not human independent review and does not approve
an executable call. A user can add the report to Advisor's research queue;
its hash and original as-of time are retained, and measured underwriting is
required separately.

Web-search queries are captured from actual tool-use events, not trusted from
the model's claimed search list. Source access is bounded to public HTTPS with
redirect checks and response limits. The model runtime has only WebSearch/
WebFetch during research and no tools during synthesis/review: no shell, files,
MCP services, trading tools or subagents. Per-run model limits are four calls at
up to $2.50 each; the worker has a 20-minute job budget. The report records this
stopping condition, rather than asserting an impossible universal exhaustion
of the internet. Source-family coverage is broad; individual evidence gaps
remain explicit and can drive the next investigation.

## Methodological references

- SEC's [EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  supports company submissions and XBRL collection; period reconciliation is
  performed in the investigator rather than delegated to a latest-value query.
- FINRA explains the distinction between [short interest and short-sale volume](https://www.finra.org/investors/insights/short-interest).
- [ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html) provides the
  vintage dimension needed for historical macro information sets.
- Research on [investor inattention and earnings announcements](https://www.nber.org/papers/w11683)
  motivates testing attention and underreaction as hypotheses, not assuming
  every earnings beat drifts upward.
- Research on [short-sale constraints](https://www.nber.org/papers/w8494)
  motivates examining overpricing and borrowing frictions alongside squeeze
  narratives; high short interest is not an automatically bullish signal.
- [Which News Moves Stock Prices?](https://www.nber.org/system/files/working_papers/w18725/w18725.pdf)
  motivates event and issuer relevance beyond headline polarity.
- [Claude CLI documentation](https://code.claude.com/docs/en/cli-reference)
  documents the structured-output and tool restrictions used by the runtime.

These references motivate specific questions and controls. They do not certify
that thresholds chosen for research triage are universal investment rules.
