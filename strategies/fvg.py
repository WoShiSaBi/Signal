from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FVG:
    direction: str  # "bullish" or "bearish"
    top: float
    bottom: float
    candle_indexes: list[int]
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    status: str = "open"  # open, respected, disrespected, invalidated, ifvg
    disrespected_at_index: int | None = None
    disrespected_at_time: pd.Timestamp | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def zone(self) -> tuple[float, float]:
        return (self.bottom, self.top)

    def key_zone(self) -> str:
        return f"{self.bottom:.5f}-{self.top:.5f}"


def detect_fvgs(df: pd.DataFrame) -> list[FVG]:
    fvgs: list[FVG] = []
    if len(df) < 3:
        return fvgs

    for index in range(2, len(df)):
        candle_1 = df.iloc[index - 2]
        candle_3 = df.iloc[index]

        if float(candle_3["low"]) > float(candle_1["high"]):
            bottom = float(candle_1["high"])
            top = float(candle_3["low"])
            fvgs.append(
                FVG(
                    direction="bullish",
                    top=top,
                    bottom=bottom,
                    candle_indexes=[index - 2, index - 1, index],
                    start_time=pd.Timestamp(candle_1["time"]),
                    end_time=pd.Timestamp(candle_3["time"]),
                )
            )

        if float(candle_3["high"]) < float(candle_1["low"]):
            bottom = float(candle_3["high"])
            top = float(candle_1["low"])
            fvgs.append(
                FVG(
                    direction="bearish",
                    top=top,
                    bottom=bottom,
                    candle_indexes=[index - 2, index - 1, index],
                    start_time=pd.Timestamp(candle_1["time"]),
                    end_time=pd.Timestamp(candle_3["time"]),
                )
            )

    return fvgs


def merge_consecutive_fvgs(fvgs: list[FVG]) -> list[FVG]:
    if not fvgs:
        return []

    sorted_fvgs = sorted(fvgs, key=lambda item: item.candle_indexes[0])
    merged: list[FVG] = []
    current = sorted_fvgs[0]

    for next_fvg in sorted_fvgs[1:]:
        current_last = max(current.candle_indexes)
        next_first = min(next_fvg.candle_indexes)
        is_consecutive = next_first <= current_last + 1

        if next_fvg.direction == current.direction and is_consecutive:
            current.top = max(current.top, next_fvg.top)
            current.bottom = min(current.bottom, next_fvg.bottom)
            current.candle_indexes = sorted(set(current.candle_indexes + next_fvg.candle_indexes))
            current.end_time = max(current.end_time, next_fvg.end_time)
            current.metadata["merged_count"] = int(current.metadata.get("merged_count", 1)) + 1
        else:
            merged.append(current)
            current = next_fvg

    merged.append(current)
    return merged


def price_enters_zone(candle: pd.Series, fvg: FVG) -> bool:
    return float(candle["low"]) <= fvg.top and float(candle["high"]) >= fvg.bottom


def candle_closes_beyond_zone(candle: pd.Series, fvg: FVG) -> bool:
    close = float(candle["close"])
    if fvg.direction == "bullish":
        return close < fvg.bottom
    return close > fvg.top


def is_zone_respected_after_touch(df: pd.DataFrame, fvg: FVG) -> tuple[bool, int | None]:
    start = max(fvg.candle_indexes) + 1
    touched_index: int | None = None

    for index in range(start, len(df)):
        candle = df.iloc[index]
        if price_enters_zone(candle, fvg):
            touched_index = index
        if candle_closes_beyond_zone(candle, fvg):
            fvg.status = "invalidated"
            return False, touched_index

    if touched_index is not None:
        fvg.status = "respected"
        return True, touched_index

    return False, None


def find_latest_respected_fvg(df: pd.DataFrame, merge_enabled: bool = True) -> tuple[FVG | None, int | None]:
    fvgs = detect_fvgs(df)
    if merge_enabled:
        fvgs = merge_consecutive_fvgs(fvgs)

    for fvg in reversed(fvgs):
        respected, touched_index = is_zone_respected_after_touch(df, fvg)
        if respected:
            return fvg, touched_index
    return None, None


def find_latest_fvg_review(df: pd.DataFrame, merge_enabled: bool = True) -> FVG | None:
    fvgs = detect_fvgs(df)
    if merge_enabled:
        fvgs = merge_consecutive_fvgs(fvgs)

    if not fvgs:
        return None

    latest = fvgs[-1]
    is_zone_respected_after_touch(df, latest)
    return latest


def find_fvgs_before_index(
    df: pd.DataFrame,
    end_index: int,
    lookback_candles: int,
    direction: str | None = None,
    merge_enabled: bool = True,
) -> list[FVG]:
    start_index = max(0, end_index - lookback_candles - 2)
    window = df.iloc[start_index : end_index + 1].copy().reset_index(drop=True)
    fvgs = detect_fvgs(window)

    for item in fvgs:
        item.candle_indexes = [index + start_index for index in item.candle_indexes]
        item.start_time = pd.Timestamp(df.iloc[item.candle_indexes[0]]["time"])
        item.end_time = pd.Timestamp(df.iloc[item.candle_indexes[-1]]["time"])

    if direction:
        fvgs = [item for item in fvgs if item.direction == direction]

    return merge_consecutive_fvgs(fvgs) if merge_enabled else fvgs


def find_fvgs_from_time(
    df: pd.DataFrame,
    start_time: pd.Timestamp,
    direction: str | None = None,
    merge_enabled: bool = True,
) -> list[FVG]:
    if df.empty:
        return []

    start_positions = df.index[pd.to_datetime(df["time"]) >= start_time].tolist()
    if not start_positions:
        return []

    start_index = max(0, start_positions[0] - 2)
    window = df.iloc[start_index:].copy().reset_index(drop=True)
    fvgs = detect_fvgs(window)

    adjusted: list[FVG] = []
    for item in fvgs:
        item.candle_indexes = [index + start_index for index in item.candle_indexes]
        item.start_time = pd.Timestamp(df.iloc[item.candle_indexes[0]]["time"])
        item.end_time = pd.Timestamp(df.iloc[item.candle_indexes[-1]]["time"])
        if item.end_time >= start_time and (direction is None or item.direction == direction):
            adjusted.append(item)

    return merge_consecutive_fvgs(adjusted) if merge_enabled else adjusted


def find_ifvg_disrespect(df: pd.DataFrame, fvg: FVG, expected_signal: str) -> tuple[FVG | None, int | None]:
    start = max(fvg.candle_indexes) + 1
    for index in range(start, len(df)):
        candle = df.iloc[index]
        close = float(candle["close"])
        converted = (
            expected_signal == "BUY"
            and fvg.direction == "bearish"
            and close > fvg.top
        ) or (
            expected_signal == "SELL"
            and fvg.direction == "bullish"
            and close < fvg.bottom
        )

        if converted:
            fvg.status = "ifvg"
            fvg.disrespected_at_index = index
            fvg.disrespected_at_time = pd.Timestamp(candle["time"])
            return fvg, index

    return None, None
