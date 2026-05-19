from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.pivots import Pivot, find_pivots


@dataclass(frozen=True)
class LiquiditySweep:
    direction: str  # "bullish" reversal sweep or "bearish" reversal sweep
    swept_level: float
    sweep_high: float
    sweep_low: float
    sweep_open: float
    sweep_close: float
    timestamp: pd.Timestamp
    candle_index: int
    pivot: Pivot

    @property
    def signal_direction(self) -> str:
        return "BUY" if self.direction == "bullish" else "SELL"


def detect_liquidity_sweeps(
    df: pd.DataFrame,
    left_bars: int = 3,
    right_bars: int = 3,
) -> list[LiquiditySweep]:
    pivots = find_pivots(df, left_bars, right_bars)
    sweeps: list[LiquiditySweep] = []

    for index in range(left_bars + right_bars + 1, len(df)):
        candle = df.iloc[index]
        previous_pivots = [pivot for pivot in pivots if pivot.index < index]

        lows = sorted(
            [pivot for pivot in previous_pivots if pivot.kind == "low"],
            key=lambda pivot: index - pivot.index,
        )
        highs = sorted(
            [pivot for pivot in previous_pivots if pivot.kind == "high"],
            key=lambda pivot: index - pivot.index,
        )

        for pivot in lows:
            if float(candle["low"]) < pivot.price and float(candle["close"]) > pivot.price:
                sweeps.append(
                    LiquiditySweep(
                        direction="bullish",
                        swept_level=pivot.price,
                        sweep_high=float(candle["high"]),
                        sweep_low=float(candle["low"]),
                        sweep_open=float(candle["open"]),
                        sweep_close=float(candle["close"]),
                        timestamp=pd.Timestamp(candle["time"]),
                        candle_index=index,
                        pivot=pivot,
                    )
                )
                break

        for pivot in highs:
            if float(candle["high"]) > pivot.price and float(candle["close"]) < pivot.price:
                sweeps.append(
                    LiquiditySweep(
                        direction="bearish",
                        swept_level=pivot.price,
                        sweep_high=float(candle["high"]),
                        sweep_low=float(candle["low"]),
                        sweep_open=float(candle["open"]),
                        sweep_close=float(candle["close"]),
                        timestamp=pd.Timestamp(candle["time"]),
                        candle_index=index,
                        pivot=pivot,
                    )
                )
                break

    return sweeps


def latest_sweep_after_index(
    df: pd.DataFrame,
    start_index: int | None,
    left_bars: int = 3,
    right_bars: int = 3,
    signal_direction: str | None = None,
) -> LiquiditySweep | None:
    sweeps = detect_liquidity_sweeps(df, left_bars, right_bars)
    if start_index is not None:
        sweeps = [sweep for sweep in sweeps if sweep.candle_index >= start_index]
    if signal_direction:
        sweeps = [sweep for sweep in sweeps if sweep.signal_direction == signal_direction]
    return sweeps[-1] if sweeps else None
