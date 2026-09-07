# Advisor intelligence terminal runbook

## Start and navigate

Run from the repository with its supported Python environment:

```bash
python -m streamlit run advisor/terminal.py --server.address 127.0.0.1 --server.port 8516
```

The new command desk is the default terminal. Type a covered ticker, `TICKER
DES` / `TICKER GP` for its price workspace, `TICKER EVID` for evidence, or
`DESK`, `CALLS`, `EVENTS`, `PERF`, `LAB`, `OPS`. Enter submits commands; native
Tab and radio navigation remain available. Workspaces render lazily. Refresh
re-reads data; it neither generates a brief nor sends orders.

Research Lab contains the opportunity screener, an analyst thesis builder,
complete packet import, scenario sensitivity and the existing research tools.
The thesis builder saves a candidate with visible missing-evidence blockers.
It does not invent estimates or turn the hypothesis into an approved call.
Save workspace persists the current security and watchlist for the signed-in
identity. Customer tenants cannot open the legacy operator's portfolio tools.

`ADVISOR_DATA_DIR` selects the local model-book data root. All new chart reads
are local, verify available panel hashes, retain adjusted-EOD labeling, and
never trigger provider traffic on page render. Missing or stale data is an
explicit state, including in the market tape and performance panels.

## Call workflow

1. Discovery and analyst hypotheses nominate a case.
2. An expectations-gap packet supplies the registered playbook's measurements,
   exact source snapshots, units, periods, thesis and plan. See
   [PACKET_SPEC.md](intelligence/PACKET_SPEC.md).
3. The synthesis pipeline can attach `intelligence_packet` to a draft view.
   Independent red-team output supplies `intelligence_claim_checks` after
   fetching and verifying the cited material. Synthesis-supplied reviews are
   discarded by the bridge.
4. Only an attested final brief can export a packet into the inbox. Final
   amended price geometry overrides the original draft. Legacy journal IDs
   are retained so the UI does not duplicate an imported brief projection.
5. The worker underwrites the packet and persists one immutable episode and
   its revisions. A complete packet becomes review-required, not approved.
6. A separately authorized reviewer inspects the packet and approves/rejects
   with a reason. Evidence freshness and playbook requirements are recomputed
   at review time. Authors, submitters and last editors cannot approve their
   own work, including when they change the displayed author name.
7. A live quote in the entry zone activates a model-book observation only
   when the reviewed trigger is satisfied. Additional confirmation conditions
   cannot be inferred from a price crossing. No broker fill is assumed.
8. Material dependency events return the call to review-required. Active risk
   crossings remain observable during review. Price targets/stops observed
   before activation withdraw the case; after activation they resolve an
   observed model-book outcome at the observed price, including adverse gaps.
9. Expiry is processed independently of entry. Revisions do not erase history
   or create new independent samples. Backdated new packets cannot enter the
   prospective history.

The engine supports long equity/ETF entry plans and research intent for
watch/hold/reduce/exit/avoid. It does not model an options contract through
equity sizing. The existing execution configuration remains disabled. New
calls remain research ideas; software regression success and analyst scenario
weights are not proof of profitable or calibrated recommendations.

## Worker and scheduling

```bash
python -m advisor.intelligence.worker --data-dir /absolute/path/to/advisor/data
```

This command reads `intelligence/inbox/*.json`, processes filing/quote events,
expires calls and writes `worker_status.json` plus a bounded
`research_queue.json`. It performs no network fetches or message sends.
Nightly research and the attested brief pipeline now invoke the worker. The
morning context includes the analyst/reassessment queue for synthesis.

Quote and filing subscription inventories include tracked new-engine calls.
The filing poller runs between 06:00 and 22:00 ET on weekdays, retaining its
20-minute interval; this is explicitly a filing intake window, not market
hours or a real-time delivery guarantee. New filing alerts trigger worker
reassessment. Guidance, estimate and macro events can use the same typed event
interface; adding a licensed provider adapter requires its own provenance and
latency validation.

Optional one-minute scheduler definitions are included under `ops/systemd/`
and `ops/com.stockstest.advisor-intelligence.plist`. The launchd template uses
`__REPO__`, which must be replaced with the absolute deployment checkout.
The systemd units use the existing `%h/stockstest` convention. Installation and
enablement belong to deployment; merely adding these files does not start a
background service. Single-host SQLite transactions serialize concurrent
writers and stale revisions fail with a conflict rather than overwriting.

## Identity and tenant configuration

Local mode defaults to the actual operating-system user with analyst rights.
It cannot grant itself independent review through a UI role selector. An
existing `ADVISOR_PORTAL_TOKEN` remains honored for local access.

