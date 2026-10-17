Deployed bot source snapshot — 2026-10-16

This is a read-only capture of the deployed Kalshi live engine and dashboard,
Trend bot, and Advisor research console. source_manifest.json records hashes
and sizes of each captured file. Snapshot code is preserved byte-for-byte.
The Kalshi snapshot includes WebSocket transport, execution fixes and the
Realized performance Line/Bars/date-range controls.

Folders:
  kalshi_live/           Live Kalshi engine and dashboard panel
  kalshi_dashboard_host/ Streamlit host wrapper and configuration
  trend_bot/             Deployed Trend source and tests
  advisor/               Deployed Advisor source and configuration

These are reference copies, not an installation or automatic activation.
The server's existing service configuration and secret environment remain
separate. No credentials, private keys, account ledgers, arming files, HALT
files, environments, or live state were captured. No bots were restarted
or changed to create this snapshot. Preserve current ledgers and reconcile
account state before any future restoration; never restore historical
arming state. The September 28 stable checkpoint is separately preserved
at kalshi_stable_baseline_2026_09_28/.

The root advisor/ directory remains the development checkout. trend_bot/
and kalshi_unbroken_bot/ at the repository root are separate repositories;
their Git histories are not embedded here. This snapshot preserves deployed
versions without altering those repositories or pushing to their remotes.
