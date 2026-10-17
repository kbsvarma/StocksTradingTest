#!/bin/bash
# Wait for all 7 multi-ticker pulls to finish, then verify each.
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/multi_ic_2026
exec >> verify.log 2>&1

echo "=== VERIFY START $(date) ==="
echo "[verify] waiting for all pull_multi.py processes to exit..."
while pgrep -f "pull_multi.py" > /dev/null; do sleep 30; done
echo "[verify] all pulls done at $(date)"

PY=/opt/anaconda3/envs/llms/bin/python

echo ""
echo "=== TOTAL COST & FILE COUNTS ==="
for T in NVDA META AAPL MSFT GOOGL AMZN TSLA; do
    files=$(ls data/$(echo $T | tr A-Z a-z)/*.parquet 2>/dev/null | wc -l | tr -d ' ')
    done_line=$(grep "\[$T done\]" pull_$T.log 2>/dev/null | tail -1)
    printf "  %-6s files=%-3s %s\n" "$T" "$files" "$done_line"
done

echo ""
echo "=== PER-TICKER EXPIRY DOW STRUCTURE ==="
$PY <<'EOF'
import pandas as pd, re
from pathlib import Path
from datetime import date
from collections import defaultdict

base = Path('data')
SYM_RE = re.compile(r'^([A-Z]+)\s+(\d{6})([PC])(\d{8})$')

for tdir in sorted(base.iterdir()):
    if not tdir.is_dir(): continue
    ticker = tdir.name.upper()
    files = sorted(tdir.glob('*.parquet'))
    if not files:
        print(f'{ticker}: NO FILES'); continue
    expiry_dows = defaultdict(int)
    for f in files[:30]:  # sample first 30 days
        try:
            df = pd.read_parquet(f)
            for s in df['symbol'].unique():
                m = SYM_RE.match(s.strip())
                if m:
                    yymmdd = m.group(2)
                    exp = date(2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
                    expiry_dows[exp.strftime('%a')] += 1
        except Exception as e:
            print(f'{ticker} {f.name}: {e}')
    print(f'{ticker} ({len(files)} files): expiry-DOWs seen = {dict(sorted(expiry_dows.items(), key=lambda kv: -kv[1]))}')
EOF

echo ""
echo "=== SAMPLE ROW per ticker ==="
$PY <<'EOF'
import pandas as pd
from pathlib import Path
for tdir in sorted(Path('data').iterdir()):
    if not tdir.is_dir(): continue
    ticker = tdir.name.upper()
    files = sorted(tdir.glob('*.parquet'))
    if not files: continue
    f = files[len(files)//2]
    df = pd.read_parquet(f)
    print(f'\n[{ticker}] {f.name}: shape={df.shape} symbols={df["symbol"].nunique()}')
    print(f'  symbols[:3] = {list(df["symbol"].unique()[:3])}')
EOF

echo ""
echo "=== VERIFY COMPLETE $(date) ==="
