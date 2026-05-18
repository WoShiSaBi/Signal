from __future__ import annotations

from datetime import datetime

from dotenv import load_dotenv

from alerts.discord import DiscordWebhookAlert
from utils.logger import setup_logger


def main() -> int:
    load_dotenv()
    logger = setup_logger("logs/bot.log")
    discord = DiscordWebhookAlert(enabled=True, username="MTF IFVG Bot", logger=logger)

    message = f"""MTF FRACTAL IFVG BOT TEST

Discord webhook is connected.
This is only a test message.

Timestamp UTC: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
"""

    if discord.send(message):
        print("Discord test message sent.")
        return 0

    print("Discord test failed. Check DISCORD_WEBHOOK_URL in .env and logs/bot.log.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
