from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd


TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
}


class MT5DataProvider:
    def __init__(
        self,
        logger: logging.Logger | None = None,
        symbol_aliases: dict[str, list[str]] | None = None,
        log_fetches: bool = True,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.symbol_aliases = symbol_aliases or {}
        self.log_fetches = log_fetches
        self.resolved_symbols: dict[str, str] = {}
        self.unavailable_symbols: set[str] = set()
        self.mt5 = None
        self.connected = False

    def connect(self) -> bool:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            self.logger.error("MetaTrader5 package is not installed. Use CSV mode or install requirements.")
            return False

        self.mt5 = mt5
        login = os.getenv("MT5_LOGIN")
        password = os.getenv("MT5_PASSWORD")
        server = os.getenv("MT5_SERVER")

        if login and password and server:
            initialized = mt5.initialize(login=int(login), password=password, server=server)
        else:
            initialized = mt5.initialize()

        if not initialized:
            self.logger.error("MT5 initialization failed: %s", mt5.last_error())
            return False

        self.connected = True
        self.logger.info("Connected to MetaTrader 5")
        return True

    def shutdown(self) -> None:
        if self.mt5 and self.connected:
            self.mt5.shutdown()
            self.connected = False

    def resolve_symbol(self, symbol: str) -> str | None:
        if not self.mt5:
            return None

        if symbol in self.resolved_symbols:
            return self.resolved_symbols[symbol]

        if symbol in self.unavailable_symbols:
            return None

        candidates = [symbol, *self.symbol_aliases.get(symbol, [])]
        for candidate in dict.fromkeys(candidates):
            info = self.mt5.symbol_info(candidate)
            if info is None:
                continue

            if not info.visible and not self.mt5.symbol_select(candidate, True):
                self.logger.error("MT5 symbol exists but could not be selected: %s", candidate)
                continue

            self.resolved_symbols[symbol] = candidate
            if candidate != symbol:
                self.logger.info("MT5 symbol resolved: %s -> %s", symbol, candidate)
            return candidate

        self.unavailable_symbols.add(symbol)
        self.logger.error(
            "MT5 symbol unavailable: %s. Tried: %s",
            symbol,
            ", ".join(candidates),
        )
        return None

    def fetch_rates(self, symbol: str, timeframe: str, count: int = 500) -> pd.DataFrame:
        if not self.mt5 or not self.connected:
            raise RuntimeError("MT5 is not connected.")

        mt5_symbol = self.resolve_symbol(symbol)
        if not mt5_symbol:
            return pd.DataFrame()

        mt5_timeframe_name = TIMEFRAME_MAP.get(timeframe.upper())
        if not mt5_timeframe_name:
            self.logger.error("Unsupported MT5 timeframe: %s", timeframe)
            return pd.DataFrame()

        mt5_timeframe = getattr(self.mt5, mt5_timeframe_name)
        rates = self.mt5.copy_rates_from_pos(mt5_symbol, mt5_timeframe, 0, count)
        if rates is None:
            self.logger.error("No MT5 data for %s %s: %s", mt5_symbol, timeframe, self.mt5.last_error())
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        if df.empty:
            return df

        df["time"] = pd.to_datetime(df["time"], unit="s")
        keep = ["time", "open", "high", "low", "close", "tick_volume"]
        if self.log_fetches:
            self.logger.info("Market data fetched from MT5: %s %s %s candles", mt5_symbol, timeframe, len(df))
        return df[[column for column in keep if column in df.columns]]


def utc_now() -> datetime:
    return datetime.utcnow()
