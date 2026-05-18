from __future__ import annotations

import logging
import os
from datetime import datetime

import pytz
import requests

from strategies.mtf_fractal_ifvg import StrategySignal


def _fmt_price(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.5f}"


def _fmt_zone(bottom: float | None, top: float | None) -> str:
    if bottom is None or top is None:
        return "N/A"
    return f"{bottom:.5f} - {top:.5f}"


def _fmt_time(value, timezone_name: str) -> str:
    if value is None:
        return "N/A"
    timezone = pytz.timezone(timezone_name)
    timestamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if timestamp.tzinfo is None:
        timestamp = pytz.utc.localize(timestamp)
    return timestamp.astimezone(timezone).strftime("%Y-%m-%d %H:%M %Z")


def format_signal_message(signal: StrategySignal, timezone_name: str = "Asia/Singapore") -> str:
    htf_zone = (
        _fmt_zone(signal.htf_fvg.bottom, signal.htf_fvg.top)
        if signal.htf_fvg
        else "N/A"
    )
    ifvg_zone = _fmt_zone(signal.ifvg.bottom, signal.ifvg.top) if signal.ifvg else "N/A"
    sweep_level = _fmt_price(signal.mtf_sweep.swept_level) if signal.mtf_sweep else "N/A"
    sweep_low = _fmt_price(signal.mtf_sweep.sweep_low) if signal.mtf_sweep else "N/A"
    sweep_high = _fmt_price(signal.mtf_sweep.sweep_high) if signal.mtf_sweep else "N/A"
    sweep_time = _fmt_time(signal.mtf_sweep.timestamp, timezone_name) if signal.mtf_sweep else "N/A"
    rr = "N/A" if signal.risk_reward is None else f"1:{signal.risk_reward:.2f}"
    reason_lines = "\n".join(f"- {reason}" for reason in signal.reason) or "- N/A"

    header = "🚨 MTF FRACTAL IFVG SIGNAL" if signal.signal in {"BUY", "SELL"} else "MTF FRACTAL IFVG UPDATE"
    invalidation = (
        f"\nInvalidation Reason:\n{signal.invalidation_reason}\n"
        if signal.invalidation_reason
        else ""
    )

    return f"""{header}

Symbol: {signal.symbol}
Signal: {signal.signal}
Scenario: {signal.scenario}

Timeframe Set:
HTF: {signal.htf}
MTF: {signal.mtf}
LTF: {signal.ltf}

HTF FVG:
Zone: {htf_zone}
Status: {signal.htf_fvg.status.title() if signal.htf_fvg else "N/A"}

MTF Liquidity Sweep:
Swept Level: {sweep_level}
Sweep Candle Low: {sweep_low}
Sweep Candle High: {sweep_high}
Sweep Time: {sweep_time}

IFVG Entry Zone:
Zone: {ifvg_zone}
Status: {signal.ifvg.status.title() if signal.ifvg else "N/A"}

Entry Plan:
Limit Entry: {_fmt_price(signal.entry_price)}
Hard SL: {_fmt_price(signal.hard_stop_loss)}
TP1: {_fmt_price(signal.tp1)}
TP2: {_fmt_price(signal.tp2)}

Management:
{signal.dynamic_stop_condition or "Wait for IFVG retest."}

Risk/Reward: {rr}
{invalidation}
Reason:
{reason_lines}

Timestamp: {_fmt_time(signal.timestamp, timezone_name)}
"""


class TelegramAlert:
    def __init__(self, enabled: bool = True, logger: logging.Logger | None = None) -> None:
        self.enabled = enabled
        self.logger = logger or logging.getLogger(__name__)
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def send(self, message: str) -> bool:
        if not self.enabled:
            self.logger.info("Telegram disabled. Message not sent.")
            return False

        if not self.token or not self.chat_id:
            self.logger.error("Telegram credentials missing. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("Telegram error: %s", exc)
            return False

        self.logger.info("Telegram alert sent at %s", datetime.utcnow().isoformat())
        return True
