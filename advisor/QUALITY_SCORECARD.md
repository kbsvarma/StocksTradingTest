# Advisor quality scorecard

The target score is evidence-based, not a marketing label. A 9.5/10 system
must meet all nine internal engineering/research gates and the separately
controlled commercial gates below.

| Gate | Weight | Passing evidence |
|---|---:|---|
| Release/runtime reliability | 1.0 | staged tests, rollback, attested release, healthy services |
| Data integrity/provenance | 1.5 | immutable panels, hashes, quarantine, source inventory, freshness |
| Research breadth | 1.0 | technical, price-factor, SEC, estimate/event and macro layers |
| Point-in-time discipline | 1.0 | filed/observed timestamps; no backfilled vendor history claims |
| Adversarial publication | 1.0 | independent red-team, fail-closed deterministic commit |
| Risk/actionability controls | 1.0 | calibrated probability + portfolio + all operational gates |
| Statistical validation | 1.5 | preregistered OOS, FDR survivors, DSR >= .95, costs and bias audit |
| Learning/monitoring | 1.0 | outcome journal, MAE/MFE, calibration, drift and incident alerts |
| Product security/operations | 1.0 | TLS, identity/RBAC, audit, secrets, SLOs, recovery exercises |

Current code intentionally refuses to self-certify statistical validation or
commercial readiness. The following cannot be completed by code alone:

- Accrue 12+ independent live-IC windows and 30+ resolved probability-scored
  recommendations; time cannot be simulated honestly.
- Contract for commercial market/fundamental data and redistribution rights.
- Complete legal/regulatory review and customer suitability/privacy design.
- Deploy behind organization-controlled TLS identity/RBAC and exercise the
  incident/recovery runbooks in the intended production environment.

Until those conditions are evidenced, the product is a strong research side
tool and interview/demo system—not a fiduciary substitute or autonomous stock
picker.
