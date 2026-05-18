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


def _confidence_label(signal: StrategySignal) -> str:
    rr = signal.risk_reward or 0
    if rr >= 3:
        return "A+ High Confidence"
    if rr >= 2:
        return "A High Confidence"
    return "Filtered"


def format_signal_embed(signal: StrategySignal, timezone_name: str = "Asia/Singapore") -> dict:
    color = 0x2ECC71 if signal.signal == "BUY" else 0xE74C3C
    rr = "N/A" if signal.risk_reward is None else f"1:{signal.risk_reward:.2f}"
    htf_zone = _fmt_zone(signal.htf_fvg.bottom, signal.htf_fvg.top) if signal.htf_fvg else "N/A"
    ifvg_zone = _fmt_zone(signal.ifvg.bottom, signal.ifvg.top) if signal.ifvg else "N/A"
    sweep_level = _fmt_price(signal.mtf_sweep.swept_level) if signal.mtf_sweep else "N/A"
    sweep_time = _fmt_time(signal.mtf_sweep.timestamp, timezone_name) if signal.mtf_sweep else "N/A"
    reasons = "\n".join(f"- {reason}" for reason in signal.reason[:7]) or "- Strategy conditions confirmed."

    return {
        "title": f"{signal.symbol} {signal.signal} Setup",
        "description": f"{_confidence_label(signal)} | {signal.scenario}",
        "color": color,
        "fields": [
            {
                "name": "Timeframe Set",
                "value": f"HTF `{signal.htf}` | MTF `{signal.mtf}` | LTF `{signal.ltf}`",
                "inline": False,
            },
            {
                "name": "Entry Plan",
                "value": (
                    f"Entry: `{_fmt_price(signal.entry_price)}`\n"
                    f"Hard SL: `{_fmt_price(signal.hard_stop_loss)}`\n"
                    f"TP1: `{_fmt_price(signal.tp1)}`\n"
                    f"TP2: `{_fmt_price(signal.tp2)}`"
                ),
                "inline": True,
            },
            {
                "name": "Risk",
                "value": f"Risk/Reward: `{rr}`\nStatus: `Waiting for IFVG retest`",
                "inline": True,
            },
            {
                "name": "HTF FVG",
                "value": f"Zone: `{htf_zone}`\nStatus: `{signal.htf_fvg.status.title() if signal.htf_fvg else 'N/A'}`",
                "inline": True,
            },
            {
                "name": "MTF Sweep",
                "value": f"Level: `{sweep_level}`\nTime: `{sweep_time}`",
                "inline": True,
            },
            {
                "name": "IFVG Zone",
                "value": f"Zone: `{ifvg_zone}`\nStatus: `{signal.ifvg.status.title() if signal.ifvg else 'N/A'}`",
                "inline": False,
            },
            {
                "name": "Why This Alert Passed",
                "value": reasons[:1024],
                "inline": False,
            },
        ],
        "footer": {
            "text": f"MTF Fractal IFVG Bot | {_fmt_time(signal.timestamp, timezone_name)}"
        },
    }


class DiscordWebhookAlert:
    def __init__(
        self,
        enabled: bool = True,
        username: str = "MTF IFVG Bot",
        logger: logging.Logger | None = None,
    ) -> None:
        self.enabled = enabled
        self.username = username
        self.logger = logger or logging.getLogger(__name__)
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

    def send(self, message: str) -> bool:
        if not self.enabled:
            self.logger.info("Discord disabled. Message not sent.")
            return False

        if not self.webhook_url:
            self.logger.error("Discord webhook missing. Set DISCORD_WEBHOOK_URL in .env.")
            return False

        payload = {
            "username": self.username,
            "content": self._fit_discord_content(message),
        }

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("Discord webhook error: %s", exc)
            return False

        self.logger.info("Discord alert sent at %s", datetime.utcnow().isoformat())
        return True

    def send_signal(self, signal: StrategySignal, timezone_name: str = "Asia/Singapore") -> bool:
        if not self.enabled:
            self.logger.info("Discord disabled. Signal embed not sent.")
            return False

        if not self.webhook_url:
            self.logger.error("Discord webhook missing. Set DISCORD_WEBHOOK_URL in .env.")
            return False

        payload = {
            "username": self.username,
            "embeds": [format_signal_embed(signal, timezone_name)],
        }

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("Discord webhook error: %s", exc)
            return False

        self.logger.info("Discord signal embed sent at %s", datetime.utcnow().isoformat())
        return True

    @staticmethod
    def _fit_discord_content(message: str) -> str:
        max_length = 2000
        if len(message) <= max_length:
            return message
        return message[: max_length - 40].rstrip() + "\n\n...message truncated for Discord"
