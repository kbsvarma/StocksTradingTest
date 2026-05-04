from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import IB

from config_loader import BotConfig
from market_data import MarketDataService
from models import CandidateSpread, Decision, LegDirection, OptionLegSpec, SignalResult, StrategyType
from trade_logger import BotLogger


@dataclass(slots=True)
class VolatilityRegime:
    name: str
    otm_low: float
    otm_high: float


class SignalEngine:
    def __init__(self, ib: IB, cfg: BotConfig, market: MarketDataService, logger: BotLogger):
        self.ib = ib
        self.cfg = cfg
        self.market = market
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)

    def evaluate(self, now: datetime, trade_taken_today: bool) -> SignalResult:
        if trade_taken_today and self.cfg.trade_once_per_day:
            return SignalResult(
                decision=Decision.SKIP,
                reason="trade already taken today",
                spx_price=0.0,
                vix_price=0.0,
            )

        if not self.cfg.is_trade_day(now.date()):
            return SignalResult(
                decision=Decision.SKIP,
                reason="day-of-week filter",
                spx_price=0.0,
                vix_price=0.0,
            )

        blocking, event = self.market.macro.has_blocking_event(now.date())
        if blocking:
            return SignalResult(
                decision=Decision.SKIP,
                reason=f"macro event filter: {event}",
                spx_price=0.0,
                vix_price=0.0,
            )

        try:
            spx_price = self.market.get_spx_price()
            vix_price = self.market.get_vix_price()
        except Exception as exc:  # noqa: BLE001
            return SignalResult(
                decision=Decision.SKIP,
                reason=f"market data unavailable: {exc}",
                spx_price=0.0,
                vix_price=0.0,
            )

        regime = self._resolve_regime(vix_price)
        if regime is None:
            return SignalResult(
                decision=Decision.SKIP,
                reason=f"VIX in tail-risk zone ({vix_price:.2f} > {self.cfg.vix_max})",
                spx_price=spx_price,
                vix_price=vix_price,
            )

        available_margin = self._available_margin()
        if available_margin is not None and available_margin < self.cfg.min_available_margin:
            return SignalResult(
                decision=Decision.SKIP,
                reason=f"available margin below minimum (${available_margin:,.0f})",
                spx_price=spx_price,
                vix_price=vix_price,
            )

        candidates = self._find_candidates(now.date(), spx_price, regime)
        if not candidates:
            return SignalResult(
                decision=Decision.SKIP,
                reason="no credit-qualified strategy candidate",
                spx_price=spx_price,
                vix_price=vix_price,
                candidates=[],
            )

        selected = self._select_candidate_by_priority(candidates)
        if selected is None:
            return SignalResult(
                decision=Decision.SKIP,
                reason="no strategy candidate matched priority",
                spx_price=spx_price,
                vix_price=vix_price,
                candidates=candidates,
            )

        per_trade_risk = max(selected.max_loss_per_contract, 1.0)
        by_margin_cap = int(self.cfg.max_margin_dollars() // per_trade_risk)
        contracts = max(0, min(self.cfg.max_contracts, by_margin_cap))

        if contracts <= 0:
            return SignalResult(
                decision=Decision.SKIP,
                reason="margin cap allows zero contracts",
                spx_price=spx_price,
                vix_price=vix_price,
                candidate=selected,
                candidates=candidates,
            )

        estimated_margin = per_trade_risk * contracts
        if estimated_margin > self.cfg.max_margin_dollars() + 1e-9:
            return SignalResult(
                decision=Decision.SKIP,
                reason="estimated margin exceeds max margin",
                spx_price=spx_price,
                vix_price=vix_price,
                candidate=selected,
                candidates=candidates,
            )

        if available_margin is not None and estimated_margin > available_margin:
            contracts = int(available_margin // per_trade_risk)
            if contracts <= 0:
                return SignalResult(
                    decision=Decision.SKIP,
                    reason="insufficient available margin for one spread",
                    spx_price=spx_price,
                    vix_price=vix_price,
                    candidate=selected,
                    candidates=candidates,
                )
            estimated_margin = per_trade_risk * contracts

        return SignalResult(
            decision=Decision.ENTER,
            reason=f"enter ({selected.strategy.value}, {regime.name})",
            spx_price=spx_price,
            vix_price=vix_price,
            candidate=selected,
            candidates=candidates,
            contracts=contracts,
            estimated_margin=estimated_margin,
        )

    def ordered_candidates(self, candidates: list[CandidateSpread]) -> list[CandidateSpread]:
        by_strategy = {cand.strategy.value.upper(): cand for cand in candidates}
        ordered: list[CandidateSpread] = []
        for strategy in self.cfg.strategy_priority:
            found = by_strategy.pop(strategy.upper(), None)
            if found is not None:
                ordered.append(found)
        ordered.extend(sorted(by_strategy.values(), key=lambda c: abs(self.cfg.target_credit - (c.quote.mid if c.quote else 0.0))))
        return ordered

    def available_margin(self) -> Optional[float]:
        return self._available_margin()

    def _resolve_regime(self, vix: float) -> Optional[VolatilityRegime]:
        # Only hard-skip when VIX is dangerously elevated (tail-risk regime).
        # No lower bound: low-VIX sessions can still be traded using tighter OTM%.
        if vix > self.cfg.vix_max:
            return None
        if vix <= 20.0:
            low = self.cfg.otm_pct_low_vix
            return VolatilityRegime("normal", low, round(low + 0.002, 4))
        high = self.cfg.otm_pct_high_vix
        return VolatilityRegime("elevated", high, round(high + 0.005, 4))

    def _find_candidates(self, current_date: date, spx_price: float, regime: VolatilityRegime) -> list[CandidateSpread]:
        chain = self.market.load_option_chain()
        strikes = chain.strikes

        otm_points = [
            regime.otm_low,
            round((regime.otm_low + regime.otm_high) / 2.0, 4),
            regime.otm_high,
        ]

        enabled = {s.upper() for s in self.cfg.enabled_strategies}
        best_by_strategy: dict[StrategyType, tuple[float, CandidateSpread]] = {}

        for dte in self.cfg.dte_candidates:
            expiry = self.market.expiry_for_dte(current_date, dte)
            if not expiry:
                continue

            for otm in otm_points:
                target_level = spx_price * (1.0 - otm)
                short_put_base = self.market.nearest_strike_at_or_below(target_level)
                if short_put_base is None:
                    continue

                short_call_base = self._nearest_strike_at_or_above(strikes, spx_price * (1.0 + otm))
                if short_call_base is None:
                    continue

                put_try = self._strike_ladder_at_or_below(strikes, short_put_base, depth=4)
                call_try = self._strike_ladder_at_or_above(strikes, short_call_base, depth=4)

                if StrategyType.PUT_BWB.value in enabled:
                    for short_put in put_try:
                        cand = self._build_put_bwb(expiry, dte, otm, target_level, short_put, strikes)
                        if cand is not None:
                            self._consider_candidate(best_by_strategy, cand)

                if StrategyType.BULL_PUT_SPREAD.value in enabled:
                    for short_put in put_try:
                        cand = self._build_bull_put(expiry, dte, otm, target_level, short_put, strikes)
                        if cand is not None:
                            self._consider_candidate(best_by_strategy, cand)

                if StrategyType.IRON_CONDOR.value in enabled:
                    max_offsets = min(len(put_try), len(call_try), 4)
                    for i in range(max_offsets):
                        short_put = put_try[i]
                        short_call = call_try[i]
                        if short_call <= short_put:
                            continue
                        cand = self._build_iron_condor(
                            expiry=expiry,
                            dte=dte,
                            otm=otm,
                            target_level=target_level,
                            short_put=short_put,
                            short_call=short_call,
                            strikes=strikes,
                        )
                        if cand is not None:
                            self._consider_candidate(best_by_strategy, cand)

            if StrategyType.IRON_FLY.value in enabled:
                center = self._nearest_strike(strikes, spx_price)
                if center is not None:
                    cand = self._build_iron_fly(
                        expiry=expiry,
                        dte=dte,
                        spx_price=spx_price,
                        center=center,
                        strikes=strikes,
                    )
                    if cand is not None:
                        self._consider_candidate(best_by_strategy, cand)

        return [item[1] for item in best_by_strategy.values()]

    def _build_bull_put(
        self,
        expiry: str,
        dte: int,
        otm: float,
        target_level: float,
        short_put: float,
        strikes: list[float],
    ) -> Optional[CandidateSpread]:
        wing_target = short_put - float(self.cfg.spread_width)
        long_put = self._nearest_strike_at_or_below(strikes, wing_target)
        if long_put is None:
            return None
        if not (long_put < short_put):
            return None

        legs = [
            OptionLegSpec(right="P", strike=short_put, direction=LegDirection.SHORT, quantity=1),
            OptionLegSpec(right="P", strike=long_put, direction=LegDirection.LONG, quantity=1),
        ]
        quote = self.market.get_credit_quote(expiry, legs)
        if not quote or quote.mid < self.cfg.min_credit:
            return None

        max_loss = self._estimate_max_loss_dollars(legs, quote.mid)
        if max_loss <= 0:
            return None

        return CandidateSpread(
            strategy=StrategyType.BULL_PUT_SPREAD,
            expiry=expiry,
            dte=dte,
            legs=legs,
            otm_pct=otm,
            target_level=target_level,
            short_put_strike=short_put,
            long_put_strike=long_put,
            max_loss_per_contract=max_loss,
            notes=f"BPS({int(self.cfg.spread_width)}w)",
            quote=quote,
        )

    def _build_put_bwb(
        self,
        expiry: str,
        dte: int,
        otm: float,
        target_level: float,
        short_put: float,
        strikes: list[float],
    ) -> Optional[CandidateSpread]:
        upper_target = short_put + float(self.cfg.bwb_narrow_wing_width)
        lower_target = short_put - float(self.cfg.bwb_wide_wing_width)
        upper = self._nearest_strike_at_or_above(strikes, upper_target)
        lower = self._nearest_strike_at_or_below(strikes, lower_target)
        if upper is None or lower is None:
            return None
        if not (upper > short_put > lower):
            return None

        legs = [
            OptionLegSpec(right="P", strike=upper, direction=LegDirection.LONG, quantity=1),
            OptionLegSpec(right="P", strike=short_put, direction=LegDirection.SHORT, quantity=2),
            OptionLegSpec(right="P", strike=lower, direction=LegDirection.LONG, quantity=1),
        ]
        quote = self.market.get_credit_quote(expiry, legs)
        if not quote or quote.mid < self.cfg.min_credit:
            return None

        max_loss = self._estimate_max_loss_dollars(legs, quote.mid)
        if max_loss <= 0:
            return None

        return CandidateSpread(
            strategy=StrategyType.PUT_BWB,
            expiry=expiry,
            dte=dte,
            legs=legs,
            otm_pct=otm,
            target_level=target_level,
            short_put_strike=short_put,
            long_put_strike=lower,
            max_loss_per_contract=max_loss,
            notes=f"BWB({int(self.cfg.bwb_narrow_wing_width)}x{int(self.cfg.bwb_wide_wing_width)})",
            quote=quote,
        )

    def _build_iron_condor(
        self,
        expiry: str,
        dte: int,
        otm: float,
        target_level: float,
        short_put: float,
        short_call: float,
        strikes: list[float],
    ) -> Optional[CandidateSpread]:
        put_target = short_put - float(self.cfg.condor_wing_width)
        call_target = short_call + float(self.cfg.condor_wing_width)
        put_wing = self._nearest_strike_at_or_below(strikes, put_target)
        call_wing = self._nearest_strike_at_or_above(strikes, call_target)
        if put_wing is None or call_wing is None:
            return None
        if not (put_wing < short_put < short_call < call_wing):
            return None

        legs = [
            OptionLegSpec(right="P", strike=short_put, direction=LegDirection.SHORT, quantity=1),
            OptionLegSpec(right="P", strike=put_wing, direction=LegDirection.LONG, quantity=1),
            OptionLegSpec(right="C", strike=short_call, direction=LegDirection.SHORT, quantity=1),
            OptionLegSpec(right="C", strike=call_wing, direction=LegDirection.LONG, quantity=1),
        ]
        quote = self.market.get_credit_quote(expiry, legs)
        if not quote or quote.mid < self.cfg.min_credit:
            return None

        max_loss = self._estimate_max_loss_dollars(legs, quote.mid)
        if max_loss <= 0:
            return None

        return CandidateSpread(
            strategy=StrategyType.IRON_CONDOR,
            expiry=expiry,
            dte=dte,
            legs=legs,
            otm_pct=otm,
            target_level=target_level,
            short_put_strike=short_put,
            long_put_strike=put_wing,
            short_call_strike=short_call,
            long_call_strike=call_wing,
            max_loss_per_contract=max_loss,
            notes=f"IC({int(self.cfg.condor_wing_width)}w)",
            quote=quote,
        )

    def _build_iron_fly(
        self,
        expiry: str,
        dte: int,
        spx_price: float,
        center: float,
        strikes: list[float],
    ) -> Optional[CandidateSpread]:
        wing = float(self.cfg.iron_fly_wing_width)
        lower = self._nearest_strike_at_or_below(strikes, center - wing)
        upper = self._nearest_strike_at_or_above(strikes, center + wing)
        if lower is None or upper is None:
            return None
        if not (lower < center < upper):
            return None

        legs = [
            OptionLegSpec(right="P", strike=center, direction=LegDirection.SHORT, quantity=1),
            OptionLegSpec(right="C", strike=center, direction=LegDirection.SHORT, quantity=1),
            OptionLegSpec(right="P", strike=lower, direction=LegDirection.LONG, quantity=1),
            OptionLegSpec(right="C", strike=upper, direction=LegDirection.LONG, quantity=1),
        ]
        quote = self.market.get_credit_quote(expiry, legs)
        if not quote or quote.mid < self.cfg.min_credit:
            return None

        max_loss = self._estimate_max_loss_dollars(legs, quote.mid)
        if max_loss <= 0:
            return None

        otm = abs(spx_price - center) / max(spx_price, 1.0)
        return CandidateSpread(
            strategy=StrategyType.IRON_FLY,
            expiry=expiry,
            dte=dte,
            legs=legs,
            otm_pct=round(otm, 5),
            target_level=center,
            short_put_strike=center,
            long_put_strike=lower,
            short_call_strike=center,
            long_call_strike=upper,
            max_loss_per_contract=max_loss,
            notes=f"IFLY({int(self.cfg.iron_fly_wing_width)}w)",
            quote=quote,
        )

    def _select_candidate_by_priority(self, candidates: list[CandidateSpread]) -> Optional[CandidateSpread]:
        ordered = self.ordered_candidates(candidates)
        if not ordered:
            return None
        return ordered[0]

    def _consider_candidate(
        self,
        best_by_strategy: dict[StrategyType, tuple[float, CandidateSpread]],
        candidate: CandidateSpread,
    ) -> None:
        if not candidate.quote:
            return
        score = abs(self.cfg.target_credit - candidate.quote.mid)
        previous = best_by_strategy.get(candidate.strategy)
        if previous is None or score < previous[0]:
            best_by_strategy[candidate.strategy] = (score, candidate)

    @staticmethod
    def _strike_ladder_at_or_below(strikes: list[float], start: float, depth: int) -> list[float]:
        try:
            idx = strikes.index(start)
        except ValueError:
            return []
        return [strikes[i] for i in range(idx, max(-1, idx - depth), -1)]

    @staticmethod
    def _strike_ladder_at_or_above(strikes: list[float], start: float, depth: int) -> list[float]:
        try:
            idx = strikes.index(start)
        except ValueError:
            return []
        upper = min(len(strikes), idx + depth)
        return [strikes[i] for i in range(idx, upper)]

    @staticmethod
    def _nearest_strike(strikes: list[float], target: float) -> Optional[float]:
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - target))

    @staticmethod
    def _nearest_strike_at_or_above(strikes: list[float], target: float) -> Optional[float]:
        values = [s for s in strikes if s >= target]
        if not values:
            return None
        return min(values)

    @staticmethod
    def _nearest_strike_at_or_below(strikes: list[float], target: float) -> Optional[float]:
        values = [s for s in strikes if s <= target]
        if not values:
            return None
        return max(values)

    @staticmethod
    def _estimate_max_loss_dollars(legs: list[OptionLegSpec], credit: float) -> float:
        unique_strikes = sorted({float(leg.strike) for leg in legs})
        if not unique_strikes:
            return 0.0
        scenarios = [0.0, *unique_strikes, unique_strikes[-1] + 500.0]

        worst_loss_points = 0.0
        for spot in scenarios:
            pnl_points = credit
            for leg in legs:
                intrinsic = max(leg.strike - spot, 0.0) if leg.right == "P" else max(spot - leg.strike, 0.0)
                qty = max(int(leg.quantity), 1)
                if leg.direction == LegDirection.SHORT:
                    pnl_points -= intrinsic * qty
                else:
                    pnl_points += intrinsic * qty
            worst_loss_points = max(worst_loss_points, max(0.0, -pnl_points))

        return round(worst_loss_points * 100.0, 2)

    def _available_margin(self) -> Optional[float]:
        # Use ib.accountValues() instead of ib.accountSummary().
        # accountSummary() sends a blocking network request — it times out when
        # called from APScheduler threads (loop.run_until_complete race).
        # accountValues() reads from ib_insync's in-process cache — no network
        # call, safe from any thread.
        try:
            values = self.ib.accountValues()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"account summary unavailable: {exc}")
            return None

        for item in values:
            if item.tag == "AvailableFunds" and item.currency == self.cfg.currency:
                try:
                    return float(item.value)
                except ValueError:
                    return None
        return None
