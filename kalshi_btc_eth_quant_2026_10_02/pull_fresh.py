"""Public GET-only fresh full UTC day. Never imports an order client."""
from pathlib import Path
import sys
import concurrent.futures as cf

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'kalshi_parallel_design_2026_10_02'))
import pull

pull.ROOT = ROOT / 'fresh_data'
pull.ROOT.mkdir(exist_ok=True)
pull.START = pull.ts('2026-10-02T00:00:00Z')
pull.END = pull.ts('2026-10-03T00:00:00Z')
pull.ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']

if __name__ == '__main__':
    # Two public-request workers, including baseline assets for portfolio comparison.
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fn, asset) for fn in [pull.kalshi, pull.coinbase] for asset in pull.ASSETS]
        for future in cf.as_completed(futures):
            future.result()
