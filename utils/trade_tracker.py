from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
import json
from pathlib import Path

import pandas as pd

from strategies.mtf_fractal_ifvg import StrategySignal


def _fmt_price(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.5f}"


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _code(value: object) -> str:
    return f"<code>{_html(value)}</code>"


def _signal_key(signal: StrategySignal) -> str:
    ifvg_zone = signal.ifvg.key_zone() if signal.ifvg else "no-ifvg"
    entry = _fmt_price(signal.entry_price) if signal.entry_price is not None else "no-entry"
    setup_time = str(signal.setup_timestamp or signal.timestamp)
    direction = signal.direction or signal.signal
    return "|".join(
        [
            signal.symbol,
            signal.timeframe_set,
            direction,
            signal.scenario,
            entry,
            ifvg_zone,
            setup_time,
        ]
    )


@dataclass
class TradeOutcome:
    trade: "TrackedTrade"
    target_name: str
    target_price: float
    hit_time: pd.Timestamp
    candle_high: float
    candle_low: float


@dataclass
class TrackedTrade:
    key: str
    symbol: str
    timeframe_set: str
    direction: str
    scenario: str
    entry: float
    stop_loss: float | None
    tp1: float | None
    tp2: float | None
    risk_reward: float | None
    opened_at: datetime
    setup_timestamp: pd.Timestamp | None
    telegram_message_id: int | None = None
    entry_filled: bool = False
    entry_filled_at: pd.Timestamp | None = None
    tp1_hit: bool = False
    tp2_hit: bool = False
    stop_loss_hit: bool = False


@dataclass
class TradeStats:
    tracked_trades: int = 0
    entry_fills: int = 0
    tp1_hits: int = 0
    tp2_hits: int = 0
    stop_loss_hits: int = 0


