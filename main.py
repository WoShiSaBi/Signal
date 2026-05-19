from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from alerts.discord import DiscordWebhookAlert
from alerts.telegram import TelegramAlert
from config_loader import (
    ConfigError,
    get_candles_to_fetch,
    get_enabled_symbols,
    get_symbol_aliases,
    get_enabled_timeframe_sets,
    get_strategy_settings,
    load_config,
    print_startup_config,
    validate_config,
)
from data.csv_data import CSVDataProvider
from data.mt5_data import MT5DataProvider
from strategies.mtf_fractal_ifvg import MTFFractalIFVGStrategy, StrategySignal, TimeframeSet
from utils.duplicate_filter import DuplicateFilter
from utils.env import load_project_env
from utils.logger import setup_logger
from utils.trade_tracker import TradeTracker, format_trade_outcome_message
from utils.time_utils import is_in_enabled_session


def build_data_provider(config: dict, logger: logging.Logger):
    data_config = config.get("data", {})
    mode = str(data_config.get("mode", "csv")).lower()
    log_fetches = bool(config.get("scanner", {}).get("log_market_data_fetch", True))

    if mode == "mt5":
        provider = MT5DataProvider(logger, symbol_aliases=get_symbol_aliases(config), log_fetches=log_fetches)
        if not provider.connect():
            if bool(data_config.get("mt5", {}).get("fallback_to_csv_on_error", False)):
                logger.error("MT5 connection failed. Falling back to CSV mode.")
                return CSVDataProvider(str(data_config.get("csv_folder", "sample_data")), logger, log_fetches)
            raise RuntimeError("MT5 mode selected but MT5 connection failed.")
        return provider

    return CSVDataProvider(str(data_config.get("csv_folder", "sample_data")), logger, log_fetches)


def is_high_confidence_signal(signal: StrategySignal, channel_config: dict) -> bool:
    return high_confidence_rejection_reason(signal, channel_config) is None


def high_confidence_rejection_reason(signal: StrategySignal, channel_config: dict) -> str | None:
    if signal.signal not in {"BUY", "SELL"}:
        return f"signal is {signal.signal}, not BUY/SELL"

    minimum_rr = float(channel_config.get("minimum_risk_reward", 2.0))
    if signal.risk_reward is None or signal.risk_reward < minimum_rr:
        rr = "N/A" if signal.risk_reward is None else f"1:{signal.risk_reward:.2f}"
        return f"risk/reward {rr} is below required 1:{minimum_rr:.2f}"

    if bool(channel_config.get("require_complete_trade_plan", True)):
        required_values = {
            "HTF FVG": signal.htf_fvg,
            "MTF sweep": signal.mtf_sweep,
            "IFVG": signal.ifvg,
            "entry": signal.entry_price,
            "hard SL": signal.hard_stop_loss,
            "TP1": signal.tp1,
        }
        missing = [name for name, value in required_values.items() if value is None]
        if missing:
            return f"incomplete trade plan; missing {', '.join(missing)}"

    return None



def should_alert_for_channel(signal: StrategySignal, channel_config: dict) -> bool:
    return alert_rejection_reason(signal, channel_config) is None


def alert_rejection_reason(signal: StrategySignal, channel_config: dict) -> str | None:
    if not bool(channel_config.get("enabled", False)):
        return "channel disabled"

    if bool(channel_config.get("high_confidence_only", False)):
        reason = high_confidence_rejection_reason(signal, channel_config)
        if reason:
            return f"high-confidence-only filter blocked it: {reason}"
        return None

    if signal.signal == "WAIT":
        if not bool(channel_config.get("send_wait_alerts", False)):
            return "WAIT alerts disabled"
        return None

    if signal.signal == "INVALIDATED":
        if not bool(channel_config.get("send_invalidated_alerts", True)):
            return "INVALIDATED alerts disabled"
        return None

    if signal.signal not in {"BUY", "SELL"}:
        return f"unsupported signal type {signal.signal}"

    return None


