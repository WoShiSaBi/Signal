from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Pivot:
    index: int
    timestamp: pd.Timestamp
    price: float
    kind: str  # "high" or "low"


def normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {', '.join(sorted(missing))}")

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"])
    for column in ["open", "high", "low", "close"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["time", "open", "high", "low", "close"])
    out = out.sort_values("time").reset_index(drop=True)
    return out


def find_pivots(df: pd.DataFrame, left_bars: int = 3, right_bars: int = 3) -> list[Pivot]:
    pivots: list[Pivot] = []
    if df.empty or len(df) < left_bars + right_bars + 1:
        return pivots

    for index in range(left_bars, len(df) - right_bars):
        window = df.iloc[index - left_bars : index + right_bars + 1]
        candle = df.iloc[index]

        if candle["high"] == window["high"].max() and window["high"].tolist().count(candle["high"]) == 1:
            pivots.append(
                Pivot(
                    index=index,
                    timestamp=pd.Timestamp(candle["time"]),
                    price=float(candle["high"]),
                    kind="high",
                )
            )

        if candle["low"] == window["low"].min() and window["low"].tolist().count(candle["low"]) == 1:
            pivots.append(
                Pivot(
                    index=index,
                    timestamp=pd.Timestamp(candle["time"]),
                    price=float(candle["low"]),
                    kind="low",
                )
            )

    return pivots


def nearest_pivot_above(
    df: pd.DataFrame,
    price: float,
    left_bars: int = 3,
    right_bars: int = 3,
    before_index: int | None = None,
) -> Pivot | None:
    pivots = find_pivots(df, left_bars, right_bars)
    candidates = [
        pivot
        for pivot in pivots
        if pivot.kind == "high"
        and pivot.price > price
        and (before_index is None or pivot.index < before_index)
    ]
    return min(candidates, key=lambda pivot: pivot.price - price) if candidates else None


def nearest_pivot_below(
    df: pd.DataFrame,
    price: float,
    left_bars: int = 3,
    right_bars: int = 3,
    before_index: int | None = None,
) -> Pivot | None:
    pivots = find_pivots(df, left_bars, right_bars)
    candidates = [
        pivot
        for pivot in pivots
        if pivot.kind == "low"
        and pivot.price < price
        and (before_index is None or pivot.index < before_index)
    ]
    return min(candidates, key=lambda pivot: price - pivot.price) if candidates else None
