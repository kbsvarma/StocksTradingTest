from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ib_insync import IB

# Allow running this script directly from any cwd without requiring PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import load_config
from market_data import MarketDataService
from signal_engine import SignalEngine
from trade_logger import BotLogger


def _parse_trade_date(raw: str | None, tz_name: str) -> date:
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return datetime.now(ZoneInfo(tz_name)).date()


def _has_flag(flag: str, args: list[str]) -> bool:
    return flag in args


def _value_after(flag: str, args: list[str]) -> str | None:
    if flag not in args:
        return None
    idx = args.index(flag)
    nxt = idx + 1
    if nxt >= len(args):
        return None
    return args[nxt]


def _build_logger(cfg) -> BotLogger:
    return BotLogger(
        logs_dir=cfg.logs_dir,
        signal_events_file=cfg.signal_events_file,
        order_events_file=cfg.order_events_file,
        tick_events_file=cfg.tick_events_file,
        trade_csv_file=cfg.trade_csv_file,
        daily_summary_file=cfg.daily_summary_file,
    )


def _valid_price(raw: float | None) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(val):
        return None
    return val


def _ibkr_stream_price(ib: IB, contract, sleep_seconds: float = 2.0) -> float | None:
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(sleep_seconds)
    for candidate in (ticker.marketPrice(), ticker.last, ticker.close):
        px = _valid_price(candidate)
        if px is not None:
            return px
    return None