def alert_rejection_summary(signal: StrategySignal, config: dict) -> str:
    channel_names = ("telegram", "discord")
    reasons = []
    for name in channel_names:
        reason = alert_rejection_reason(signal, config.get(name, {}))
        if reason:
            reasons.append(f"{name}: {reason}")
        else:
            reasons.append(f"{name}: would send")
    return "; ".join(reasons)


def should_alert(signal: StrategySignal, config: dict) -> bool:
    return should_alert_for_channel(signal, config.get("telegram", {})) or should_alert_for_channel(
        signal,
        config.get("discord", {}),
    )


def should_warn_lower_timeframes(signal: StrategySignal) -> bool:
    if signal.signal in {"BUY", "SELL"}:
        return True
    if signal.signal != "WAIT":
        return False
    return signal.htf_fvg is not None or signal.mtf_sweep is not None or signal.direction in {"BUY", "SELL"}


def apply_htf_priority_warning(signal: StrategySignal) -> None:
    signal.htf_priority_warning = True
    signal.confluence_score -= 1


def fetch_market_data(
    provider,
    symbol: str,
    timeframe_set: TimeframeSet,
    candles_to_fetch: int,
    logger: logging.Logger,
    daily_timeframe: str = "D1",
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

    daily = provider.fetch_rates(symbol, daily_timeframe, candles_to_fetch)
    return htf, mtf, ltf, daily


def scan_once(
    config: dict,
    provider,
    telegram: TelegramAlert,
    discord: DiscordWebhookAlert,
    duplicate_filter: DuplicateFilter,
    trade_tracker: TradeTracker,
    logger: logging.Logger,
) -> None:
    if not is_in_enabled_session(config):
        logger.info("Outside enabled sessions. Scanner is waiting.")
        return

    symbols = get_enabled_symbols(config)
    timezone_name = config.get("sessions", {}).get("timezone", "Asia/Singapore")
    daily_timeframe = str(config.get("data", {}).get("daily_timeframe", "D1"))
    log_wait_states = bool(config.get("scanner", {}).get("log_wait_states", True))
    filters_config = config.get("filters", {})
    warn_htf_priority = bool(
        filters_config.get(
            "warn_htf_priority",
            filters_config.get("enforce_htf_priority", True),
        )
    )

    for symbol in symbols:
        timeframe_sets = get_enabled_timeframe_sets(config, symbol)
        if not timeframe_sets:
            logger.error("Skipping %s because no timeframe sets are enabled for this symbol.", symbol)
            continue

        strategy = MTFFractalIFVGStrategy(get_strategy_settings(config, symbol), logger)
        htf_priority_blocker: StrategySignal | None = None

        for timeframe_set in timeframe_sets:
            candles_to_fetch = get_candles_to_fetch(config, timeframe_set.name)
            bundle = fetch_market_data(provider, symbol, timeframe_set, candles_to_fetch, logger, daily_timeframe)
            if bundle is None:
                continue

            htf, mtf, ltf, daily = bundle
            if bool(config.get("scanner", {}).get("track_trade_outcomes", True)):
                outcomes = trade_tracker.check_outcomes(symbol, timeframe_set.name, ltf)
                for outcome in outcomes:
                    outcome_message = format_trade_outcome_message(outcome)
                    outcome_message += (
                        "\nRuntime TP Stats:\n"
                        f"Tracked Trades: {trade_tracker.stats.tracked_trades}\n"
                        f"Entry Fills: {trade_tracker.stats.entry_fills}\n"
                        f"TP1 Hits: {trade_tracker.stats.tp1_hits}\n"
                        f"TP2 Hits: {trade_tracker.stats.tp2_hits}\n"
                    )
                    telegram_sent = (
                        telegram.send(outcome_message)
                        if bool(config.get("telegram", {}).get("enabled", False))
                        else False
                    )
                    discord_sent = (
                        discord.send_trade_outcome(outcome, trade_tracker.stats, timezone_name)
                        if bool(config.get("discord", {}).get("enabled", False))
                        else False
                    )
                    logger.info(
                        "%s %s: %s hit at %s. Runtime stats: tracked=%s, entry_fills=%s, tp1_hits=%s, tp2_hits=%s",
                        symbol,
                        timeframe_set.name,
                        outcome.target_name,
                        outcome.target_price,
                        trade_tracker.stats.tracked_trades,
                        trade_tracker.stats.entry_fills,
                        trade_tracker.stats.tp1_hits,
                        trade_tracker.stats.tp2_hits,
                    )
                    if not telegram_sent and not discord_sent:
                        logger.info("Trade outcome alert was not sent because no enabled channel accepted it.")

            signal = strategy.analyze(symbol, timeframe_set, htf, mtf, ltf, daily)

            if warn_htf_priority and htf_priority_blocker is not None:
                apply_htf_priority_warning(signal)
                logger.info(
                    "%s %s: HTF priority warning applied because higher timeframe set %s has active %s state.",
                    symbol,
                    timeframe_set.name,
                    htf_priority_blocker.timeframe_set,
                    htf_priority_blocker.signal,
                )

            if signal.signal == "WAIT":
                if log_wait_states:
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

            if warn_htf_priority and htf_priority_blocker is None and should_warn_lower_timeframes(signal):
                htf_priority_blocker = signal

            if not should_alert(signal, config):
                logger.info(
                    "Alert not sent for %s %s: %s",
                    symbol,
                    timeframe_set.name,
                    alert_rejection_summary(signal, config),
                )
                continue

            if not duplicate_filter.should_send(signal):
                logger.info(
                    "Alert not sent for %s %s: duplicate setup or symbol rate limit reached",
                    symbol,
                    timeframe_set.name,
                )
                continue

            sent_any = False

            telegram_rejection = alert_rejection_reason(signal, config.get("telegram", {}))
            if telegram_rejection is None:
                sent_any = telegram.send_signal(signal, timezone_name) or sent_any
            else:
                logger.info(
                    "Telegram alert not sent for %s %s: %s",
                    symbol,
                    timeframe_set.name,
                    telegram_rejection,
                )

            discord_rejection = alert_rejection_reason(signal, config.get("discord", {}))
            if discord_rejection is None:
                sent_any = discord.send_signal(signal, timezone_name) or sent_any
            else:
                logger.info(
                    "Discord alert not sent for %s %s: %s",
                    symbol,
                    timeframe_set.name,
                    discord_rejection,
                )

            if sent_any:
                logger.info("Alert sent for %s %s", symbol, timeframe_set.name)
                if trade_tracker.track_signal(signal):
                    logger.info(
                        "Tracking trade outcome for %s %s. Runtime stats: tracked=%s, entry_fills=%s, tp1_hits=%s, tp2_hits=%s",
                        symbol,
                        timeframe_set.name,
                        trade_tracker.stats.tracked_trades,
                        trade_tracker.stats.entry_fills,
                        trade_tracker.stats.tp1_hits,
                        trade_tracker.stats.tp2_hits,
                    )


def run_bot(config_path: str, run_once: bool = False) -> int:
    load_project_env()
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
    trade_tracker = TradeTracker()

    scan_interval = int(scanner_config.get("scan_interval_seconds", 60))

    try:
        while True:
            scan_once(config, provider, telegram, discord, duplicate_filter, trade_tracker, logger)
            if run_once:
                break
            
            # --- REAL-TIME SYNC CODE ---
            sync_interval = 300  # 300 seconds = 5 minutes
            current_time = time.time()
            sleep_seconds = sync_interval - (current_time % sync_interval)
            
            logger.info(f"Waiting {int(sleep_seconds)} seconds for the next 5-minute mark...")
            time.sleep(sleep_seconds)
            # ---------------------------
            
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
