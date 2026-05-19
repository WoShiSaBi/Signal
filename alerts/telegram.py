from __future__ import annotations

import logging
import os
from datetime import datetime
from html import escape

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


def _fmt_confluences(signal: StrategySignal) -> str:
    if signal.confluence_total <= 0:
        return "N/A"
    factors = ", ".join(signal.confluence_factors) if signal.confluence_factors else "None"
    return f"{signal.confluence_score}/{signal.confluence_total} ({factors})"


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _code(value: object) -> str:
    return f"<code>{_html(value)}</code>"


def _signal_badge(signal: StrategySignal) -> tuple[str, str]:
    if signal.signal == "BUY":
        return "🟢", "BUY Setup"
    if signal.signal == "SELL":
        return "🔴", "SELL Setup"
    if signal.signal == "WAIT":
        return "🟡", "WAIT Update"
    if signal.signal == "INVALIDATED":
        return "⚪", "Invalidated"
    return "🔵", f"{signal.signal} Update"


def _fmt_status(value: str | None) -> str:
    if not value:
        return "N/A"
    if value.lower() == "ifvg":
        return "IFVG"
    return value.replace("_", " ").title()


def _confidence_label(signal: StrategySignal) -> str:
    rr = signal.risk_reward or 0
    if signal.signal not in {"BUY", "SELL"}:
        return "Filtered"
    if rr >= 3:
        return "A+ High Confidence"
    if rr >= 2:
        return "A High Confidence"
    return "Low RR"


def _htf_priority_warning(signal: StrategySignal) -> str:
    if not signal.htf_priority_warning:
        return ""
    return "\n⚠️ WARNING: Conflicting HTF Setup Currently Active (-1 Confluence)"


def format_signal_message(signal: StrategySignal, timezone_name: str = "Asia/Singapore") -> str:
    htf_zone = _fmt_zone(signal.htf_fvg.bottom, signal.htf_fvg.top) if signal.htf_fvg else "N/A"
    ifvg_zone = _fmt_zone(signal.ifvg.bottom, signal.ifvg.top) if signal.ifvg else "N/A"
    sweep_level = _fmt_price(signal.mtf_sweep.swept_level) if signal.mtf_sweep else "N/A"
    sweep_low = _fmt_price(signal.mtf_sweep.sweep_low) if signal.mtf_sweep else "N/A"
    sweep_high = _fmt_price(signal.mtf_sweep.sweep_high) if signal.mtf_sweep else "N/A"
    sweep_time = _fmt_time(signal.mtf_sweep.timestamp, timezone_name) if signal.mtf_sweep else "N/A"
    sweep_range = f"{sweep_low} - {sweep_high}" if signal.mtf_sweep else "N/A"
    rr = "N/A" if signal.risk_reward is None else f"1:{signal.risk_reward:.2f}"
    reason_lines = "\n".join(f"• {_html(reason)}" for reason in signal.reason[:8]) or "• N/A"
    icon, title = _signal_badge(signal)

    invalidation = (
        f"\n🚫 <b>Invalidation:</b> {_html(signal.invalidation_reason)}"
        if signal.invalidation_reason
        else ""
    )

    return f"""{icon} <b>MTF FRACTAL IFVG</b>
<b>{_html(signal.symbol)}</b> | <b>{_html(title)}</b>
Scenario: {_code(signal.scenario)}
Quality: {_code(_confidence_label(signal))}{_htf_priority_warning(signal)}

📊 <b>Timeframes</b>
HTF: {_code(signal.htf)}
MTF: {_code(signal.mtf)}
LTF: {_code(signal.ltf)}

🎯 <b>Entry Plan</b>
Entry: {_code(_fmt_price(signal.entry_price))}
Hard SL: {_code(_fmt_price(signal.hard_stop_loss))}
TP1: {_code(_fmt_price(signal.tp1))}
TP2: {_code(_fmt_price(signal.tp2))}

🧱 <b>Zones</b>
HTF FVG: {_code(htf_zone)}
HTF Status: {_code(_fmt_status(signal.htf_fvg.status if signal.htf_fvg else None))}
IFVG: {_code(ifvg_zone)}
IFVG Status: {_code(_fmt_status(signal.ifvg.status if signal.ifvg else None))}

💧 <b>Liquidity Sweep</b>
Swept Level: {_code(sweep_level)}
Sweep Range: {_code(sweep_range)}
Sweep Time: {_code(sweep_time)}

📌 <b>Risk & Filters</b>
Risk/Reward: {_code(rr)}
Confluences: {_code(_fmt_confluences(signal))}{invalidation}

🛡 <b>Management</b>
{_html(signal.dynamic_stop_condition or "Wait for IFVG retest.")}

📝 <b>Reason</b>
{reason_lines}

⏱ <i>{_html(_fmt_time(signal.timestamp, timezone_name))}</i>"""


class TelegramAlert:
    def __init__(
        self,
        enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.enabled = enabled
        self.logger = logger or logging.getLogger(__name__)
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def send(self, message: str, parse_mode: str | None = None) -> bool:
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
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            response = requests.post(url, json=payload, timeout=15)
            if not response.ok:
                self.logger.error(
                    "Telegram error: HTTP %s - %s",
                    response.status_code,
                    response.text,
                )
                return False
        except requests.RequestException as exc:
            self.logger.error("Telegram request error: %s", exc.__class__.__name__)
            return False

        self.logger.info("Telegram alert sent at %s", datetime.utcnow().isoformat())
        return True

    def send_signal(self, signal: StrategySignal, timezone_name: str = "Asia/Singapore") -> bool:
        return self.send(format_signal_message(signal, timezone_name), parse_mode="HTML")
