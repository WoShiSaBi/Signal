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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    setup_timestamp: pd.Timestamp | None = None

    def is_alertable(self) -> bool:
        return self.signal in {"BUY", "SELL", "INVALIDATED", "WAIT"}


class MTFFractalIFVGStrategy:
    def __init__(self, settings: dict, logger: logging.Logger | None = None) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self.left = int(settings.get("pivot_left_bars", 3))
        self.right = int(settings.get("pivot_right_bars", 3))
        self.merge_enabled = bool(settings.get("fvg_merge_enabled", True))
        self.override_lookback = int(settings.get("mtf_override_lookback_candles", 4))
        self.minimum_rr = float(settings.get("minimum_risk_reward", 2.0))

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
        if htf_fvg is None:
            latest_htf_fvg = find_latest_fvg_review(htf, self.merge_enabled)
            if latest_htf_fvg and latest_htf_fvg.status == "invalidated":
                self.logger.info(
                    "%s %s: HTF FVG invalidated: %s %.5f-%.5f",
                    symbol,
                    timeframe_set.name,
                    latest_htf_fvg.direction,
                    latest_htf_fvg.bottom,
                    latest_htf_fvg.top,
                )
            return self._wait(symbol, timeframe_set, "Waiting for a respected HTF FVG.")

        self.logger.info(
            "%s %s: HTF FVG detected and respected: %s %.5f-%.5f",
            symbol,
            timeframe_set.name,
            htf_fvg.direction,
            htf_fvg.bottom,
            htf_fvg.top,
        )

        htf_touch_time = pd.Timestamp(htf.iloc[htf_touch_index]["time"]) if htf_touch_index is not None else None
        mtf_start_index = None
        if htf_touch_time is not None:
            matching = mtf.index[pd.to_datetime(mtf["time"]) >= htf_touch_time].tolist()
            mtf_start_index = matching[0] if matching else None

        sweep = latest_sweep_after_index(mtf, mtf_start_index, self.left, self.right)
        if sweep is None:
            signal = self._wait(symbol, timeframe_set, "HTF FVG respected. Waiting for MTF liquidity sweep.")
            signal.htf_fvg = htf_fvg
            return signal

        self.logger.info(
            "%s %s: MTF liquidity sweep detected: %s level %.5f at %s",
            symbol,
            timeframe_set.name,
            sweep.direction,
            sweep.swept_level,
            sweep.timestamp,
        )

        trade_direction = sweep.signal_direction
        required_fvg_direction = "bearish" if trade_direction == "BUY" else "bullish"

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
            signal = self._wait(symbol, timeframe_set, "Waiting for a qualifying entry FVG.")
            signal.htf_fvg = htf_fvg
            signal.mtf_sweep = sweep
            signal.direction = trade_direction
            signal.scenario = scenario
            return signal

        if ifvg is None or disrespect_index is None:
            signal = self._wait(symbol, timeframe_set, "Entry FVG found. Waiting for IFVG disrespect confirmation.")
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

        provisional_entry = entry_from_ifvg(ifvg, trade_direction)
        tp1 = nearest_liquidity_target(
            trade_df,
            trade_direction,
            provisional_entry,
            self.left,
            self.right,
            before_index=disrespect_index,
        )
        disrespect_candle = trade_df.iloc[disrespect_index]

        if disrespect_candle_touches_target(disrespect_candle, trade_direction, tp1):
            invalid = self._invalid(
                symbol,
                timeframe_set,
                "Scenario 3 invalidation: disrespect candle already touched nearest liquidity TP1.",
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
        )

        if risk.risk_reward is None or risk.risk_reward < self.minimum_rr:
            invalid = self._invalid(
                symbol,
                timeframe_set,
                f"Risk/reward below minimum {self.minimum_rr:.2f}.",
            )
            self._attach_common(invalid, htf_fvg, sweep, ifvg, risk, trade_direction, scenario)
            invalid.setup_timestamp = ifvg.disrespected_at_time
            return invalid

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
            reason=[
                "HTF FVG respected.",
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
