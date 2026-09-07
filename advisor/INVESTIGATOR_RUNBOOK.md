# Investment investigator — operation

> Current connection status: the user requested removal of the investigator’s personal Claude connection. It has been removed. Source collection and deterministic analysis remain active; model-driven research/synthesis is disconnected. The model workflow described below records the implemented design, not a currently connected service. No Claude or Codex login is required for the investigator.


## Terminal

Open the Advisor terminal and enter `NVDA INT`, or choose **09 INVESTIGATE**.
Uncovered bare tickers also open the investigator. Existing covered tickers
still open their security workspace; **Investigate this security** opens the
new workflow. Enter a ticker then choose **Run investment investigation**. Merely navigating or
refreshing the page never starts paid research or provider requests.

The job runs separately from Streamlit. Its stage updates every five seconds;
**Load latest report** loads the completed result. Reports contain substantial
insights, action conditions, reproducible findings, competing hypotheses,
sector questions, complete evidence clocks and a source-coverage table. Report
history retains earlier investigations. Markdown and JSON exports are available.

**Add to Advisor research queue** saves a deterministic hypothesis episode,
retaining the report hash and original timestamp. Repeated clicks do not create
new calls. The existing worker places the hypothesis/lineage in the synthesis
queue. This does not manufacture independently verified claims or price levels.

## CLI

```bash
python -m advisor.investigator NVDA
python -m advisor.investigator NVDA --scan-only
python -m advisor.investigator NVDA --data-dir /absolute/tenant/data
```

Artifacts live under
`DATA/intelligence/investigations/TICKER/RUN_ID/`: report.json, report.md,
status.json and research.json; deep proposals are retained separately.
`latest.json` points to the newest completed artifact. Readers verify the
snapshot hash. A completed run ID cannot be overwritten. Failed jobs retain
status and can be restarted with a new run ID.

## Research model

No personal assistant account is connected. The investigator no longer invokes
`claude`, reads `CLAUDE_BIN`, or uses an interactive Claude login. The terminal
and CLI run source collection and deterministic analysis by default. The
provider-neutral reasoning interface is disconnected; a replacement has not
been selected or silently connected. Prior reports retain their original
history, including failures from the removed connection.

The pre-existing scheduled-brief pipeline is separate from this new connection.
Its model integration has not been changed by the investigator unlink action.

## Sources and configuration

No new dependency installation is required for the current local Python
runtime. Collectors use requests, pandas, yfinance and BeautifulSoup already
present in Advisor. SEC traffic is paced within the investigator process.
`SEC_USER_AGENT` can set the deployment contact identifier.

Optional credentials are `FINNHUB_API_KEY` for dated company news and
`FMP_API_KEY` for period-specific analyst estimates. Keys stay out of snapshot
URLs and error messages. Missing keys are a missing route, not fabricated data.

Issuer navigation discovers IR/news links from the profile website. Verified
NVIDIA navigation seeds are included. Additional issuer navigation URLs can be
configured without changing code:

```json
{"EXAMPLE": ["https://issuer.example/investors/results", "https://issuer.example/news"]}
```

Save this as `DATA/intelligence/issuer_sources.json`. Navigation pages are not
current evidence until dated source pages are actually retrieved. The public
HTTPS and response-size restrictions apply to configured and model-discovered
URLs alike.

Licensed observations can be supplied in
`DATA/intelligence/source_inbox/TICKER.json` as an array of source envelopes:

```json
[{
  "ticker": "EXAMPLE",
  "source": "borrow",
  "kind": "borrow",
  "payload": {"annualized_fee_pct": 12.5, "utilization_pct": 91, "provider": "licensed lender feed", "scope": "provider-specific"},
  "retrieved_at": "2026-09-04T20:10:00Z",
  "observed_at": "2026-09-04T20:00:00Z",
  "published_at": null,
  "url": "https://provider.example/data",
  "title": "Lending observation",
  "clock_quality": "explicit"
}]
```

Use a registered source ID and the correct underlying measurement/event
clocks. `fundamental` records additionally require finite value, metric, unit,
measurement period and duration_class. Unknown, stale or mismatched periods
cannot support a current claim. Imported source authority is resolved from the
catalog. Importing data does not independently verify its economic meaning.

## Verification and current local acceptance

Run `python -m pytest advisor/tests -q`. Tests cover accounting-window
reconciliation, old-quarter/future leakage, article-date contamination,
headline-only claims, unrelated evidence, source binding, same-origin
corroboration, change detection, reverse valuation, adversarial rejection,
partial-source recovery, report integrity, idempotent handoff and actual
Streamlit command/render behavior. The complete four-stage agent orchestration
is exercised with deterministic model responses and real source/citation gates.

The live NVDA source scan has been exercised against SEC, Yahoo, FRED and
issuer pages. The local Claude OAuth session was expired during the live deep
smoke test. Therefore live semantic synthesis remains an explicit acceptance
item after login is refreshed; the test suite does not stand in for that run.
See `INVESTIGATOR_METHODOLOGY.md` for source coverage and analytical design.
