from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.fvg import FVG
from strategies.liquidity import LiquiditySweep
from strategies.pivots import find_pivots, nearest_pivot_above, nearest_pivot_below


@dataclass(frozen=True)
class RiskPlan:
    entry: float
    hard_sl: float
    stop_loss_pips: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    management: str
    lot_sizes: dict[str, float]
    stop_loss_mode: str


def entry_from_ifvg(ifvg: FVG, direction: str, boundary_mode: str = "support_resistance") -> float:
    if boundary_mode == "midpoint":
        return (float(ifvg.top) + float(ifvg.bottom)) / 2

    if boundary_mode == "opposite_boundary":
        return float(ifvg.bottom) if direction == "BUY" else float(ifvg.top)

    if direction == "BUY":
        return float(ifvg.top)
    return float(ifvg.bottom)


def hard_stop_from_sweep(sweep: LiquiditySweep, direction: str) -> float:
    return sweep.sweep_low if direction == "BUY" else sweep.sweep_high


def fixed_pip_stop(entry: float, direction: str, pip_size: float, pips: float = 100) -> float:
    distance = pip_size * pips
    return entry - distance if direction == "BUY" else entry + distance


def nearest_opposite_liquidity_stop(
    df: pd.DataFrame,
    direction: str,
    entry: float,
    left_bars: int,
    right_bars: int,
    before_index: int | None = None,
) -> float | None:
    pivots = find_pivots(df, left_bars, right_bars)
    candidates = [
        pivot
        for pivot in pivots
        if (before_index is None or pivot.index < before_index)
        and (
            (direction == "BUY" and pivot.kind == "low" and pivot.price < entry)
            or (direction == "SELL" and pivot.kind == "high" and pivot.price > entry)
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pivot: pivot.index).price


def choose_stop_loss(
    entry: float,
    direction: str,
    swing_stop: float | None,
    fixed_stop: float,
    mode: str,
) -> tuple[float, str]:
    normalized_mode = mode.lower()
    if normalized_mode == "fixed_100":
        return fixed_stop, "fixed_100"
    if normalized_mode == "swing_only" and swing_stop is not None:
        return swing_stop, "swing_only"
    if normalized_mode == "whichever_is_tighter" and swing_stop is not None:
        swing_risk = abs(entry - swing_stop)
        fixed_risk = abs(entry - fixed_stop)
        return (swing_stop, "swing_only") if swing_risk <= fixed_risk else (fixed_stop, "fixed_100")
    return fixed_stop, "fixed_100"


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


def stop_loss_distance_pips(entry: float, stop: float, pip_size: float) -> float | None:
    if pip_size <= 0:
        return None
    distance = abs(entry - stop) / pip_size
    return distance if distance > 0 else None


def calculate_lot_sizes(
    stop_loss_pips: float | None,
    pip_value_per_lot: float,
    balances: list[float],
    risk_percent_per_trade: float = 1.0,
) -> dict[str, float]:
    if stop_loss_pips is None or stop_loss_pips <= 0 or pip_value_per_lot <= 0:
        return {f"{int(balance / 1000)}k": 0.0 for balance in balances}

    lots: dict[str, float] = {}
    for balance in balances:
        risk_amount = balance * (risk_percent_per_trade / 100)
        lots[f"{int(balance / 1000)}k"] = risk_amount / (stop_loss_pips * pip_value_per_lot)
    return lots


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
    pip_size: float,
    risk_settings: dict | None = None,
) -> RiskPlan:
    risk_settings = risk_settings or {}
    entry_boundary = str(risk_settings.get("entry_boundary", "support_resistance"))
    tp2_settings = risk_settings.get("tp2", {})
    if not isinstance(tp2_settings, dict):
        tp2_settings = {}

    entry = entry_from_ifvg(ifvg, direction, entry_boundary)
    sl_mode = str(risk_settings.get("sl_mode", "swing_only"))
    fixed_stop_pips = float(risk_settings.get("fixed_stop_pips", 100))
    fixed_stop = fixed_pip_stop(entry, direction, pip_size, fixed_stop_pips)
    swing_stop = nearest_opposite_liquidity_stop(
        trade_df,
        direction,
        entry,
        left_bars,
        right_bars,
        before_index=disrespect_index,
    )
    if swing_stop is None:
        swing_stop = hard_stop_from_sweep(sweep, direction)
    hard_sl, chosen_sl_mode = choose_stop_loss(entry, direction, swing_stop, fixed_stop, sl_mode)
    tp1 = nearest_liquidity_target(
        trade_df,
        direction,
        entry,
        left_bars,
        right_bars,
        before_index=disrespect_index,
    )
    tp2_enabled = bool(tp2_settings.get("enabled", True))
    tp2_source = str(tp2_settings.get("source", "previous_day"))
    tp2 = previous_day_extreme(daily_df, direction, entry) if tp2_enabled and tp2_source == "previous_day" else None
    sl_pips = stop_loss_distance_pips(entry, hard_sl, pip_size)
    rr = calculate_rr(direction, entry, hard_sl, tp1)
    pip_value_per_lot = float(risk_settings.get("pip_value_per_lot", 10))
    balances = [float(item) for item in risk_settings.get("lot_size_balances", [10000, 50000, 100000])]
    risk_percent_per_trade = float(
        risk_settings.get("risk_percent_per_trade", risk_settings.get("risk_percent", 1.0))
    )
    lot_sizes = calculate_lot_sizes(sl_pips, pip_value_per_lot, balances, risk_percent_per_trade)
    management = (
        "Close 50% at TP1, move SL to breakeven after TP1, close remaining 50% at TP2."
        if tp2 is not None
        else "Close 100% at TP1."
    )
    return RiskPlan(
        entry=entry,
        hard_sl=hard_sl,
        stop_loss_pips=sl_pips,
        tp1=tp1,
        tp2=tp2,
        risk_reward=rr,
        management=management,
        lot_sizes=lot_sizes,
        stop_loss_mode=chosen_sl_mode,
    )
