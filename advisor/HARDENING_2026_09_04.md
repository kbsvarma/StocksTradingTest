# Advisor hardening — 2026-09-04

Scope: existing research assistant, not order execution. Unrelated worktrees and
runtime data are not modified. Passing tests is not evidence of investment alpha.

## Implementation passes

1. **Deployment:** route both entry points through staging/tests/rollback; refuse
   remote code drift during staging; verify activated content digest. Never
   overwrite a running pipeline during the copy step.
2. **Concurrency:** acquire the single-flight lock before publishing status;
   rejected duplicate requests cannot erase the active run's state.
3. **Provider availability:** stop downstream stages after macro quota/auth failure;
   parse explicit reset timestamps and refuse UI retries before provider reset.
4. **Historical integrity:** historical picks cannot use current learned priors
   or today's confidence mapping. Calibration checks the actual scoring version.
5. **Probability integrity:** exclude replay rows from live calibration; prevent
   in-sample empirical buckets and legacy usable flags becoming probabilities.
   Independent time-held-out probability validation remains required.
6. **Benchmark validity:** remove the invalid selected-pick resampling control;
   compute SPY and stop-policy comparisons on paired rows only. Persist full
   issue-time eligible candidate sets for a future genuine ranking control.
7. **Outcome integrity:** align OHLC by date and reject incomplete windows;
   benchmark from the pick reference bar to its actual exit bar.
8. **Numerical safety:** reject infinite prices/ATR and nonpositive generated
   levels; return a controlled error for empty price panels.
9. **Cost transparency:** distinguish unavailable cost estimates from verified
   modelled costs, reject malformed estimates, show unknown viability in the UI.
10. **Model promotion and regression:** old fixed-weight validation cannot promote
    the adaptive live scorer. Add targeted regressions and run the full suite in
    isolated staging before activation. Guard missing performance display values.

## Remaining acceptance requirements (not waived)

Additional follow-through: live generator learning now excludes replay/old-policy
outcomes and refuses legacy priors without provenance. New generator-warning
HTML is escaped. The candidate suite reached 340 passing tests before these
additional checks; final release verification is recorded in the task report.

- Point-in-time replay of the entire adaptive scoring policy, not fixed weights.
- Time-held-out, independent live probability calibration; only one v2 live
  resolved observation was present at review time.
- A true full-opportunity-set ranking comparator; new immutable archives supply
  the prospective inputs, but future outcomes cannot be manufactured today.
- Execution-realism review including gaps, fills, borrowing costs and corporate
  actions; current daily-bar outcomes are research simulations, not fills.
- Contracted provider capacity and licensed redistribution rights.
- Commercial authentication/TLS, tenant isolation, legal/compliance review and
  representative operational soak/load testing.

No unconditional 9.5/10 or production-investment reliability claim is made.
