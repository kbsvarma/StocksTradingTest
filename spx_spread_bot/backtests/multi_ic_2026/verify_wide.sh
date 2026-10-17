#!/bin/bash
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/multi_ic_2026
exec >> verify_wide.log 2>&1

echo "=== VERIFY-WIDE START $(date) ==="
while pgrep -f "pull_multi.py.*--out-subdir (iwm_wide|jnj_wide)" > /dev/null; do sleep 60; done
echo "[verify] both pulls done at $(date)"

PY=/opt/anaconda3/envs/llms/bin/python

echo ""
echo "=== COSTS + FILE COUNTS ==="
for T in IWM JNJ; do
    SUB=$(echo $T | tr A-Z a-z)_wide
    files=$(ls data/$SUB/*.parquet 2>/dev/null | wc -l | tr -d ' ')
    done_line=$(grep "\[$T done\]" pull_${T}_wide.log 2>/dev/null | tail -1)
    printf "  %-4s files=%s  %s\n" "$T" "$files" "$done_line"
done

echo ""
echo "=== DATE RANGE per ticker ==="
for SUB in iwm_wide jnj_wide; do
    first=$(ls data/$SUB/*.parquet 2>/dev/null | head -1 | xargs basename 2>/dev/null)
    last=$(ls data/$SUB/*.parquet 2>/dev/null | tail -1 | xargs basename 2>/dev/null)
    echo "  $SUB: $first → $last"
done

echo ""
echo "=== 0DTE-by-DOW per ticker (sample first 100 files each) ==="
$PY <<'EOF'
import pandas as pd, re
from pathlib import Path
from datetime import date
from collections import defaultdict

SYM_RE = re.compile(r'^([A-Z]+)\s+(\d{6})([PC])(\d{8})$')

for sub in ["iwm_wide", "jnj_wide"]:
    base = Path(f"data/{sub}")
    files = sorted(base.glob("*.parquet"))
    if not files:
        print(f"\n{sub.upper()}: NO FILES"); continue
    dow_0dte = defaultdict(lambda: {'has_0dte': 0, 'total': 0})
    sample_files = files[::max(1, len(files)//100)][:100]
    for f in sample_files:
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
    print(f"\n{sub.upper()} (sample of {len(sample_files)} days):")
    for dow in ['Mon','Tue','Wed','Thu','Fri']:
        s = dow_0dte[dow]
        pct = s['has_0dte']/s['total']*100 if s['total']>0 else 0
        print(f"  {dow}: {s['has_0dte']}/{s['total']} ({pct:.0f}%)")
EOF

echo ""
echo "=== Sample symbol per ticker ==="
$PY <<'EOF'
import pandas as pd
from pathlib import Path
for sub in ["iwm_wide", "jnj_wide"]:
    files = sorted(Path(f"data/{sub}").glob("*.parquet"))
    if not files: continue
    f = files[len(files)//2]
    df = pd.read_parquet(f)
    print(f"\n[{sub}] {f.name}: shape={df.shape} symbols={df['symbol'].nunique()}")
    print(f"  sample = {list(df['symbol'].unique()[:3])}")
EOF

echo ""
echo "=== VERIFY-WIDE COMPLETE $(date) ==="
