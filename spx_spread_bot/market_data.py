from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import time as _time

import requests
import yfinance as _yf
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
        # Persistent streaming ticker for the underlying index — avoids
        # per-call reqTickers() which can timeout from APScheduler threads.
        self._underlying_stream_ticker: Optional[Ticker] = None
        # yfinance price cache: (price, fetched_at) — avoids hammering yfinance
        # on every signal evaluation while keeping prices fresh (10-second TTL).
        self._yf_spx_cache: tuple[float, float] = (0.0, 0.0)
        self._yf_vix_cache: tuple[float, float] = (0.0, 0.0)
        # Day-open cache: (open_price, date_str) — open doesn't change intraday,
        # so we cache by date rather than by time.
        self._yf_open_cache: tuple[float, str] = (0.0, "")
        self._yf_vix_open_cache: tuple[float, str] = (0.0, "")

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

    def start_underlying_stream(self) -> None:
        """Subscribe to a continuous streaming ticker for the underlying index.

        This is called once after connect so that get_cached_underlying_price()
        can read price data without ever calling reqTickers() (which is a
        synchronous request-response and times out from APScheduler threads).
        """
        try:
            self.ensure_reference_contracts()
            if self._underlying_stream_ticker is not None:
                try:
                    self.ib.cancelMktData(self._underlying_stream_ticker.contract)
                except Exception:  # noqa: BLE001
                    pass
            self._underlying_stream_ticker = self.ib.reqMktData(
                self._spx_contract, "", False, False
            )
            self.logger.info(f"underlying price stream started for {self.cfg.underlying_symbol}")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"underlying price stream failed to start: {exc}")

    def get_cached_underlying_price(self) -> float:
        """Read the underlying price from the streaming ticker (no network call).

        Falls back to the last close if live/last are not yet populated.
        Returns 0.0 if no data is available.
        """
        t = self._underlying_stream_ticker
        if t is None:
            return 0.0
        price = self._first_valid_price([t.marketPrice(), t.last, t.close])
        return price or 0.0

    def disconnect(self) -> None:
        if self._underlying_stream_ticker is not None:
            try:
                self.ib.cancelMktData(self._underlying_stream_ticker.contract)
            except Exception:  # noqa: BLE001
                pass
            self._underlying_stream_ticker = None
        self.cancel_tick_streams()
        if self.ib.isConnected():
            self.ib.disconnect()

    def prewarm_option_contracts(
        self,
        underlying_price: float,
        expiries: list[str],
        rights: tuple[str, ...] = ("P",),
        otm_range_pct: float = 0.06,
        itm_range_pct: float = 0.01,
    ) -> int:
        """Qualify option contracts in the main thread and cache their conIds.

        Call this at startup (before APScheduler starts) so that background
        threads never need to call qualifyContracts — they just hit the cache.
        Uses the actual strikes from the loaded option chain (respects 1-pt XSP
        intervals and 5-pt SPX intervals automatically).

        Returns the number of contracts successfully pre-qualified.
        """
        if not expiries:
            return 0

        # Pull strikes from the cached option chain — respects the actual
        # strike intervals (1-pt for XSP, 5-pt for SPX) without hard-coding.
        chain = self.load_option_chain()
        lo = underlying_price * (1.0 - otm_range_pct)
        hi = underlying_price * (1.0 + itm_range_pct)
        strikes = [s for s in chain.strikes if lo <= s <= hi]

        if not strikes:
            self.logger.warning(
                f"option pre-warm: no chain strikes found in range [{lo:.1f},{hi:.1f}]"
            )
            return 0

        qualified_count = 0
        total = len(expiries) * len(rights) * len(strikes)
        self.logger.info(
            f"pre-qualifying {total} option contracts "
            f"({len(expiries)} expiry × {len(rights)} right × {len(strikes)} strikes "
            f"in range [{strikes[0]:.1f}–{strikes[-1]:.1f}])"
        )

        for expiry in expiries:
            for right in rights:
                for strike in strikes:
                    key = (expiry, float(strike), right.upper())
                    if key in self._option_contract_cache or key in self._invalid_option_contracts:
                        qualified_count += 1
                        continue
                    try:
                        self.build_option_contract(expiry, strike, right)
                        qualified_count += 1
                    except Exception:  # noqa: BLE001
                        self._invalid_option_contracts.add(key)

        self.logger.info(f"option contract pre-warm: {qualified_count}/{total} qualified")
        return qualified_count

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
        """Return SPX (or XSP=SPX/10) price via yfinance with a 10-second cache.

        Using yfinance avoids ib_insync reqTickers() calls from APScheduler
        threads, which race on loop.run_until_complete() and time out.
        yfinance prices are 15-min delayed for free tier but are accurate
        enough for strike selection and VIX/regime checks.
        """
        cached_price, fetched_at = self._yf_spx_cache
        if cached_price > 0 and _time.time() - fetched_at < 10:
            return cached_price
        try:
            info = _yf.Ticker("^GSPC").fast_info
            price = float(getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None) or 0.0)
            if price <= 0:
                hist = _yf.Ticker("^GSPC").history(period="2d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            if cached_price > 0:
                return cached_price  # return stale value rather than raising
            raise RuntimeError(f"SPX price unavailable: {exc}") from exc
        if price <= 0:
            if cached_price > 0:
                return cached_price
            raise RuntimeError("SPX price unavailable from yfinance")
        if self.cfg.underlying_symbol.upper() == "XSP":
            price = price / 10.0
        price = round(price, 2)
        self._yf_spx_cache = (price, _time.time())
        return price

    def get_vix_price(self) -> float:
        """Return VIX price via yfinance with a 10-second cache."""
        cached_price, fetched_at = self._yf_vix_cache
        if cached_price > 0 and _time.time() - fetched_at < 10:
            return cached_price
        try:
            info = _yf.Ticker("^VIX").fast_info
            price = float(getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None) or 0.0)
            if price <= 0:
                hist = _yf.Ticker("^VIX").history(period="2d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            if cached_price > 0:
                return cached_price
            raise RuntimeError(f"VIX price unavailable: {exc}") from exc
        if price <= 0:
            if cached_price > 0:
                return cached_price
            raise RuntimeError("VIX price unavailable from yfinance")
        price = round(price, 2)
        self._yf_vix_cache = (price, _time.time())
        return price

    def get_spx_open(self, current_date: "date") -> float:
        """Return the SPX day-open price via yfinance, cached by date.

        The open doesn't change intraday so we cache keyed on the date string
        rather than a TTL. On a new trading day the cache misses and re-fetches.
        Returns 0.0 on failure so callers can treat a zero as 'unavailable'
        and fail open rather than blocking an entry.
        """
        cached_open, cached_date = self._yf_open_cache
        if cached_open > 0 and cached_date == current_date.isoformat():
            return cached_open
        try:
            info = _yf.Ticker("^GSPC").fast_info
            open_price = float(getattr(info, "open", None) or 0.0)
            if open_price <= 0:
                hist = _yf.Ticker("^GSPC").history(period="1d")
                if not hist.empty:
                    open_price = float(hist["Open"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"SPX open price fetch failed: {exc}")
            return cached_open if cached_open > 0 else 0.0
        if open_price <= 0:
            return cached_open if cached_open > 0 else 0.0
        if self.cfg.underlying_symbol.upper() == "XSP":
            open_price = open_price / 10.0
        open_price = round(open_price, 2)
        self._yf_open_cache = (open_price, current_date.isoformat())
        self.logger.info(f"SPX day open cached: {open_price:.2f} for {current_date}")
        return open_price

    def get_vix_open(self, current_date: "date") -> float:
        """Return today's VIX opening price via yfinance, cached by date.

        Used to detect intraday VIX spikes: if VIX has risen more than
        cfg.vix_max_daily_rise points from the open, the signal engine skips.
        Returns 0.0 on failure so callers treat zero as 'unavailable' and fail open.
        """
        cached_open, cached_date = self._yf_vix_open_cache
        if cached_open > 0 and cached_date == current_date.isoformat():
            return cached_open
        try:
            info = _yf.Ticker("^VIX").fast_info
            open_price = float(getattr(info, "open", None) or 0.0)
            if open_price <= 0:
                hist = _yf.Ticker("^VIX").history(period="1d")
                if not hist.empty:
                    open_price = float(hist["Open"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"VIX open price fetch failed: {exc}")
            return cached_open if cached_open > 0 else 0.0
        if open_price <= 0:
            return cached_open if cached_open > 0 else 0.0
        open_price = round(open_price, 2)
        self._yf_vix_open_cache = (open_price, current_date.isoformat())
        self.logger.info(f"VIX day open cached: {open_price:.2f} for {current_date}")
        return open_price

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
        # Use streaming subscriptions (not snapshot) — see get_credit_quote for rationale.
        contracts_for_spread = [short_put, long_put]
        stream_tickers = [self.ib.reqMktData(c, "", False, False) for c in contracts_for_spread]
        self.ib.sleep(2.0)
        for c in contracts_for_spread:
            try:
                self.ib.cancelMktData(c)
            except Exception:  # noqa: BLE001
                pass
        if len(stream_tickers) != 2:
            return None
        short_ticker, long_ticker = stream_tickers
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
                exc_msg = str(exc)
                # Only cache as permanently invalid when the gateway explicitly
                # says the contract doesn't exist.  TimeoutError (empty message)
                # means the background thread couldn't pump the event loop to
                # receive the response — permanently caching those would prevent
                # retries after a restart or prewarm repopulates the cache.
                if exc_msg:
                    self._invalid_option_contracts.add(key)
                self.logger.warning(
                    f"candidate contract unavailable {expiry} {leg.strike}{leg.right}: {exc_msg}"
                )
                return None

        # IBKR snapshot market data (reqMktData snapshot=True, used by reqTickers)
        # silently drops requests for XSP options on this gateway — the future
        # never resolves and the 8-second RequestTimeout fires. Streaming
        # subscriptions (snapshot=False) work correctly: IBKR starts pushing
        # ticks within ~1s of subscription. We subscribe, wait 2s for data to
        # flow, read the tickers, then cancel the subscriptions.
        stream_tickers = []
        try:
            for c in contracts:
                stream_tickers.append(self.ib.reqMktData(c, "", False, False))
        except Exception as _req_exc:  # noqa: BLE001
            # Gateway disconnected mid-subscription — treat as no quote rather than crashing.
            self.logger.warning(f"get_credit_quote reqMktData failed ({_req_exc}); skipping candidate")
            return None
        self.ib.sleep(2.0)
        for c in contracts:
            try:
                self.ib.cancelMktData(c)
            except Exception:  # noqa: BLE001
                pass
        tickers = stream_tickers
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
        # Only hard-reject when the best-case credit (ask side) is also non-positive.
        # credit_bid can be negative on OTM 0DTE spreads with wide bid/ask — that is
        # normal and should not disqualify the candidate; min_credit on the mid filters it.
        if credit_ask <= 0:
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

    def get_streamed_ticker(self, conid: int):
        """Return the live streaming Ticker for *conid* if one exists and has
        received at least one valid price tick (bid, ask, or last > 0).
        Returns None if the stream is not active or data has not arrived yet.
        """
        for ticker in self._tickers:
            if ticker.contract and int(getattr(ticker.contract, "conId", 0) or 0) == conid:
                bid = float(ticker.bid or 0.0)
                ask = float(ticker.ask or 0.0)
                last = float(ticker.last or 0.0)
                if bid > 0 or ask > 0 or last > 0:
                    return ticker
        return None

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
