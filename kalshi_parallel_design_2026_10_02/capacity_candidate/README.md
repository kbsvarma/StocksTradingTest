# Capacity proposal, rebased on active Signal95 and execution retries

Review-only candidate, not deployed or activated. Five contracts per entry, max20 contracts/$19 reserved exposure, and the existing $10 daily budget. Signals, execution cap93.9c, retries and ledger semantics are unchanged. No activation script or authenticated client is included.

Keeping the daily budget at $10 initially allows at most two full five-contract positions ($9.50 reserved), despite the higher portfolio ceiling. A third needs sufficient realized profit that day. This is intentional and differs from the unaccepted $25 daily-budget option. Current production is still max8/$7.60/$10.

This copy matches the current production engine except for the cap message; sizing changes only MAX_CONTRACTS and MAX_RISK. The expected production hashes are recorded. A later live release must recheck them, reconcile state, preserve the ledger, and perform current preflight. Tests use an in-memory fake exchange only.
