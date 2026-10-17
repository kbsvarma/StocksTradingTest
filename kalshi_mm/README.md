# Kalshi Market-Making Bot (LIP harvest)

Quotes two-sided limit orders on Kalshi to capture the **Liquidity Incentive
Program** subsidy (resting orders near the touch earn a share of per-market daily
pools, $10–$1,000/day each), plus the Volume Incentive Program cashback
($0.005/contract). Both programs end **Sept 1, 2026** — this is a time-boxed edge.

This is *market making*, not directional betting: we get **paid a subsidy to post
liquidity**. The real risk is **inventory / adverse selection** — we get filled
when the market moves against us. The whole job of the risk layer is to not give
the subsidy back in inventory losses.

## Status

| Component | File | State |
|---|---|---|
| Signed REST client (auth + read + orders) | `kalshi_client.py` | ✅ built |
| Read-only selftest | `selftest.py` | ✅ built — **run this first** |
| Live market scanner | `scanner.py` | ✅ built |
| Quote / inventory / risk engine | `quoter.py` | ⏳ next |
| Main quoting loop | `main.py` | ⏳ next |

## Setup

1. Generate an API key pair in Kalshi → Settings → API. Download the private key
   PEM. **Never paste the private key into chat or commit it.**

2. Create `~/.kalshi_env` (mode 600), mirroring `~/.webull_env`:

   ```
   KALSHI_KEY_ID=your-api-key-id
   KALSHI_PRIVATE_KEY_PATH=~/.kalshi_private_key.pem
   ```

   ```sh
   chmod 600 ~/.kalshi_env ~/.kalshi_private_key.pem
   ```

3. Install deps (already present in the anaconda env, but for a clean venv):

   ```sh
   pip install -r kalshi_mm/requirements.txt
   ```

## Run

```sh
cd kalshi_mm

# 1. Prove your key works (READ ONLY, no orders):
python selftest.py

# 2. Scan for target markets (public data; run during US market hours):
python scanner.py --top 40
```

## Safety model

- `config.yaml: safety.dry_run` starts **true**. Every non-GET request (orders,
  cancels) is suppressed and logged until explicitly armed. The full
  read→decide→quote loop is proven in dry_run before a real order is ever sent.
- Hard caps enforced in code: per-market position, total exposure, daily loss,
  max open orders.
- Kill switch: `touch kalshi_mm/state/HALT` stops quoting immediately.
- `post_only: true` — we are always a maker; never cross the spread, never pay
  taker fees.

## Open question being chased

Which markets have an **active LIP reward period** is not in the public market
payload (only `liquidity_dollars`). That marker likely lives behind the
authenticated account view — `scanner.investigate_reward_markers()` will pin it
down now that we can sign requests. Until confirmed, target selection leans on
spread + liquidity.
