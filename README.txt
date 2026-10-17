StocksTradingTest — working source and research checkpoint

This repository contains the trading/Advisor source, tests, research scripts,
and reviewed configuration templates. Dated folders preserve separate
experiments; they are not automatically promoted to live designs.

Start with deployments/2026_10_16/README.txt for the captured deployed Kalshi,
Trend, and Advisor versions. kalshi_stable_baseline_2026_09_28/BASELINE.md is
the accepted stable Kalshi return point; keep that checkpoint unchanged.
The latest dashboard change is in kalshi_ui_chart_2026_10_16/candidate/.
The isolated NFL weekend implementation is in kalshi_nfl_live_2026_10_16/.

Security and local configuration
- Never add credentials, private keys, secret environment files, broker account
  identifiers, ledgers, runtime state, raw data or downloaded web assets.
- Older Databento download scripts now read DATABENTO_API_KEY from the
  environment instead of embedding a credential. Do not place that key here.
- Broker diagnostic scripts/dashboard access read WEBULL_ACCOUNT_ID from the
  environment. Webull YAML configuration files are private/local; copy the
  corresponding .yaml.example and set the account identifier only locally.
  Existing local configurations were retained when removed from tracking.
- .gitignore excludes private/runtime/generated files. The private MEMORY.md
  remains local; AGENTS.md still directs agents to read it on that workstation.
- .gitleaks.toml extends the standard Gitleaks rules and adds a direct
  Databento-client credential rule. Its two exact-line exceptions are
  non-secret dictionary field/scenario names.
- Validate the complete staged tree with tools/check_staged_secrets.py before
  committing. Install Gitleaks separately or set GITLEAKS_BIN to its executable.

Raw research datasets, operational receipts, account snapshots, repeated
backup trees, and screenshots remain on their existing storage and are not
part of this source commit. Research scripts may require those separately
stored inputs and licensed data feeds. A source backup is not evidence that
every historical experiment is production-ready or has a validated edge.
