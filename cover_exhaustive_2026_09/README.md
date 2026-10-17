# Exhaustive constant-rebalanced stock pairs

Start with REPORT.md and the two reader-friendly CSVs:
- LIST_1_ALL_STOCKS.csv
- LIST_2_EX_AI_MAG7.csv

All data acquisition was free. No Databento purchase or live trade occurred.

## Reproduce the frozen numerical results offline

Python runtime used: /opt/anaconda3/bin/python. Dependencies: numpy, pandas, pyarrow, scipy, numba, matplotlib, tabulate. Sources and matrix are already saved; no new data download is needed for the core exhaustive search.

Run individually from the repository root:

    /opt/anaconda3/bin/python cover_exhaustive_2026_09/verify_engine.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/search.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/rank.py

The original protocol and exclusion hashes are checked before ranking. The data-quality/tradability amendment is explicitly later than the initial raw ranking. Recomputing the screen and detailed outputs additionally uses the recorded local and supplemental source files:

    /opt/anaconda3/bin/python cover_exhaustive_2026_09/audit_prices.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/exclusions_review.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/tradability.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/shortlist_details.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/family_bootstrap.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/exposure.py
    /opt/anaconda3/bin/python cover_exhaustive_2026_09/report.py

The family bootstrap uses four Numba threads; BLAS threading can be limited to avoid oversubscription. Core search took approximately 40 seconds on this machine, after the much longer inventory/data-repair stage. About 2.93 million combinations were evaluated; no search stopped because attractive pairs were found.

The inventory and supplemental retrieval scripts are provenance/build tools, not required for numerical replay. Running them again changes the input-data snapshot and may make network requests. Keep original snapshots/hashes when comparing results.