@dataclass
class TradeTracker:
    active_trades: dict[str, TrackedTrade] = field(default_factory=dict)
    stats: TradeStats = field(default_factory=TradeStats)
    state_path: Path = field(default_factory=lambda: Path("logs/trade_tracker_state.json"))

    def __post_init__(self) -> None:
        self.load()

    def track_signal(self, signal: StrategySignal, telegram_message_id: int | None = None) -> bool:
        if signal.signal not in {"BUY", "SELL"}:
            return False
        if signal.entry_price is None or signal.tp1 is None:
            return False

        key = _signal_key(signal)
        if key in self.active_trades:
            return False

        self.active_trades[key] = TrackedTrade(
            key=key,
            symbol=signal.symbol,
            timeframe_set=signal.timeframe_set,
            direction=signal.signal,
            scenario=signal.scenario,
            entry=signal.entry_price,
            stop_loss=signal.hard_stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            risk_reward=signal.risk_reward,
            opened_at=datetime.utcnow(),
            setup_timestamp=signal.setup_timestamp,
            telegram_message_id=telegram_message_id,
        )
        self.stats.tracked_trades += 1
        self.save()
        return True

    def check_outcomes(self, symbol: str, timeframe_set: str, candles: pd.DataFrame) -> list[TradeOutcome]:
        if candles.empty:
            return []

        outcomes: list[TradeOutcome] = []
        for trade in list(self.active_trades.values()):
            if trade.symbol != symbol or trade.timeframe_set != timeframe_set:
                continue
            outcomes.extend(self._check_trade(trade, candles))
        return outcomes

    def _check_trade(self, trade: TrackedTrade, candles: pd.DataFrame) -> list[TradeOutcome]:
        outcomes: list[TradeOutcome] = []
        if trade.stop_loss_hit or (trade.tp1_hit and (trade.tp2 is None or trade.tp2_hit)):
            return outcomes

        scan = candles.copy()
        scan["time"] = pd.to_datetime(scan["time"])
        if trade.setup_timestamp is not None:
            scan = scan[scan["time"] >= pd.Timestamp(trade.setup_timestamp)]

        for _, candle in scan.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            candle_time = pd.Timestamp(candle["time"])

            if not trade.entry_filled and self._price_touched(trade.direction, trade.entry, high, low, "entry"):
                trade.entry_filled = True
                trade.entry_filled_at = candle_time
                self.stats.entry_fills += 1
                self.save()

            if not trade.entry_filled:
                continue

            if trade.stop_loss is not None and not trade.stop_loss_hit and self._price_touched(
                trade.direction,
                trade.stop_loss,
                high,
                low,
                "stop_loss",
            ):
                trade.stop_loss_hit = True
                self.stats.stop_loss_hits += 1
                outcomes.append(TradeOutcome(trade, "STOP LOSS", trade.stop_loss, candle_time, high, low))
                self.active_trades.pop(trade.key, None)
                self.save()
                break

            if trade.tp1 is not None and not trade.tp1_hit and self._price_touched(
                trade.direction,
                trade.tp1,
                high,
                low,
                "target",
            ):
                trade.tp1_hit = True
                self.stats.tp1_hits += 1
                outcomes.append(TradeOutcome(trade, "TP1", trade.tp1, candle_time, high, low))
                self.save()

            if trade.tp2 is not None and not trade.tp2_hit and self._price_touched(
                trade.direction,
                trade.tp2,
                high,
                low,
                "target",
            ):
                trade.tp2_hit = True
                self.stats.tp2_hits += 1
                outcomes.append(TradeOutcome(trade, "TP2", trade.tp2, candle_time, high, low))
                self.save()

            if trade.tp1_hit and (trade.tp2 is None or trade.tp2_hit):
                self.active_trades.pop(trade.key, None)
                self.save()
                break

        return outcomes

    def load(self) -> None:
        if not self.state_path.exists():
            return

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        stats = data.get("stats", {})
        self.stats = TradeStats(
            tracked_trades=int(stats.get("tracked_trades", 0)),
            entry_fills=int(stats.get("entry_fills", 0)),
            tp1_hits=int(stats.get("tp1_hits", 0)),
            tp2_hits=int(stats.get("tp2_hits", 0)),
            stop_loss_hits=int(stats.get("stop_loss_hits", 0)),
        )

        active: dict[str, TrackedTrade] = {}
        for key, item in data.get("active_trades", {}).items():
            setup_timestamp = item.get("setup_timestamp")
            entry_filled_at = item.get("entry_filled_at")
            active[key] = TrackedTrade(
                key=key,
                symbol=str(item["symbol"]),
                timeframe_set=str(item["timeframe_set"]),
                direction=str(item["direction"]),
                scenario=str(item["scenario"]),
                entry=float(item["entry"]),
                stop_loss=self._optional_float(item.get("stop_loss")),
                tp1=self._optional_float(item.get("tp1")),
                tp2=self._optional_float(item.get("tp2")),
                risk_reward=self._optional_float(item.get("risk_reward")),
                opened_at=datetime.fromisoformat(item["opened_at"]),
                setup_timestamp=pd.Timestamp(setup_timestamp) if setup_timestamp else None,
                telegram_message_id=self._optional_int(item.get("telegram_message_id")),
                entry_filled=bool(item.get("entry_filled", False)),
                entry_filled_at=pd.Timestamp(entry_filled_at) if entry_filled_at else None,
                tp1_hit=bool(item.get("tp1_hit", False)),
                tp2_hit=bool(item.get("tp2_hit", False)),
                stop_loss_hit=bool(item.get("stop_loss_hit", False)),
            )
        self.active_trades = active

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "stats": {
                "tracked_trades": self.stats.tracked_trades,
                "entry_fills": self.stats.entry_fills,
                "tp1_hits": self.stats.tp1_hits,
                "tp2_hits": self.stats.tp2_hits,
                "stop_loss_hits": self.stats.stop_loss_hits,
            },
            "active_trades": {
                key: {
                    "symbol": trade.symbol,
                    "timeframe_set": trade.timeframe_set,
                    "direction": trade.direction,
                    "scenario": trade.scenario,
                    "entry": trade.entry,
                    "stop_loss": trade.stop_loss,
                    "tp1": trade.tp1,
                    "tp2": trade.tp2,
                    "risk_reward": trade.risk_reward,
                    "opened_at": trade.opened_at.isoformat(),
                    "setup_timestamp": str(trade.setup_timestamp) if trade.setup_timestamp is not None else None,
                    "telegram_message_id": trade.telegram_message_id,
                    "entry_filled": trade.entry_filled,
                    "entry_filled_at": str(trade.entry_filled_at) if trade.entry_filled_at is not None else None,
                    "tp1_hit": trade.tp1_hit,
                    "tp2_hit": trade.tp2_hit,
                    "stop_loss_hit": trade.stop_loss_hit,
                }
                for key, trade in self.active_trades.items()
            },
        }
        self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _optional_float(value: object) -> float | None:
        return None if value is None else float(value)

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return None if value is None else int(value)

    @staticmethod
    def _price_touched(direction: str, price: float, high: float, low: float, kind: str) -> bool:
        if kind == "entry":
            return low <= price if direction == "BUY" else high >= price
        if kind == "stop_loss":
            return low <= price if direction == "BUY" else high >= price
        return high >= price if direction == "BUY" else low <= price


def format_trade_outcome_message(outcome: TradeOutcome) -> str:
    trade = outcome.trade
    rr = "N/A" if trade.risk_reward is None else f"1:{trade.risk_reward:.2f}"
    is_stop_loss = outcome.target_name == "STOP LOSS"
    title = "STOP LOSS HIT" if is_stop_loss else f"{outcome.target_name} HIT"
    status = "Trade invalidated at hard stop." if is_stop_loss else "Target reached."
    outcome_label = "Stop Loss" if is_stop_loss else outcome.target_name

    return f"""<b>{_html(title)}</b>
<b>{_html(trade.symbol)}</b> | <b>{_html(trade.direction)}</b> | {_code(trade.timeframe_set)}

<b>Status:</b> {_html(status)}
<b>Scenario:</b> {_code(trade.scenario)}

<b>Levels</b>
Entry: {_code(_fmt_price(trade.entry))}
Hard SL: {_code(_fmt_price(trade.stop_loss))}
TP1: {_code(_fmt_price(trade.tp1))}
TP2: {_code(_fmt_price(trade.tp2))}
Risk/Reward: {_code(rr)}

<b>Hit Details</b>
{_html(outcome_label)}: {_code(_fmt_price(outcome.target_price))}
Entry Filled: {_code(trade.entry_filled_at or "N/A")}
Hit Time: {_code(outcome.hit_time)}
Candle High: {_code(_fmt_price(outcome.candle_high))}
Candle Low: {_code(_fmt_price(outcome.candle_low))}
"""
