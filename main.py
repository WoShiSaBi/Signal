from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from alerts.discord import DiscordWebhookAlert
from alerts.telegram import TelegramAlert, format_signal_message
from config_loader import (
    ConfigError,
    get_enabled_symbols,
    get_symbol_aliases,
    get_enabled_timeframe_sets,
    load_config,
    print_startup_config,
    validate_config,
)
from data.csv_data import CSVDataProvider
from data.mt5_data import MT5DataProvider
from strategies.mtf_fractal_ifvg import MTFFractalIFVGStrategy, StrategySignal, TimeframeSet
from utils.duplicate_filter import DuplicateFilter
from utils.logger import setup_logger
from utils.time_utils import is_in_enabled_session


def build_data_provider(config: dict, logger: logging.Logger):
    data_config = config.get("data", {})
    mode = str(data_config.get("mode", "csv")).lower()

    if mode == "mt5":
        provider = MT5DataProvider(logger, symbol_aliases=get_symbol_aliases(config))
        if not provider.connect():
            raise RuntimeError("MT5 mode selected but MT5 connection failed.")
        return provider

    return CSVDataProvider(str(data_config.get("csv_folder", "sample_data")), logger)


def is_high_confidence_signal(signal: StrategySignal, channel_config: dict) -> bool:
    if signal.signal not in {"BUY", "SELL"}:
        return False

    minimum_rr = float(channel_config.get("minimum_risk_reward", 2.0))
    if signal.risk_reward is None or signal.risk_reward < minimum_rr:
        return False

    if bool(channel_config.get("require_complete_trade_plan", True)):
        required_values = [
            signal.htf_fvg,
            signal.mtf_sweep,
            signal.ifvg,
            signal.entry_price,
            signal.hard_stop_loss,
            signal.tp1,
        ]
        if any(value is None for value in required_values):
            return False

    return True


def should_alert_for_channel(signal: StrategySignal, channel_config: dict) -> bool:
    if not bool(channel_config.get("enabled", False)):
        return False

    if bool(channel_config.get("high_confidence_only", False)):
        return is_high_confidence_signal(signal, channel_config)

    if signal.signal == "WAIT":
        return bool(channel_config.get("send_wait_alerts", False))

    if signal.signal == "INVALIDATED":
        return bool(channel_config.get("send_invalidated_alerts", True))

    return signal.signal in {"BUY", "SELL"}


def should_alert(signal: StrategySignal, config: dict) -> bool:
    return should_alert_for_channel(signal, config.get("telegram", {})) or should_alert_for_channel(
        signal,
        config.get("discord", {}),
    )


