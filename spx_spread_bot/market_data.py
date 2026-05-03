from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import requests
from ib_insync import IB, Contract, Index, Option, Ticker

from config_loader import BotConfig
from models import LegDirection, OptionLegSpec, SpreadQuote, TickSnapshot
from trade_logger import BotLogger


@dataclass(slots=True)
class OptionChainSnapshot:
    expirations: set[str]
    strikes: list[float]


class MacroCalendar:
    """Macro event calendar with resilient fallbacks.

    Order of precedence:
    1) Manual CSV file (always supported, no external dependency).
    2) FMP economic calendar API if macro_api_key is configured.
    3) FOMC dates from Federal Reserve site.

    When sources are unavailable, behavior follows cfg.macro_fail_open.
    """

    def __init__(self, cfg: BotConfig, logger: BotLogger):
        self.cfg = cfg
        self.logger = logger
        self.cache_path = Path(cfg.data_dir) / "macro_calendar_cache.json"
        self._cache: dict[str, list[str]] = {}
        self.last_refresh_ok: bool = False
        self.last_refresh_error: str = ""
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.last_refresh_ok = bool(self._cache)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"macro calendar cache load failed: {exc}")
            self.last_refresh_ok = False
            self.last_refresh_error = str(exc)

    def _save_cache(self) -> None:
        self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def refresh(self) -> None:
        merged: dict[str, set[str]] = {}

        for event_date, event in self._load_manual_csv():
            merged.setdefault(event_date, set()).add(event)

        for event_date, event in self._load_fomc_dates():
            merged.setdefault(event_date, set()).add(event)

        for event_date, event in self._load_fmp_calendar():
            merged.setdefault(event_date, set()).add(event)

        self._cache = {k: sorted(v) for k, v in merged.items()}
        self._save_cache()
        self.last_refresh_ok = bool(self._cache)
        if not self.last_refresh_ok:
            self.last_refresh_error = "no macro rows loaded from any source"
            self.logger.warning("macro calendar refresh completed but loaded zero dates")
        else:
            self.last_refresh_error = ""
            self.logger.info(f"macro calendar refreshed with {len(self._cache)} dates")

    def events_for_date(self, current_date: date) -> list[str]:
        if not self._cache and not self.cfg.macro_fail_open and not self.last_refresh_ok:
            return ["CALENDAR_UNAVAILABLE"]
        return list(self._cache.get(current_date.isoformat(), []))

    def has_blocking_event(self, current_date: date) -> tuple[bool, str]:
        events = self.events_for_date(current_date)
        if not events:
            return False, ""
        if "CALENDAR_UNAVAILABLE" in events:
            return True, "CALENDAR_UNAVAILABLE"
        for ev in events:
            if ev in {"FOMC", "CPI", "NFP"}:
                return True, ev
        return False, ""

    def _load_manual_csv(self) -> list[tuple[str, str]]:
        path = Path(self.cfg.manual_macro_dates_csv)
        if not path.exists():
            return []

        rows: list[tuple[str, str]] = []
        import csv

        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                event_date = (row.get("date") or "").strip()
                event_name = (row.get("event") or "").strip().upper()
                if not event_date or not event_name:
                    continue
                rows.append((event_date, event_name))
        return rows

    def _load_fomc_dates(self) -> list[tuple[str, str]]:
        """Parse decision days from Federal Reserve meeting calendar page.

        We record the second day of each meeting range (the decision day),
        and single-day meetings as-is.
        """
        url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            text = resp.text
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"FOMC calendar fetch failed: {exc}")
            return []

        rows: list[tuple[str, str]] = []
        pattern = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2})(?:-(\d{1,2}))?,\s+(20\d{2})")
        month_lookup = {
            "January": 1,
            "February": 2,
            "March": 3,
            "April": 4,
            "May": 5,
            "June": 6,
            "July": 7,
            "August": 8,
            "September": 9,
            "October": 10,
            "November": 11,
            "December": 12,
        }

        for month, day1, day2, year in pattern.findall(text):
            if month not in month_lookup:
                continue
            m = month_lookup[month]
            d = int(day2 or day1)
            y = int(year)
            try:
                event_date = date(y, m, d).isoformat()
            except ValueError:
                continue
            rows.append((event_date, "FOMC"))

        return rows

    def _load_fmp_calendar(self) -> list[tuple[str, str]]:
        api_key = self.cfg.macro_api_key.strip()
        if not api_key:
            return []

        today = date.today()
        horizon = today + timedelta(days=370)
        url = (
            "https://financialmodelingprep.com/stable/economic-calendar"
            f"?from={today.isoformat()}&to={horizon.isoformat()}&apikey={api_key}"
        )

        try:
            resp = requests.get(url, timeout=25)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"FMP macro calendar fetch failed: {exc}")
            return []

        rows: list[tuple[str, str]] = []
        for item in payload if isinstance(payload, list) else []:
            event_name = str(item.get("event") or item.get("name") or "").lower()
            raw_date = str(item.get("date") or "")
            if not raw_date:
                continue
            event_date = raw_date.split("T", 1)[0]
            if "federal funds" in event_name or "interest rate decision" in event_name:
                rows.append((event_date, "FOMC"))
            elif "consumer price index" in event_name or event_name.startswith("cpi"):
                rows.append((event_date, "CPI"))
            elif "non farm payroll" in event_name or "non-farm payroll" in event_name:
                rows.append((event_date, "NFP"))

        return rows


