# Application research runtime

Investigate launches a full background investigation: timestamped provider evidence, up to four adaptive web-research rounds, source fetching and excerpt verification, synthesis, then adversarial review. A reviewed synthesis drives the decision brief. Expired premises invalidate that synthesis when viewed. Plain navigation never launches a job.

The application uses the OpenAI Responses API independently of personal Claude or Codex logins. Configure `OPENAI_API_KEY` in the service owner's `~/.advisor_research.env` (permissions 600), outside this repository. Optional `ADVISOR_RESEARCH_MODEL` defaults to `gpt-6-astra`; `ADVISOR_RESEARCH_REASONING` defaults to `high`. The terminal service loads this optional file; CLI calls also read it. `ADVISOR_RESEARCH_ENV` can select another configuration path. Never commit credentials.

Without a credential, full research explicitly fails before collection. Saved reports remain accessible. `--scan-only` is an explicit diagnostic evidence scan, not a model investigation.

Requests use structured output, a 12,000 output-token cap and at most 12 web-tool calls per research round. Research requests time out after 150 seconds; synthesis and review after 180 seconds. These are request limits, not a guaranteed dollar ceiling. Token and tool usage comes from the provider response. There are no automatic retries after an uncertain API timeout. Search history distinguishes provider-observed queries from model-reported queries.

Validation must distinguish automated transport fixtures from live provider testing. A passing mocked API test does not establish model quality or successful live connectivity. Before declaring the engine operational, complete real investigations and inspect their sources, dates, reasoning and rendered reports.
