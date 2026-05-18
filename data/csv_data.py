from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd


class CSVDataProvider:
    def __init__(
        self,
        csv_folder: str = "sample_data",
        logger: logging.Logger | None = None,
        log_fetches: bool = True,
    ) -> None:
        self.csv_folder = Path(csv_folder)
        self.logger = logger or logging.getLogger(__name__)
        self.log_fetches = log_fetches

    def fetch_rates(self, symbol: str, timeframe: str, count: int = 500) -> pd.DataFrame:
        candidates = [
            self.csv_folder / f"{symbol}_{timeframe}.csv",
            self.csv_folder / f"{symbol}.csv",
            self.csv_folder / "example.csv",
        ]

        csv_path = next((path for path in candidates if path.exists()), None)
        if csv_path is None:
            self.logger.error("CSV data missing for %s %s in %s", symbol, timeframe, self.csv_folder)
            return pd.DataFrame()

        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:
            self.logger.error("Could not read CSV file %s: %s", csv_path, exc)
            return pd.DataFrame()

        if "symbol" in df.columns:
            matching = df["symbol"].astype(str).str.upper() == symbol.upper()
            if matching.any():
                df = df[matching]

        if "timeframe" in df.columns:
            matching = df["timeframe"].astype(str).str.upper() == timeframe.upper()
            if matching.any():
                df = df[matching]

        if "time" not in df.columns and "timestamp" in df.columns:
            df = df.rename(columns={"timestamp": "time"})

        columns = [column for column in ["time", "open", "high", "low", "close", "tick_volume"] if column in df.columns]
        df = df[columns].tail(count).copy()
        if self.log_fetches:
            self.logger.info("Market data fetched from CSV: %s %s %s candles", symbol, timeframe, len(df))
        return df
