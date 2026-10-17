# Dashboard accounting display fixes, September 28

Trading code and accepted baseline snapshot unchanged. Updated only deployed dashboard_panel.py; rollback copies in production archive/.

- Explicit WIN/LOSS outcome and latest settled result.
- Read complete SQLite ledger with mode=ro for a consistent summary and latest-100 table snapshot.
- Sum settled net P&L with Decimal and display ROUND_HALF_UP, fixing 6.675 displayed as 6.67; now 6.68.
- Show lifetime win/loss/unfilled counts and exact four-decimal net. Missing ledger produces an error, not invented zero totals.

Verified with Streamlit AppTest against live data and actual deployed dashboard entrypoint: +$6.68, 0 open/pending, 106 settled (103W/3L), 107 attempts (1 unfilled), exact +$6.6750. ETH Sep28 01:45 loss of $1.8485 is included. Checked negative halfway rounding and aggregation of >100 records. These figures are a dated snapshot, not fixed expected future totals.