def fetch_market_data(
    provider,
    symbol: str,
    timeframe_set: TimeframeSet,
    candles_to_fetch: int,
    logger: logging.Logger,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    htf = provider.fetch_rates(symbol, timeframe_set.htf, candles_to_fetch)
    if htf.empty:
        logger.error("Skipping %s %s because HTF market data is empty.", symbol, timeframe_set.name)
        return None

    mtf = provider.fetch_rates(symbol, timeframe_set.mtf, candles_to_fetch)
    if mtf.empty:
        logger.error("Skipping %s %s because MTF market data is empty.", symbol, timeframe_set.name)
        return None

    ltf = provider.fetch_rates(symbol, timeframe_set.ltf, candles_to_fetch)
    if ltf.empty:
        logger.error("Skipping %s %s because LTF market data is empty.", symbol, timeframe_set.name)
        return None

    daily = provider.fetch_rates(symbol, "D1", candles_to_fetch)
    return htf, mtf, ltf, daily


def scan_once(
    config: dict,
    provider,
    strategy: MTFFractalIFVGStrategy,
    telegram: TelegramAlert,
    discord: DiscordWebhookAlert,
    duplicate_filter: DuplicateFilter,
    logger: logging.Logger,
) -> None:
    if not is_in_enabled_session(config):
        logger.info("Outside enabled sessions. Scanner is waiting.")
        return

    symbols = get_enabled_symbols(config)
    timeframe_sets = get_enabled_timeframe_sets(config)
    candles_to_fetch = int(config.get("strategy", {}).get("candles_to_fetch", 500))
    timezone_name = config.get("sessions", {}).get("timezone", "Asia/Singapore")

    for symbol in symbols:
        for timeframe_set in timeframe_sets:
            bundle = fetch_market_data(provider, symbol, timeframe_set, candles_to_fetch, logger)
            if bundle is None:
                continue

            htf, mtf, ltf, daily = bundle
            signal = strategy.analyze(symbol, timeframe_set, htf, mtf, ltf, daily)

            if signal.signal == "WAIT":
                logger.info("%s %s: WAIT - %s", symbol, timeframe_set.name, "; ".join(signal.reason))
            elif signal.signal == "INVALIDATED":
                logger.info("%s %s: INVALIDATED - %s", symbol, timeframe_set.name, signal.invalidation_reason)
            else:
                logger.info(
                    "%s %s: %s signal entry=%s tp1=%s rr=%s",
                    symbol,
                    timeframe_set.name,
                    signal.signal,
                    signal.entry_price,
                    signal.tp1,
                    signal.risk_reward,
                )

            if not should_alert(signal, config):
                continue

            if not duplicate_filter.should_send(signal):
                logger.info("Duplicate or rate-limited alert skipped for %s %s", symbol, timeframe_set.name)
                continue

            message = format_signal_message(signal, timezone_name)
            sent_any = False

            if should_alert_for_channel(signal, config.get("telegram", {})):
                sent_any = telegram.send(message) or sent_any

            if should_alert_for_channel(signal, config.get("discord", {})):
                sent_any = discord.send_signal(signal, timezone_name) or sent_any

            if sent_any:
                logger.info("Alert sent for %s %s", symbol, timeframe_set.name)


def run_bot(config_path: str, run_once: bool = False) -> int:
    load_dotenv()
    logger = setup_logger("logs/bot.log")

    try:
        config = load_config(config_path)
        validate_config(config)
    except ConfigError as exc:
        logger.error("Config error: %s", exc)
        return 1

    logger.info("Config loaded from %s", Path(config_path).resolve())
    print_startup_config(config)
    logger.info("Enabled symbols: %s", ", ".join(get_enabled_symbols(config)))
    for item in config.get("symbols", []):
        if isinstance(item, dict) and not item.get("enabled", False):
            logger.info("Disabled symbol skipped: %s", item.get("name", "unknown"))
    logger.info(
        "Enabled timeframe sets: %s",
        ", ".join(
            f"{item.name}({item.htf}/{item.mtf}/{item.ltf})" for item in get_enabled_timeframe_sets(config)
        ),
    )

    try:
        provider = build_data_provider(config, logger)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    strategy = MTFFractalIFVGStrategy(config.get("strategy", {}), logger)
    telegram = TelegramAlert(bool(config.get("telegram", {}).get("enabled", True)), logger)
    discord_config = config.get("discord", {})
    discord = DiscordWebhookAlert(
        enabled=bool(discord_config.get("enabled", False)),
        username=str(discord_config.get("username", "MTF IFVG Bot")),
        logger=logger,
    )
    scanner_config = config.get("scanner", {})
    duplicate_filter = DuplicateFilter(
        duplicate_cooldown_minutes=int(scanner_config.get("duplicate_cooldown_minutes", 30)),
        max_alerts_per_symbol_per_hour=int(scanner_config.get("max_alerts_per_symbol_per_hour", 3)),
    )

    scan_interval = int(scanner_config.get("scan_interval_seconds", 60))

    try:
        while True:
            scan_once(config, provider, strategy, telegram, discord, duplicate_filter, logger)
            if run_once:
                break
            time.sleep(scan_interval)
    except KeyboardInterrupt:
        logger.info("Scanner stopped by user.")
    finally:
        if hasattr(provider, "shutdown"):
            provider.shutdown()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MTF Fractal IFVG Telegram Signal Bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(run_bot(args.config, args.once))
