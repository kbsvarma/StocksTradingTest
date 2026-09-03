# RETIRED — DO NOT EXECUTE

This legacy single-session prompt is intentionally non-operational.

Advisor briefs may only be produced by `advisor/orchestrator.py` through the
ordered macro → synthesis → red-team → publish pipeline. Each stage must
complete successfully, and deterministic validation must pass, before an
artifact can be published. There is no model-only fallback.

If any stage fails, times out, exhausts provider capacity, or produces an
invalid artifact:

1. mark the pipeline failed with a machine-readable reason;
2. preserve the last validated brief without modifying it;
3. publish no recommendation, alert, journal entry, or proposal; and
4. surface the failure to the operator.

Never use this file to generate a brief. Never bypass the orchestrator,
`brief_check`, calibration/actionability gates, portfolio verification, or
red-team verdicts.