def main() -> int:
    args = sys.argv[1:]
    cfg_path = _value_after("--config", args) or "config.yaml"
    cfg = load_config(cfg_path)
    tz = ZoneInfo(cfg.timezone)
    trade_date = _parse_trade_date(_value_after("--trade-date", args), cfg.timezone)
    preview_time = _value_after("--preview-time", args) or "10:00"
    skip_signal_preview = _has_flag("--skip-signal-preview", args)
    hh, mm = preview_time.split(":")
    preview_dt = datetime.combine(trade_date, time(int(hh), int(mm)), tz)

    logger = _build_logger(cfg)
    ib = IB()

    report: dict[str, Any] = {
        "ts": datetime.now(tz).isoformat(),
        "config_path": str(Path(cfg_path).resolve()),
        "trade_date": trade_date.isoformat(),
        "preview_dt": preview_dt.isoformat(),
        "paper_trading": cfg.paper_trading,
        "live_mode_enabled": cfg.live_mode_enabled,
        "host": cfg.host,
        "port": cfg.port,
        "client_id": cfg.client_id,
        "enabled_strategies": list(cfg.enabled_strategies),
        "dte_candidates": list(cfg.dte_candidates),
        "checks": {},
        "warnings": [],
    }

    # Use a probe client-id range so repeated checks don't fail with id collisions.
    probe_client_id = None
    last_connect_error: Exception | None = None
    for offset in range(900, 1000):
        cid = cfg.client_id + offset
        try:
            ib.connect(cfg.host, cfg.port, clientId=cid, timeout=20)
            ib.reqMarketDataType(int(cfg.market_data_type))
            probe_client_id = cid
            break
        except Exception as exc:  # noqa: BLE001
            last_connect_error = exc
            try:
                ib.disconnect()
            except Exception:  # noqa: BLE001
                pass

    if probe_client_id is None:
        report["checks"]["ib_connect"] = {
            "ok": False,
            "error": str(last_connect_error or "unable to connect with probe client IDs"),
        }
        print(json.dumps(report, indent=2, default=str))
        return 1

    report["checks"]["ib_connect"] = {"ok": True, "probe_client_id": probe_client_id}

    try:
        market = MarketDataService(ib, cfg, logger)
        signal_engine = SignalEngine(ib, cfg, market, logger)

        # Refresh macro data for a fresh morning-readiness view.
        try:
            market.macro.refresh()
        except Exception as exc:  # noqa: BLE001
            report["warnings"].append(f"Macro refresh failed: {exc}")

        # Account funds
        available_funds = None
        for item in ib.accountSummary():
            if item.tag == "AvailableFunds" and item.currency == cfg.currency:
                try:
                    available_funds = float(item.value)
                except ValueError:
                    available_funds = None
                break
        report["checks"]["available_funds"] = {"ok": available_funds is not None, "value": available_funds}

        # Open orders / positions snapshot
        try:
            ib.reqOpenOrders()
            ib.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass

        active_ids = {int(getattr(order, "orderId", 0) or 0) for order in ib.openOrders()}
        open_spx_orders = []
        for tr in ib.openTrades():
            if int(tr.order.orderId or 0) not in active_ids:
                continue
            c = tr.contract
            if getattr(c, "symbol", "") != cfg.underlying_symbol:
                continue
            open_spx_orders.append(
                {
                    "orderId": tr.order.orderId,
                    "clientId": tr.order.clientId,
                    "status": tr.orderStatus.status,
                    "secType": getattr(c, "secType", ""),
                    "action": tr.order.action,
                    "qty": tr.order.totalQuantity,
                }
            )
        report["checks"]["open_spx_orders"] = {"ok": len(open_spx_orders) == 0, "count": len(open_spx_orders), "rows": open_spx_orders}
        if open_spx_orders:
            report["warnings"].append("Open SPX orders exist; clear them before market open.")

        spx_positions = []
        for p in ib.positions():
            c = p.contract
            if getattr(c, "symbol", "") != cfg.underlying_symbol:
                continue
            if getattr(c, "secType", "") not in {"OPT", "BAG"}:
                continue
            if abs(float(p.position)) <= 0:
                continue
            spx_positions.append(
                {
                    "secType": getattr(c, "secType", ""),
                    "localSymbol": getattr(c, "localSymbol", ""),
                    "position": float(p.position),
                }
            )
        report["checks"]["open_spx_positions"] = {"ok": len(spx_positions) == 0, "count": len(spx_positions), "rows": spx_positions}
        if spx_positions:
            report["warnings"].append("Open SPX positions exist; flatten before market open.")

        # Macro filter snapshot
        blocking, event = market.macro.has_blocking_event(trade_date)
        report["checks"]["macro_filter"] = {"ok": not blocking, "blocking_event": event}
        if blocking:
            report["warnings"].append(f"Macro block for {trade_date.isoformat()}: {event}")

        # Market snapshot
        try:
            spx = market.get_spx_price()
            vix = market.get_vix_price()
            report["checks"]["market_snapshot"] = {
                "ok": True,
                "spx": spx,
                "vix": vix,
                "source": "ibkr_snapshot_primary",
            }
        except Exception as primary_exc:  # noqa: BLE001
            # Keep IBKR as primary source; if snapshot API is empty off-hours,
            # fall back to IBKR streaming close/last values for readiness checks.
            try:
                market.ensure_reference_contracts()
                spx = _ibkr_stream_price(ib, market._spx_contract)
                vix = _ibkr_stream_price(ib, market._vix_contract)
                if spx is None or vix is None:
                    raise RuntimeError("streaming fallback returned no valid SPX/VIX prices")
                report["checks"]["market_snapshot"] = {
                    "ok": True,
                    "spx": spx,
                    "vix": vix,
                    "source": "ibkr_stream_fallback",
                    "primary_error": str(primary_exc),
                }
                report["warnings"].append(
                    "Primary IBKR snapshot had no live tick; using IBKR stream close/last fallback."
                )
            except Exception as fallback_exc:  # noqa: BLE001
                report["checks"]["market_snapshot"] = {
                    "ok": False,
                    "error": str(primary_exc),
                    "fallback_error": str(fallback_exc),
                }
                report["warnings"].append("SPX/VIX snapshot unavailable.")
                spx = 0.0

        # Option chain + expiries
        try:
            chain = market.load_option_chain()
            expiries: dict[str, str | None] = {}
            for dte in cfg.dte_candidates:
                expiries[str(dte)] = market.expiry_for_dte(trade_date, int(dte))
            report["checks"]["option_chain"] = {
                "ok": bool(chain.strikes and chain.expirations),
                "strike_count": len(chain.strikes),
                "expiration_count": len(chain.expirations),
                "expiries_for_dte": expiries,
            }
            if not expiries.get("0"):
                report["warnings"].append("No 0DTE expiry found for configured trade date.")
        except Exception as exc:  # noqa: BLE001
            report["checks"]["option_chain"] = {"ok": False, "error": str(exc)}
            report["warnings"].append("Option chain unavailable.")

        # Signal preview (no order placement)
        if skip_signal_preview:
            report["checks"]["signal_preview"] = {"ok": True, "skipped": True}
        else:
            try:
                signal = signal_engine.evaluate(preview_dt, trade_taken_today=False)
                row = asdict(signal)
                candidate = signal.candidate
                row["decision"] = signal.decision.value
                if candidate:
                    row["candidate"] = {
                        "strategy": candidate.strategy.value,
                        "expiry": candidate.expiry,
                        "dte": candidate.dte,
                        "short_put_strike": candidate.short_put_strike,
                        "long_put_strike": candidate.long_put_strike,
                        "short_call_strike": candidate.short_call_strike,
                        "long_call_strike": candidate.long_call_strike,
                        "max_loss_per_contract": candidate.max_loss_per_contract,
                        "quote_mid": candidate.quote.mid if candidate.quote else None,
                        "legs": [
                            {
                                "right": leg.right,
                                "strike": leg.strike,
                                "direction": leg.direction.value,
                                "quantity": leg.quantity,
                            }
                            for leg in candidate.legs
                        ],
                    }
                report["checks"]["signal_preview"] = {"ok": True, "result": row}
            except Exception as exc:  # noqa: BLE001
                report["checks"]["signal_preview"] = {"ok": False, "error": str(exc)}
                report["warnings"].append("Signal preview crashed.")

    finally:
        try:
            ib.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # Hard readiness flag
    critical_ok = [
        report["checks"].get("ib_connect", {}).get("ok", False),
        report["checks"].get("open_spx_positions", {}).get("ok", False),
        report["checks"].get("open_spx_orders", {}).get("ok", False),
        report["checks"].get("market_snapshot", {}).get("ok", False),
        report["checks"].get("option_chain", {}).get("ok", False),
    ]
    report["ready"] = all(critical_ok)

    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