class MarketDataService:
    def __init__(self, ib: IB, cfg: BotConfig, logger: BotLogger):
        self.ib = ib
        self.cfg = cfg
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)
        self.macro = MacroCalendar(cfg, logger)

        self._spx_contract: Optional[Contract] = None
        self._vix_contract: Optional[Contract] = None
        self._option_chain: Optional[OptionChainSnapshot] = None
        self._option_contract_cache: dict[tuple[str, float, str], Contract] = {}
        self._invalid_option_contracts: set[tuple[str, float, str]] = set()
        self._synthetic_quote_warned: set[str] = set()
        self._tickers: list[Ticker] = []
        self._ticker_handlers: list[tuple[Ticker, Callable[[Ticker], None]]] = []
        self._last_tick_log_at: dict[str, datetime] = {}

    def connect(self) -> None:
        if self.ib.isConnected():
            return

        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id, timeout=20)
        self.ib.reqMarketDataType(int(self.cfg.market_data_type))
        self.logger.info(
            "Connected to IBKR "
            f"host={self.cfg.host} port={self.cfg.port} clientId={self.cfg.client_id} "
            f"marketDataType={self.cfg.market_data_type}"
        )

    def disconnect(self) -> None:
        self.cancel_tick_streams()
        if self.ib.isConnected():
            self.ib.disconnect()

    def ensure_reference_contracts(self) -> None:
        if self._spx_contract and self._vix_contract:
            return

        spx = Index(self.cfg.underlying_symbol, self.cfg.underlying_exchange, self.cfg.currency)
        vix = Index("VIX", self.cfg.underlying_exchange, self.cfg.currency)

        q_spx = self.ib.qualifyContracts(spx)
        q_vix = self.ib.qualifyContracts(vix)
        if not q_spx:
            raise RuntimeError("unable to qualify SPX index contract")
        if not q_vix:
            raise RuntimeError("unable to qualify VIX index contract")

        self._spx_contract = q_spx[0]
        self._vix_contract = q_vix[0]

    def get_spx_price(self) -> float:
        self.ensure_reference_contracts()
        ticker = self.ib.reqTickers(self._spx_contract)[0]
        price = self._first_valid_price([ticker.marketPrice(), ticker.last, ticker.close])
        if price is None:
            raise RuntimeError("SPX price unavailable")
        return price

    def get_vix_price(self) -> float:
        self.ensure_reference_contracts()
        ticker = self.ib.reqTickers(self._vix_contract)[0]
        price = self._first_valid_price([ticker.marketPrice(), ticker.last, ticker.close])
        if price is None:
            raise RuntimeError("VIX price unavailable")
        return price

    def load_option_chain(self) -> OptionChainSnapshot:
        if self._option_chain:
            return self._option_chain

        self.ensure_reference_contracts()
        chains = self.ib.reqSecDefOptParams(
            self.cfg.underlying_symbol,
            "",
            self._spx_contract.secType,
            self._spx_contract.conId,
        )

        selected = None
        for chain in chains:
            if getattr(chain, "tradingClass", "") != self.cfg.preferred_trading_class:
                continue
            selected = chain
            break

        if selected is None and chains:
            selected = chains[0]

        if selected is None:
            raise RuntimeError("option chain not available")

        snapshot = OptionChainSnapshot(
            expirations=set(selected.expirations),
            strikes=sorted(float(s) for s in selected.strikes),
        )
        self._option_chain = snapshot
        return snapshot

    def expiry_for_dte(self, current_date: date, dte: int) -> Optional[str]:
        chain = self.load_option_chain()
        wanted = (current_date + timedelta(days=dte)).strftime("%Y%m%d")
        return wanted if wanted in chain.expirations else None

    def nearest_strike_at_or_below(self, target: float) -> Optional[float]:
        chain = self.load_option_chain()
        candidates = [s for s in chain.strikes if s <= target]
        if not candidates:
            return None
        return max(candidates)

    def build_put_contract(self, expiry: str, strike: float) -> Contract:
        return self.build_option_contract(expiry=expiry, strike=strike, right="P")

    def build_option_contract(self, expiry: str, strike: float, right: str) -> Contract:
        key = (expiry, float(strike), right.upper())
        cached = self._option_contract_cache.get(key)
        if cached is not None:
            return cached

        option = Option(
            self.cfg.underlying_symbol,
            expiry,
            strike,
            right,
            exchange=self.cfg.option_exchange,
            tradingClass=self.cfg.preferred_trading_class,
            currency=self.cfg.currency,
            multiplier="100",
        )
        qualified = self.ib.qualifyContracts(option)
        if not qualified:
            raise RuntimeError(f"unable to qualify option {expiry} {strike}{right}")
        contract = qualified[0]
        self._option_contract_cache[key] = contract
        return contract

    def get_spread_quote(self, short_put: Contract, long_put: Contract) -> Optional[SpreadQuote]:
        tickers = self.ib.reqTickers(short_put, long_put)
        if len(tickers) != 2:
            return None

        short_ticker, long_ticker = tickers
        short_bid, short_ask = self._bid_ask_with_fallback(short_ticker)
        long_bid, long_ask = self._bid_ask_with_fallback(long_ticker)

        if short_bid is None or short_ask is None or long_bid is None or long_ask is None:
            return None

        bid = short_bid - long_ask
        ask = short_ask - long_bid
        mid = round((bid + ask) / 2.0, 2)
        if bid <= 0 or ask <= 0:
            return None

        return SpreadQuote(bid=round(bid, 2), ask=round(ask, 2), mid=mid, ts=datetime.now(self.tz))

    def get_credit_quote(self, expiry: str, legs: list[OptionLegSpec]) -> Optional[SpreadQuote]:
        if not legs:
            return None

        contracts: list[Contract] = []
        for leg in legs:
            key = (expiry, float(leg.strike), leg.right.upper())
            if key in self._invalid_option_contracts:
                return None
            try:
                contracts.append(self.build_option_contract(expiry, leg.strike, leg.right))
            except Exception as exc:  # noqa: BLE001
                self._invalid_option_contracts.add(key)
                self.logger.warning(
                    f"candidate contract unavailable {expiry} {leg.strike}{leg.right}: {exc}"
                )
                return None

        tickers = self.ib.reqTickers(*contracts)
        if len(tickers) != len(legs):
            return None

        short_bid_total = 0.0
        short_ask_total = 0.0
        long_bid_total = 0.0
        long_ask_total = 0.0

        synthetic_used = False
        for leg, ticker in zip(legs, tickers):
            bid, ask = self._bid_ask_with_fallback(ticker)
            if bid is None or ask is None:
                return None
            if self._valid_positive(ticker.bid) is None or self._valid_positive(ticker.ask) is None:
                synthetic_used = True

            qty = max(int(leg.quantity), 1)
            if leg.direction == LegDirection.SHORT:
                short_bid_total += bid * qty
                short_ask_total += ask * qty
            else:
                long_bid_total += bid * qty
                long_ask_total += ask * qty

        credit_bid = short_bid_total - long_ask_total
        credit_ask = short_ask_total - long_bid_total
        if credit_bid <= 0 or credit_ask <= 0:
            return None

        mid = round((credit_bid + credit_ask) / 2.0, 2)
        if synthetic_used:
            note = "delayed/fallback synthetic option quote path active"
            if note not in self._synthetic_quote_warned:
                self._synthetic_quote_warned.add(note)
                self.logger.warning(note)
        return SpreadQuote(
            bid=round(credit_bid, 2),
            ask=round(credit_ask, 2),
            mid=mid,
            ts=datetime.now(self.tz),
        )

    def start_tick_streams(
        self,
        contracts: list[Contract],
        on_tick: Optional[Callable[[Ticker], None]] = None,
    ) -> None:
        self.cancel_tick_streams()

        for contract in contracts:
            ticker = self.ib.reqMktData(contract, "", False, False)

            def _handler(t: Ticker, ticker_symbol: str = contract.localSymbol or contract.symbol) -> None:
                if on_tick:
                    on_tick(t)
                self._log_tick_if_due(t, ticker_symbol)

            ticker.updateEvent += _handler
            self._ticker_handlers.append((ticker, _handler))
            self._tickers.append(ticker)

    def cancel_tick_streams(self) -> None:
        for ticker, handler in self._ticker_handlers:
            try:
                ticker.updateEvent -= handler
            except Exception:  # noqa: BLE001
                pass
        for ticker in self._tickers:
            try:
                self.ib.cancelMktData(ticker.contract)
            except Exception:  # noqa: BLE001
                pass

        self._ticker_handlers.clear()
        self._tickers.clear()

    def _log_tick_if_due(self, ticker: Ticker, ticker_symbol: str) -> None:
        now = datetime.now(self.tz)
        last = self._last_tick_log_at.get(ticker_symbol)
        if last and (now - last).total_seconds() < self.cfg.tick_log_interval_seconds:
            return

        self._last_tick_log_at[ticker_symbol] = now
        contract = ticker.contract
        strike = float(getattr(contract, "strike", 0.0) or 0.0)
        right = str(getattr(contract, "right", ""))
        expiry = str(getattr(contract, "lastTradeDateOrContractMonth", ""))

        snapshot = TickSnapshot(
            ts=now,
            symbol=contract.symbol,
            expiry=expiry,
            strike=strike,
            right=right,
            bid=float(ticker.bid or 0.0),
            ask=float(ticker.ask or 0.0),
            last=float(ticker.last or 0.0),
            model=float(ticker.modelGreeks.optPrice if ticker.modelGreeks else 0.0),
            extras={
                "bidSize": float(ticker.bidSize or 0.0),
                "askSize": float(ticker.askSize or 0.0),
                "volume": float(ticker.volume or 0.0),
                "impliedVol": float(ticker.impliedVolatility or 0.0),
                "delta": float(ticker.modelGreeks.delta if ticker.modelGreeks else 0.0),
                "gamma": float(ticker.modelGreeks.gamma if ticker.modelGreeks else 0.0),
                "theta": float(ticker.modelGreeks.theta if ticker.modelGreeks else 0.0),
                "vega": float(ticker.modelGreeks.vega if ticker.modelGreeks else 0.0),
            },
        )
        self.logger.tick_event(snapshot)

    def _bid_ask_with_fallback(self, ticker: Ticker) -> tuple[float | None, float | None]:
        bid = self._valid_positive(ticker.bid)
        ask = self._valid_positive(ticker.ask)
        if bid is not None and ask is not None:
            return bid, ask

        ref = self._first_valid_price(
            [
                ticker.marketPrice(),
                ticker.last,
                ticker.close,
                ticker.modelGreeks.optPrice if ticker.modelGreeks else None,
            ]
        )
        if ref is None:
            return None, None
        synthetic = round(ref, 2)
        return synthetic, synthetic

    @staticmethod
    def _valid_positive(value: object) -> float | None:
        if value is None:
            return None
        try:
            val = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(val) or val <= 0.0:
            return None
        return val

    @staticmethod
    def _first_valid_price(values: list[object]) -> float | None:
        for value in values:
            parsed = MarketDataService._valid_positive(value)
            if parsed is not None:
                return parsed
        return None