For enterprise sign-in, install Streamlit's authentication extra for the
deployment's supported version, configure the identity provider in
`.streamlit/secrets.toml`, set `ADVISOR_AUTH_MODE=oidc`, and provision
`ADVISOR_ACCESS_POLICY` with an absolute path. The code uses the verified
`st.user` session and requires a verified-email claim. It does not trust
identity headers or URL parameters. Provider claim mapping must be validated
for the selected IdP. See [Streamlit's official OIDC documentation](https://docs.streamlit.io/develop/concepts/connections/authentication).

Example policy shape (illustrative identities and paths):

```json
{
  "members": {
    "analyst@example.com": {"tenant": "team-a", "role": "analyst"},
    "reviewer@example.com": {"tenant": "team-a", "role": "reviewer"}
  },
  "tenants": {"team-a": {"data_root": "/srv/advisor/team-a"}},
  "local_accounts": {
    "reviewer-os-account": {"tenant": "model", "role": "reviewer"},
    "operator-os-account": {"tenant": "model", "role": "admin"}
  }
}
```

Each tenant must have an explicit, dedicated data root for quotes, dossiers,
portfolio context and legacy artifacts. SQLite call queries additionally
filter by tenant. This is application isolation, not database row-level
security or proof that a multi-host deployment has passed penetration testing.
The local review CLI resolves the actual OS account through `local_accounts`;
it has no command-line role override.

```bash
python -m advisor.intelligence.cli --data-dir /absolute/data review \
  --call-id CALL_ID --revision 2 --verdict approve --reason "Reviewed source and plan"
python -m advisor.intelligence.cli --data-dir /absolute/data export
python -m advisor.intelligence.cli --data-dir /absolute/data backup --output /secure/backup/calls.sqlite
```

## Read-only API

```bash
python -m advisor.intelligence.api --policy /secure/api-policy.json --port 8517
```

Default binding is loopback. API policy has `tokens` entries containing
`sha256` of the bearer token, `user`, and `tenant`, plus the same explicit
`tenants` map shown above. Provision secrets outside source control. Requests
use `Authorization: Bearer ...`; query-token authentication is rejected.

Routes: `GET /health`, `GET /v1/snapshot`, `GET /v1/calls`,
`GET /v1/calls/{call_id}/history`. Research routes require authentication.
There are no order or write routes. Responses use no-store caching and tenant
scope. Configure TLS termination, network controls, rate limits, and commercial
data entitlements before external distribution; the local listener is not a
public production API deployment.

## Evidence, performance and portfolio context

Exact snapshot binding is reported separately from independent semantic
verification. Numeric claims match value, unit and period; their source and
review must have been available at decision time. Review freshness can expire.
The numeric/source importer still needs auditing: a hash proves identity, not
that an extraction or source statement is true.

The scenario engine computes weighted net payoffs and adverse sensitivity
from explicit analyst assumptions. Negative modeled payoffs block the case
when a scenario packet is provided. Forecast diagnostics use a time split,
training-period base-rate comparator, separate model/playbook/instrument/
horizon/event cohorts and nonoverlapping holdout windows. They never approve
a model merely because it has 30 rows.

Performance views separate quote-observed outcomes from modeled fills and
actual executions. Costs must be sourced and dated. Same-window benchmarks
and issue-date cluster uncertainty are required for comparative claims; the
code does not compound overlapping calls into an invented portfolio curve.
Existing suggestion entry-policy simulations remain available in the legacy
track-record workspace with their original limitations and provenance.

Optional `intelligence/portfolio.json` contains `status: verified`, `as_of`,
holdings with `ticker`, `sector`, `weight_pct`, and `drivers`, plus mandate
limits `max_name_pct`, `max_sector_pct`, `max_gross_pct`. The call's proposed
`allocation.weight_pct` is checked against name/sector/gross exposure. Missing
or stale context produces model-book-only output. Stops are not guaranteed
maximum losses and allocation checks never grant execution authority.

## Integrity, recovery and acceptance

The call store appends revisions and audit records in one transaction with
WAL and FULL synchronous durability. Update/delete triggers protect history
against application mistakes. Exports verify the audit hash chain and its
binding to each revision. Retain checkpoints outside the host; an administrator
with filesystem access can replace the entire database and its hashes.

Backups use SQLite's consistent backup API and refuse to overwrite an existing
destination. Restore to a separate path first, open read-only, export and
compare the checkpoint before changing the live data root. Restoration and
tenant isolation are exercised in the regression suite.

Regression command: `python -m pytest advisor/tests -q`. The implementation
also has real Streamlit execution tests for every workspace, browser checks
for navigation and chart/scenario rendering, and failure cases for identity
spoofing, stale evidence, future consensus, event duplication, review-time risk
crossings, backdated issuance and same-day expiry. See the implementation log
for the verified count and local runtime.

External acceptance still requires current licensed feeds, a provisioned and
tested identity provider, deployment/restore drills on the target host, and
enough prospective outcome evidence for the intended investment claims. No
current-call quality or commercial readiness is inferred from stale local
artifacts. The implementation does not modify the existing execution opt-ins.

## On-demand stock investigation (September 6 expansion)

`TICKER INT` and **09 INVESTIGATE** now open an arbitrary stock investigation,
including securities outside the candidate slate. The new source/temporal and
reasoning architecture is documented in `INVESTIGATOR_METHODOLOGY.md`; start,
source credentials, model login, report history and queue handoff are documented
in `INVESTIGATOR_RUNBOOK.md`. The legacy call book's data age is separate from
an investigation's own evidence clocks.
