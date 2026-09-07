# Advisor live UI acceptance — 2026-09-06

Live service: existing Advisor at `192.168.3.36:8505`.
Release: `20260907T015230Z-22b445766f4d-05bbca82df88`.

## Delivered

- Compact, colorful terminal navigation with responsive search and controls.
- Company-name and typo resolution: Nividia, Nvidia, NVDA and NVDIA resolve to NVIDIA / NVDA. Other names use provider search; ambiguous listings require a selection, unknown companies cannot launch a report.
- Readable evidence-based decision brief: support, contradictions, economic implications, next tests, valuation sensitivities, dated reference-price checkpoints and explicit source gaps. No JSON dumps in the main report.
- Current evidence is reassessed at viewing time. No source-count buy score, fabricated price target or implied model review.
- Completed source investigations display automatically.

## Verification

- 507 Advisor tests passed locally and on the Linux release stage before promotion.
- Live Nividia search resolved to NVIDIA / NVDA.
- Live NVIDIA report checked at widths 320, 390, 768, 1024, 1440, 1920 and 2560 pixels: no document overflow or clipped header, search, navigation, verdict or finding panels. Research cards stacked below the breakpoint and returned to side-by-side layout above it.
- Remaining eight workspaces checked at 390 and 1440 pixels: no document overflow or Streamlit exceptions.
- Phone and desktop screenshots visually inspected. Native radio indicators removed using the deployed Streamlit control structure.
- Fresh live NVIDIA run completed at 2026-09-07T01:56:52 UTC with 312 records and seven computed findings; its timestamp and findings appeared automatically without manual reload.
- Existing runtime data and logs preserved by the release script; deployment service-health and content-hash checks passed.

Chrome interaction was interrupted by concurrent use, so final responsive acceptance used the existing hidden test tab against the same live service. No second server or separate product was started.

## Scope of intelligence

The visible decision brief is evidence-linked rules analysis. Model synthesis and independent model challenge remain disconnected at the user's request. A fresh source scan is not represented as a completed model-reviewed investment recommendation.

## Security charts, alignment and watchlist follow-up

Final live release: `20260907T024456Z-22b445766f4d-fb65f97a9918`.
512 tests passed on the server before promotion; release health/hash checks passed.

- Measured search input/button outer boxes: both 44px tall, identical top and bottom coordinates. Command input, Go, Refresh and Save likewise match exactly.
- Loaded 1,255 daily sessions for NVDA, AAPL, MSFT, GOOGL, SPY and QQQ. No provider errors in the initial market snapshot loads.
- Verified candlestick/line modes, one-month range with appropriate price/volume scaling, SPY comparison rebased to the selected range, and mobile zoom. Moving averages use full preceding history when viewing shorter windows.
- Investigate and Watchlist passed width checks at 320, 390, 768, 1024, 1440, 1920 and 2560 pixels with no document overflow or clipped market/watchlist panels. Mobile chart screenshot visually inspected after zoom.
- Watchlist action buttons measured at an identical baseline despite different summary lengths. Nividia resolved to NVDA on addition without creating a duplicate. After a fresh page session the saved list remained NVDA, AAPL, MSFT and GOOGL.
- Seeded current research summaries: NVDA seven findings; AAPL six; MSFT three; GOOGL eight. These remain calculated research assessments, not independently model-reviewed trade recommendations.
- Tests cover preserving good market history after refresh failures, comparison normalization, short-range moving averages, and per-user/per-tenant watchlist persistence including deliberately empty lists.


## Original terminal styling restored
Release: 20260907T031103Z-22b445766f4d-032828d4397f. Restored original black/amber/green/red styling across current workspaces. Fixed Streamlit negative custom-HTML paragraph margins. Remote suite: 512 passed. Live desktop desk, NVDA five-year chart, and four-stock watchlist visually inspected. Search and command controls measured identical 44px heights and aligned edges. Mobile 390px viewport had no document overflow. Hidden QA tab closed and viewport restored.
