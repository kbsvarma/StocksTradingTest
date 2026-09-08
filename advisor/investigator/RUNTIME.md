# Application research runtime

Investigate launches a full background investigation: timestamped provider evidence, up to four adaptive web-research rounds, source fetching and excerpt verification, synthesis, then adversarial review. A reviewed synthesis drives the decision brief. Expired premises invalidate that synthesis when viewed. Plain navigation never launches a job.

The application uses the OpenAI Responses API independently of personal Claude or Codex logins. Configure `OPENAI_API_KEY` in the service owner's `~/.advisor_research.env` (permissions 600), outside this repository. Optional `ADVISOR_RESEARCH_MODEL` defaults to `gpt-6-astra`; `ADVISOR_RESEARCH_REASONING` defaults to `high`. The terminal service loads this optional file; CLI calls also read it. `ADVISOR_RESEARCH_ENV` can select another configuration path. Never commit credentials.

Without a credential, full research explicitly fails before collection. Saved reports remain accessible. `--scan-only` is an explicit diagnostic evidence scan, not a model investigation.

Requests use structured output, a 12,000 output-token cap and at most 12 web-tool calls per research round. Research requests time out after 150 seconds; synthesis and review after 180 seconds. These are request limits, not a guaranteed dollar ceiling. Token and tool usage comes from the provider response. There are no automatic retries after an uncertain API timeout. Search history distinguishes provider-observed queries from model-reported queries.

Validation must distinguish automated transport fixtures from live provider testing. A passing mocked API test does not establish model quality or successful live connectivity. Before declaring the engine operational, complete real investigations and inspect their sources, dates, reasoning and rendered reports.

## Gemini

`GEMINI_API_KEY` selects Gemini automatically when no provider is explicitly chosen. Set `ADVISOR_RESEARCH_PROVIDER=gemini` or `openai` to choose explicitly. The Gemini default is `gemini-3.8-flash`. No SDK installation is required. Existing public-source collectors supply evidence; Gemini plans bounded Bing RSS queries and Advisor fetches the pages directly. Google's paid search-grounding tool is never requested. No automatic paid-provider fallback is used, and this code does not enable billing. The project's Google billing tier determines API charges and quotas; an API key alone does not expose that billing status.

Each Gemini research round uses two model requests (search planning and extraction), at most four queries and ten public source pages. Synthesis and adversarial review are separate requests. All extracted sources still pass the existing body-excerpt and temporal checks. Search failures and unreadable pages remain explicit evidence gaps. CLI defaults to full investigation; `--scan-only` must be supplied explicitly for a diagnostic scan.

### Markdown report workflow

`ADVISOR_RESEARCH_WORKFLOW=concise` uses dated issuer documents converted to Markdown during collection. Retrieval reserves topic budgets and preserves the latest release's headline/segment summary. A separate model request retains the draft's evidence and adds accounting/risk sections. Document and section citations must resolve to supplied evidence. A failed review leaves an explicitly labelled draft, never a claim of completed verification. Review output and evidence are retained in the run's model trace.

Requests share a per-model file queue under `advisor/data/intelligence/model-pacing/` (inside the terminal service’s permitted writable directory), spaced 65 seconds apart, with one retry for HTTP 429/500/502/503 and bounded request time. This reduces collisions; it does not override daily provider quotas. The current UI distinguishes a new job's failure from an older saved report. Legacy computed valuation ratios are excluded from the Markdown model packet to prevent blending newer earnings releases with older companyfacts periods.

These operations use existing runtime dependencies and the existing private research configuration; no experiment-directory packages, interactive logins, or ticker-specific scripts are required on a migrated host. Deploy with the standard Advisor deployment script and provision the private credential through the existing migration procedure.
