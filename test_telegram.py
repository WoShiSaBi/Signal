from __future__ import annotations

from datetime import datetime

from alerts.telegram import TelegramAlert
from utils.env import load_project_env
from utils.logger import setup_logger


def main() -> int:
    load_project_env()
    logger = setup_logger("logs/bot.log")
    telegram = TelegramAlert(enabled=True, logger=logger)

    message = f"""MTF FRACTAL IFVG BOT TEST

Telegram bot is connected.
This is only a test message.

Timestamp UTC: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
"""

    if telegram.send(message):
        print("Telegram test message sent.")
        return 0

    print("Telegram test failed. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env and logs/bot.log.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
