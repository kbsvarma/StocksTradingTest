#!/bin/bash
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/multi_ic_2026
exec >> verify_2020.log 2>&1

echo "=== VERIFY-2020 START $(date) ==="
while pgrep -f "ticker IWM.*2020-01-02" > /dev/null; do sleep 60; done
echo "[verify] pull done at $(date)"

PY=/opt/anaconda3/envs/llms/bin/python

echo ""
echo "=== COST + TOTAL FILES ==="
grep "\[IWM done\]" pull_IWM_2020_2023.log 2>/dev/null | tail -1
ls data/iwm_wide/*.parquet 2>/dev/null | wc -l | xargs echo "total iwm_wide files:"

echo ""
echo "=== FILES PER YEAR ==="
for yr in 2020 2021 2022 2023 2024 2025 2026; do
    n=$(ls data/iwm_wide/iwm_cbbo_${yr}*.parquet 2>/dev/null | wc -l | tr -d ' ')
    printf "  %s: %s files\n" "$yr" "$n"
done

echo ""
echo "=== 0DTE-BY-DOW per year (Fri only expected in 2020-2022; daily M-F from 2023+) ==="
$PY <<'EOF'
import pandas as pd, re
from pathlib import Path
from datetime import date
from collections import defaultdict

SYM_RE = re.compile(r'^([A-Z]+)\s+(\d{6})([PC])(\d{8})$')

base = Path("data/iwm_wide")
for yr in [2020, 2021, 2022, 2023]:
    files = sorted(base.glob(f"iwm_cbbo_{yr}*.parquet"))
    if not files:
        print(f"\n{yr}: NO FILES"); continue
    dow_0dte = defaultdict(lambda: {'has_0dte': 0, 'total': 0})
    sample = files[::max(1, len(files)//60)][:60]
    for f in sample:
        ds = f.stem.split('_')[-1]
        td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        df = pd.read_parquet(f)
        expiries = set()
        for s in df['symbol'].unique():
            m = SYM_RE.match(s.strip())
            if m:
                yymmdd = m.group(2)
                exp = date(2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
                expiries.add(exp)
        has_0dte = td in expiries
        dow_0dte[td.strftime('%a')]['total'] += 1
        if has_0dte:
            dow_0dte[td.strftime('%a')]['has_0dte'] += 1
    print(f"\n{yr} (sample of {len(sample)}):")
    for dow in ['Mon','Tue','Wed','Thu','Fri']:
        s = dow_0dte[dow]
        pct = s['has_0dte']/s['total']*100 if s['total']>0 else 0
        print(f"  {dow}: {s['has_0dte']}/{s['total']} ({pct:.0f}%)")
EOF

echo ""
echo "=== VERIFY-2020 COMPLETE $(date) ==="
