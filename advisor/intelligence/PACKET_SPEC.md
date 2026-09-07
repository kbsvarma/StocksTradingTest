# Intelligence packet v1

The synthesis stage can attach an `intelligence_packet` to a view in its one
authorized draft file. The red-team verifies claims in its own artifact. After
publication attestation, the bridge exports the packet into the worker inbox.
An operator may also import a packet from the terminal or local inbox. This
creates a research draft, never an order or self-approved recommendation.

Required envelope: `ticker`, `episode`, `playbook`, `event_at`, `expires_at`,
`author`, `sources`, `claims`, `observations`, `thesis`, `plan`. Timestamps are
timezone-aware ISO strings. `decision_at` is optional for local imports and
must represent the actual decision time; replays must not enter prospective
learning. The publication bridge stamps its own decision time and author.

`playbook` is one of `earnings_continuation`, `fundamental_revision`, or
`sector_repricing`. Required observation names are registered in playbooks.py.
`observations` maps each measurement name to a unique claim_id.

Each `sources[source_id]` contains `source_id`, a credential-free HTTPS `url`,
`tier` (primary/licensed_vendor/secondary), `published_at`, `retrieved_at`,
`content` (exact supporting source text), and `facts`. Each `facts[field]`
contains numeric `value`, `unit`, and `period`. A source's `sha256` is computed
by `evidence.seal_source`; it covers metadata, text and facts. Preserve licensed
content only within its entitlements. A URL alone is not a licensed snapshot.

Each claim contains `claim_id`, `source_id`, `kind` (numeric/text), `excerpt`,
and `load_bearing` (default true). Numeric claims additionally contain `field`,
`value`, `unit`, `period`; those must match the sealed source facts. Independent
semantic review must also support the claim; numeric matching alone is not
sufficient. Reviews bind claim and source hashes, author, reviewer and time.
In the model pipeline, only the separate red-team artifact can supply the
verification; synthesis-supplied reviews are discarded by the bridge.

`thesis` contains meaningful strings for `what_changed`, `consensus`, `variant`,
`mechanism`, `why_not_priced`, `catalyst`, `invalidation`, `contrary_evidence`.
These are distinct questions, not copies of a factor score. Supply evidence
for the economic mechanism and distinguish market-pricing inference from
measured analyst consensus.

For a proposed long, `plan` contains `entry_low`, `entry_high`, `stop`, `target`,
`horizon_sessions` (1–126), `entry_condition`, `invalidation`. Optional
`trigger_kind: price_zone` means the reviewed plan needs only the price-zone
condition. Otherwise activation also requires its explicit `confirmation_id`
on an event. A quote crossing cannot silently satisfy an additional catalyst.
Plan geometry must satisfy 0 < stop < entry_low <= entry_high < target.

The worker binds a packet to its hash and stable episode identity. A changed
packet creates a new revision and requires renewed review. It never inherits
an old approval. Sample gates, instrument support and portfolio context are
not relaxed to fill an empty call book.

An entry plan also requires `cost: {round_trip_bps, source, as_of}` with a
finite, nonnegative modeled round-trip estimate and a current source timestamp.
An entry slippage number alone is not a complete round-trip cost model. Missing
costs block review eligibility instead of turning gross payoffs into net claims.
Optional `allocation.weight_pct` and `drivers` feed verified-portfolio exposure
diagnostics; they do not authorize a personalized allocation.
