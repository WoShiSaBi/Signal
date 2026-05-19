from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging

import pandas as pd

from strategies.fvg import (
    FVG,
    find_fvgs_before_index,
    find_fvgs_from_time,
    find_ifvg_disrespect,
    find_latest_fvg_review,
    find_latest_respected_fvg,
    first_zone_touch_index,
    has_disrespect_close,
)
from strategies.liquidity import LiquiditySweep, latest_sweep_after_index
from strategies.pivots import normalize_ohlc
from strategies.risk import (
    RiskPlan,
    build_risk_plan,
    disrespect_candle_touches_target,
    entry_from_ifvg,
    nearest_liquidity_target,
)


def _fmt_price(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.5f}"


def _fmt_time(value: pd.Timestamp | None) -> str:
    return "N/A" if value is None else str(value)


def _fmt_zone(fvg: FVG | None) -> str:
    if fvg is None:
        return "N/A"
    return f"{fvg.direction} {_fmt_price(fvg.bottom)}-{_fmt_price(fvg.top)}"


@dataclass
class TimeframeSet:
    name: str
    htf: str
    mtf: str
    ltf: str


@dataclass
class StrategySignal:
    symbol: str
    timeframe_set: str
    htf: str
    mtf: str
    ltf: str
    signal: str  # BUY, SELL, WAIT, INVALIDATED
    scenario: str
    direction: str | None = None
    htf_fvg: FVG | None = None
    mtf_sweep: LiquiditySweep | None = None
    ifvg: FVG | None = None
    entry_price: float | None = None
    hard_stop_loss: float | None = None
    dynamic_stop_condition: str | None = None
    tp1: float | None = None
    tp2: float | None = None
    risk_reward: float | None = None
    reason: list[str] = field(default_factory=list)
    invalidation_reason: str | None = None
    confluence_score: int = 0
    confluence_total: int = 0
    confluence_factors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    setup_timestamp: pd.Timestamp | None = None

    def is_alertable(self) -> bool:
        return self.signal in {"BUY", "SELL", "INVALIDATED", "WAIT"}


