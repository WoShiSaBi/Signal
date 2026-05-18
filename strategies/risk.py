from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.fvg import FVG
from strategies.liquidity import LiquiditySweep
from strategies.pivots import nearest_pivot_above, nearest_pivot_below


@dataclass(frozen=True)
class RiskPlan:
    entry: float
    hard_sl: float
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    management: str


def entry_from_ifvg(ifvg: FVG, direction: str) -> float:
    if direction == "BUY":
        return float(ifvg.top)
    return float(ifvg.bottom)


def hard_stop_from_sweep(sweep: LiquiditySweep, direction: str) -> float:
    return sweep.sweep_low if direction == "BUY" else sweep.sweep_high


def previous_day_extreme(daily_df: pd.DataFrame, direction: str, entry: float) -> float | None:
    if daily_df.empty or len(daily_df) < 2:
        return None

    ordered = daily_df.sort_values("time").reset_index(drop=True)
    previous_day = ordered.iloc[-2]

    if direction == "BUY":
        high = float(previous_day["high"])
        return high if high > entry else None

    low = float(previous_day["low"])
    return low if low < entry else None


def nearest_liquidity_target(
    df: pd.DataFrame,
    direction: str,
    entry: float,
    left_bars: int,
    right_bars: int,
    before_index: int | None = None,
) -> float | None:
    pivot = (
        nearest_pivot_above(df, entry, left_bars, right_bars, before_index)
        if direction == "BUY"
        else nearest_pivot_below(df, entry, left_bars, right_bars, before_index)
    )
    return pivot.price if pivot else None


def calculate_rr(direction: str, entry: float, stop: float, target: float | None) -> float | None:
    if target is None:
        return None

    risk = abs(entry - stop)
    reward = (target - entry) if direction == "BUY" else (entry - target)
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def disrespect_candle_touches_target(candle: pd.Series, direction: str, target: float | None) -> bool:
    if target is None:
        return False
    if direction == "BUY":
        return float(candle["high"]) >= target
    return float(candle["low"]) <= target


def build_risk_plan(
    trade_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    direction: str,
    ifvg: FVG,
    sweep: LiquiditySweep,
    left_bars: int,
    right_bars: int,
    disrespect_index: int | None,
) -> RiskPlan:
    entry = entry_from_ifvg(ifvg, direction)
    hard_sl = hard_stop_from_sweep(sweep, direction)
    tp1 = nearest_liquidity_target(
        trade_df,
        direction,
        entry,
        left_bars,
        right_bars,
        before_index=disrespect_index,
    )
    tp2 = previous_day_extreme(daily_df, direction, entry)
    rr = calculate_rr(direction, entry, hard_sl, tp1)
    management = (
        "Close 50% at TP1, move SL to breakeven after TP1, close remaining 50% at TP2."
        if tp2 is not None
        else "Close 100% at TP1."
    )
    return RiskPlan(entry=entry, hard_sl=hard_sl, tp1=tp1, tp2=tp2, risk_reward=rr, management=management)