class MTFFractalIFVGStrategy:
    def __init__(self, settings: dict, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        pivots = settings.get("pivots", {})
        fvg = settings.get("fvg", {})
        scenarios = settings.get("scenarios", {})
        override = scenarios.get("mtf_override", {}) if isinstance(scenarios, dict) else {}
        base_ltf = scenarios.get("base_ltf", {}) if isinstance(scenarios, dict) else {}
        invalidation = settings.get("invalidation", {})
        risk = settings.get("risk", {})
        liquidity_sweep = settings.get("liquidity_sweep", {})
        trend_filter = settings.get("trend_filter", {})
        confluence = settings.get("confluence", {})
        filters = settings.get("filters", {})
        if not isinstance(filters, dict):
            filters = {}

        self.left = int(pivots.get("left_bars", settings.get("pivot_left_bars", 3)))
        self.right = int(pivots.get("right_bars", settings.get("pivot_right_bars", 3)))
        self.merge_enabled = bool(fvg.get("merge_enabled", settings.get("fvg_merge_enabled", True)))
        self.require_entry_fvg_opposes_sweep = bool(fvg.get("require_entry_fvg_opposes_sweep", True))
        self.untapped_htf_fvg_mode = str(fvg.get("untapped_htf_fvg_mode", "bonus")).lower()
        self.liquidity_sweep_enabled = bool(liquidity_sweep.get("enabled", True))
        self.override_enabled = bool(override.get("enabled", True))
        self.base_ltf_enabled = bool(base_ltf.get("enabled", True))
        self.override_lookback = int(override.get("lookback_candles", settings.get("mtf_override_lookback_candles", 4)))
        self.scenario_3_enabled = bool(invalidation.get("scenario_3_enabled", True))
        self.minimum_rr_enabled = bool(invalidation.get("minimum_rr_enabled", True))
        self.risk_settings = risk if isinstance(risk, dict) else {}
        self.minimum_rr = float(self.risk_settings.get("minimum_risk_reward", settings.get("minimum_risk_reward", 2.0)))
        self.require_tp1 = bool(self.risk_settings.get("require_tp1", True))
        self.trend_filter = trend_filter if isinstance(trend_filter, dict) else {}
        self.trend_filter_enabled = bool(self.trend_filter.get("enabled", True))
        self.trend_strict_requirement = bool(self.trend_filter.get("strict_requirement", False))
        self.trend_fast_period = int(self.trend_filter.get("fast_period", 50))
        self.trend_slow_period = int(self.trend_filter.get("slow_period", 200))
        self.high_rr_threshold = float(confluence.get("high_rr_threshold", 3.0)) if isinstance(confluence, dict) else 3.0
        self.min_htf_fvg_pips = float(filters.get("min_htf_fvg_pips", 0) or 0)
        self.pip_size_setting = filters.get("pip_size", "auto")

    def analyze(
        self,
        symbol: str,
        timeframe_set: TimeframeSet,
        htf_df: pd.DataFrame,
        mtf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        daily_df: pd.DataFrame,
    ) -> StrategySignal:
        try:
            htf = normalize_ohlc(htf_df)
            mtf = normalize_ohlc(mtf_df)
            ltf = normalize_ohlc(ltf_df)
            daily = normalize_ohlc(daily_df) if not daily_df.empty else pd.DataFrame()
        except ValueError as exc:
            return self._invalid(symbol, timeframe_set, f"Data error: {exc}")

        htf_fvg, htf_touch_index = find_latest_respected_fvg(htf, self.merge_enabled)
        if htf_fvg is not None and self.min_htf_fvg_pips > 0:
            htf_fvg_pips = self._fvg_size_pips(symbol, htf_fvg)
            if htf_fvg_pips < self.min_htf_fvg_pips:
                self.logger.info(
                    "%s %s: HTF FVG ignored because size %.2f pips is below minimum %.2f pips: %s %.5f-%.5f",
                    symbol,
                    timeframe_set.name,
                    htf_fvg_pips,
                    self.min_htf_fvg_pips,
                    htf_fvg.direction,
                    htf_fvg.bottom,
                    htf_fvg.top,
                )
                return self._wait(
                    symbol,
                    timeframe_set,
                    (
                        "No trade: HTF FVG size filter blocked the setup. "
                        f"Gap size is {htf_fvg_pips:.2f} pips, minimum is {self.min_htf_fvg_pips:.2f} pips "
                        f"({_fmt_zone(htf_fvg)})."
                    ),
                )

        if htf_fvg is None:
            latest_htf_fvg = find_latest_fvg_review(htf, self.merge_enabled)
            if latest_htf_fvg is not None and self.min_htf_fvg_pips > 0:
                latest_htf_fvg_pips = self._fvg_size_pips(symbol, latest_htf_fvg)
                if latest_htf_fvg_pips < self.min_htf_fvg_pips:
                    return self._wait(
                        symbol,
                        timeframe_set,
                        (
                            "No trade: latest HTF FVG was ignored by the size filter. "
                            f"Gap size is {latest_htf_fvg_pips:.2f} pips, minimum is "
                            f"{self.min_htf_fvg_pips:.2f} pips ({_fmt_zone(latest_htf_fvg)})."
                        ),
                    )
            if latest_htf_fvg and latest_htf_fvg.status == "invalidated":
                self.logger.info(
                    "%s %s: HTF FVG invalidated: %s %.5f-%.5f",
                    symbol,
                    timeframe_set.name,
                    latest_htf_fvg.direction,
                    latest_htf_fvg.bottom,
                    latest_htf_fvg.top,
                )
                return self._wait(
                    symbol,
                    timeframe_set,
                    (
                        "No trade: latest HTF FVG was invalidated instead of respected "
                        f"({_fmt_zone(latest_htf_fvg)})."
                    ),
                )
            if latest_htf_fvg:
                return self._wait(
                    symbol,
                    timeframe_set,
                    (
                        "No trade yet: HTF FVG exists but has not been respected by a later touch "
                        f"({_fmt_zone(latest_htf_fvg)})."
                    ),
                )
            return self._wait(symbol, timeframe_set, "No trade yet: no HTF FVG was found in the fetched candles.")

        self.logger.info(
            "%s %s: HTF FVG detected and respected: %s %.5f-%.5f",
            symbol,
            timeframe_set.name,
            htf_fvg.direction,
            htf_fvg.bottom,
            htf_fvg.top,
        )

        invalidated, invalidated_index = has_disrespect_close(htf, htf_fvg)
        if invalidated:
            invalid_time = pd.Timestamp(htf.iloc[invalidated_index]["time"]) if invalidated_index is not None else None
            return self._invalid(
                symbol,
                timeframe_set,
                (
                    "No trade: HTF FVG disrespect invalidation. An HTF candle closed beyond the FVG zone "
                    f"({_fmt_zone(htf_fvg)}) at {_fmt_time(invalid_time)}."
                ),
            )

        trade_direction = self._direction_from_htf_fvg(htf_fvg)
        htf_touch_time = pd.Timestamp(htf.iloc[htf_touch_index]["time"]) if htf_touch_index is not None else None
        is_untapped_htf_fvg = self._is_untapped_htf_fvg(htf, htf_fvg, htf_touch_index)
        htf_fvg.metadata["is_untapped"] = is_untapped_htf_fvg

        if self.untapped_htf_fvg_mode in {"require", "required", "strict", "requirement"} and not is_untapped_htf_fvg:
            signal = self._wait(
                symbol,
                timeframe_set,
                (
                    "No trade: HTF FVG is not virgin/untapped before the current setup touch "
                    f"({_fmt_zone(htf_fvg)})."
                ),
            )
            signal.htf_fvg = htf_fvg
            signal.direction = trade_direction
            return signal

        htf_trend = self._trend_direction(htf)
        if self.trend_filter_enabled and self.trend_strict_requirement and htf_trend != trade_direction:
            signal = self._wait(
                symbol,
                timeframe_set,
                (
                    "No trade: HTF trend alignment is required, but trend is "
                    f"{htf_trend or 'unknown'} while setup bias is {trade_direction}."
                ),
            )
            signal.htf_fvg = htf_fvg
            signal.direction = trade_direction
            return signal

        mtf_start_index = None
        if htf_touch_time is not None:
            matching = mtf.index[pd.to_datetime(mtf["time"]) >= htf_touch_time].tolist()
            mtf_start_index = matching[0] if matching else None

        if not self.liquidity_sweep_enabled:
            signal = self._wait(symbol, timeframe_set, "Liquidity sweep confirmation is disabled in config.")
            signal.htf_fvg = htf_fvg
            return signal

        sweep = latest_sweep_after_index(
            mtf,
            mtf_start_index,
            self.left,
            self.right,
            signal_direction=trade_direction,
        )
        if sweep is None:
            signal = self._wait(
                symbol,
                timeframe_set,
                (
                    f"No trade yet: HTF {htf_fvg.direction} FVG creates {trade_direction} bias, "
                    "but no aligned MTF liquidity sweep has formed "
                    f"after the HTF touch at {_fmt_time(htf_touch_time)}."
                ),
            )
            signal.htf_fvg = htf_fvg
            signal.direction = trade_direction
            return signal

        self.logger.info(
            "%s %s: MTF liquidity sweep detected: %s level %.5f at %s",
            symbol,
            timeframe_set.name,
            sweep.direction,
            sweep.swept_level,
            sweep.timestamp,
        )

        required_fvg_direction = None
        if self.require_entry_fvg_opposes_sweep:
            required_fvg_direction = "bearish" if trade_direction == "BUY" else "bullish"

        override_fvgs = []
        if self.override_enabled:
            override_fvgs = find_fvgs_before_index(
                mtf,
                sweep.candle_index,
                self.override_lookback,
                direction=required_fvg_direction,
                merge_enabled=self.merge_enabled,
            )

        if override_fvgs:
            self.logger.info("%s %s: Scenario 1 MTF Override detected", symbol, timeframe_set.name)
            selected_fvg = override_fvgs[-1]
            ifvg, disrespect_index = find_ifvg_disrespect(mtf, selected_fvg, trade_direction)
            scenario = "Scenario 1 MTF Override"
            trade_df = mtf
            ifvg_timeframe = timeframe_set.mtf
            scenario_reason = "MTF override FVG found before the sweep."
        else:
            if not self.base_ltf_enabled:
                signal = self._wait(
                    symbol,
                    timeframe_set,
                    "No trade: no MTF override FVG qualified and the fallback Base LTF strategy is disabled in config.",
                )
                signal.htf_fvg = htf_fvg
                signal.mtf_sweep = sweep
                signal.direction = trade_direction
                signal.scenario = "Base LTF Strategy Disabled"
                return signal

            self.logger.info("%s %s: Base LTF Strategy selected", symbol, timeframe_set.name)
            ltf_fvgs = find_fvgs_from_time(
                ltf,
                sweep.timestamp,
                direction=required_fvg_direction,
                merge_enabled=self.merge_enabled,
            )
            selected_fvg = ltf_fvgs[0] if ltf_fvgs else None
            ifvg, disrespect_index = (
                find_ifvg_disrespect(ltf, selected_fvg, trade_direction) if selected_fvg else (None, None)
            )
            scenario = "Base LTF Strategy"
            trade_df = ltf
            ifvg_timeframe = timeframe_set.ltf
            scenario_reason = "No MTF override FVG found. Dropped to LTF."

        if selected_fvg is None:
            if scenario == "Scenario 1 MTF Override":
                search_detail = (
                    f"lookback={self.override_lookback} MTF candles before the sweep; "
                    f"required direction={required_fvg_direction or 'any'}"
                )
            else:
                search_detail = (
                    f"searched {timeframe_set.ltf} candles from sweep time {sweep.timestamp}; "
                    f"required direction={required_fvg_direction or 'any'}"
                )
            signal = self._wait(
                symbol,
                timeframe_set,
                (
                    "No trade yet: liquidity sweep confirmed, but no qualifying entry FVG was found "
                    f"({search_detail})."
                ),
            )
            signal.htf_fvg = htf_fvg
            signal.mtf_sweep = sweep
            signal.direction = trade_direction
            signal.scenario = scenario
            return signal

        if ifvg is None or disrespect_index is None:
            signal = self._wait(
                symbol,
                timeframe_set,
                (
                    "No trade yet: entry FVG was found, but price has not closed through it to confirm IFVG "
                    f"conversion for a {trade_direction} setup ({_fmt_zone(selected_fvg)})."
                ),
            )
            signal.htf_fvg = htf_fvg
            signal.mtf_sweep = sweep
            signal.ifvg = selected_fvg
            signal.direction = trade_direction
            signal.scenario = scenario
            return signal

        self.logger.info(
            "%s %s: IFVG disrespect confirmed on %s at %s",
            symbol,
            timeframe_set.name,
            ifvg_timeframe,
            ifvg.disrespected_at_time,
        )

        provisional_entry = entry_from_ifvg(
            ifvg,
            trade_direction,
            str(self.risk_settings.get("entry_boundary", "support_resistance")),
        )
        tp1 = nearest_liquidity_target(
            trade_df,
            trade_direction,
            provisional_entry,
            self.left,
            self.right,
            before_index=disrespect_index,
        )
        disrespect_candle = trade_df.iloc[disrespect_index]

        if self.require_tp1 and tp1 is None:
            invalid = self._invalid(
                symbol,
                timeframe_set,
                (
                    "No trade: IFVG confirmed, but no valid TP1 liquidity target was found beyond the entry "
                    f"{_fmt_price(provisional_entry)}."
                ),
            )
            invalid.scenario = scenario
            invalid.direction = trade_direction
            invalid.htf_fvg = htf_fvg
            invalid.mtf_sweep = sweep
            invalid.ifvg = ifvg
            invalid.setup_timestamp = ifvg.disrespected_at_time
            return invalid

        if self.scenario_3_enabled and disrespect_candle_touches_target(disrespect_candle, trade_direction, tp1):
            invalid = self._invalid(
                symbol,
                timeframe_set,
                (
                    "No trade: Scenario 3 invalidation. The IFVG disrespect candle already touched TP1 "
                    f"({_fmt_price(tp1)}), so the target was reached before a clean retest entry."
                ),
            )
            invalid.scenario = scenario
            invalid.direction = trade_direction
            invalid.htf_fvg = htf_fvg
            invalid.mtf_sweep = sweep
            invalid.ifvg = ifvg
            invalid.tp1 = tp1
            invalid.setup_timestamp = ifvg.disrespected_at_time
            return invalid

        risk = build_risk_plan(
            trade_df,
            daily,
            trade_direction,
            ifvg,
            sweep,
            self.left,
            self.right,
            disrespect_index,
            self.risk_settings,
        )

        if self.minimum_rr_enabled and (risk.risk_reward is None or risk.risk_reward < self.minimum_rr):
            invalid = self._invalid(
                symbol,
                timeframe_set,
                (
                    "No trade: risk/reward filter failed. "
                    f"Calculated RR is {'N/A' if risk.risk_reward is None else f'1:{risk.risk_reward:.2f}'}, "
                    f"minimum is 1:{self.minimum_rr:.2f}; entry={_fmt_price(risk.entry)}, "
                    f"SL={_fmt_price(risk.hard_sl)}, TP1={_fmt_price(risk.tp1)}."
                ),
            )
            self._attach_common(invalid, htf_fvg, sweep, ifvg, risk, trade_direction, scenario)
            invalid.setup_timestamp = ifvg.disrespected_at_time
            return invalid

        confluence_score, confluence_total, confluence_factors = self._build_confluence_score(
            trade_direction,
            risk.risk_reward,
            is_untapped_htf_fvg,
            htf_trend,
        )

        signal = StrategySignal(
            symbol=symbol,
            timeframe_set=timeframe_set.name,
            htf=timeframe_set.htf,
            mtf=timeframe_set.mtf,
            ltf=timeframe_set.ltf,
            signal=trade_direction,
            scenario=scenario,
            direction=trade_direction,
            htf_fvg=htf_fvg,
            mtf_sweep=sweep,
            ifvg=ifvg,
            entry_price=risk.entry,
            hard_stop_loss=risk.hard_sl,
            dynamic_stop_condition=(
                "Send management alert if a candle body closes back inside or beyond the IFVG zone."
            ),
            tp1=risk.tp1,
            tp2=risk.tp2,
            risk_reward=risk.risk_reward,
            confluence_score=confluence_score,
            confluence_total=confluence_total,
            confluence_factors=confluence_factors,
            reason=[
                f"HTF {htf_fvg.direction} FVG respected; directional bias locked to {trade_direction}.",
                "MTF liquidity sweep confirmed.",
                scenario_reason,
                f"Selected {ifvg_timeframe} FVG disrespected and converted into IFVG.",
                "Disrespect candle did not touch TP1.",
                "Waiting for IFVG retest.",
            ],
            timestamp=datetime.utcnow(),
            setup_timestamp=ifvg.disrespected_at_time,
        )
        return signal

    @staticmethod
    def _direction_from_htf_fvg(htf_fvg: FVG) -> str:
        return "BUY" if htf_fvg.direction == "bullish" else "SELL"

    def _fvg_size_pips(self, symbol: str, fvg: FVG) -> float:
        return abs(fvg.top - fvg.bottom) / self._pip_size(symbol)

    def _pip_size(self, symbol: str) -> float:
        configured = self.pip_size_setting
        if configured is not None and str(configured).lower() != "auto":
            return float(configured)

        normalized = "".join(character for character in symbol.upper() if character.isalnum())
        if normalized.startswith(("XAU", "XAG")):
            return 0.1

        fiat_codes = {
            "AUD",
            "CAD",
            "CHF",
            "EUR",
            "GBP",
            "JPY",
            "NZD",
            "USD",
        }
        base = normalized[:3]
        quote = normalized[3:6]
        is_standard_forex_pair = base in fiat_codes and quote in fiat_codes
        if is_standard_forex_pair and quote == "JPY":
            return 0.01
        if is_standard_forex_pair:
            return 0.0001

        return 1.0

    @staticmethod
    def _is_untapped_htf_fvg(htf_df: pd.DataFrame, htf_fvg: FVG, touch_index: int | None) -> bool:
        if touch_index is None:
            return False
        first_touch = htf_fvg.metadata.get("first_touched_index")
        if first_touch is None:
            first_touch = first_zone_touch_index(htf_df, htf_fvg, before_index=touch_index + 1)
        return first_touch == touch_index

    def _trend_direction(self, htf_df: pd.DataFrame) -> str | None:
        if not self.trend_filter_enabled:
            return None
        if htf_df.empty or len(htf_df) < self.trend_slow_period:
            return None

        close = htf_df["close"].astype(float)
        fast_ema = close.ewm(span=self.trend_fast_period, adjust=False).mean().iloc[-1]
        slow_ema = close.ewm(span=self.trend_slow_period, adjust=False).mean().iloc[-1]

        if fast_ema > slow_ema:
            return "BUY"
        if fast_ema < slow_ema:
            return "SELL"
        return None

    def _build_confluence_score(
        self,
        trade_direction: str,
        risk_reward: float | None,
        is_untapped_htf_fvg: bool,
        htf_trend: str | None,
    ) -> tuple[int, int, list[str]]:
        total = 1
        factors: list[str] = []

        if risk_reward is not None and risk_reward >= self.high_rr_threshold:
            factors.append("High Risk/Reward")

        if self.untapped_htf_fvg_mode not in {"off", "false", "disabled"}:
            total += 1
            if is_untapped_htf_fvg:
                factors.append("Untapped HTF FVG")

        if self.trend_filter_enabled:
            total += 1
            if htf_trend == trade_direction:
                factors.append("HTF Trend Aligned")

        return len(factors), total, factors

    def _attach_common(
        self,
        signal: StrategySignal,
        htf_fvg: FVG,
        sweep: LiquiditySweep,
        ifvg: FVG,
        risk: RiskPlan,
        direction: str,
        scenario: str,
    ) -> None:
        signal.htf_fvg = htf_fvg
        signal.mtf_sweep = sweep
        signal.ifvg = ifvg
        signal.direction = direction
        signal.scenario = scenario
        signal.entry_price = risk.entry
        signal.hard_stop_loss = risk.hard_sl
        signal.tp1 = risk.tp1
        signal.tp2 = risk.tp2
        signal.risk_reward = risk.risk_reward
        signal.dynamic_stop_condition = "Candle body closes back inside or beyond the IFVG zone."

    @staticmethod
    def _wait(symbol: str, timeframe_set: TimeframeSet, reason: str) -> StrategySignal:
        return StrategySignal(
            symbol=symbol,
            timeframe_set=timeframe_set.name,
            htf=timeframe_set.htf,
            mtf=timeframe_set.mtf,
            ltf=timeframe_set.ltf,
            signal="WAIT",
            scenario="Pending",
            reason=[reason],
        )

    @staticmethod
    def _invalid(symbol: str, timeframe_set: TimeframeSet, reason: str) -> StrategySignal:
        return StrategySignal(
            symbol=symbol,
            timeframe_set=timeframe_set.name,
            htf=timeframe_set.htf,
            mtf=timeframe_set.mtf,
            ltf=timeframe_set.ltf,
            signal="INVALIDATED",
            scenario="Rejected",
            invalidation_reason=reason,
            reason=[reason],
        )
