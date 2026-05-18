from __future__ import annotations

from datetime import datetime, time

import pytz


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def now_in_timezone(timezone_name: str) -> datetime:
    timezone = pytz.timezone(timezone_name)
    return datetime.now(timezone)


def is_in_enabled_session(config: dict) -> bool:
    sessions = config.get("sessions", {})
    timezone_name = sessions.get("timezone", "UTC")
    current = now_in_timezone(timezone_name).time()

    checked_any = False
    for name, item in sessions.items():
        if name == "timezone" or not isinstance(item, dict) or not item.get("enabled", False):
            continue

        checked_any = True
        start = parse_hhmm(str(item.get("start", "00:00")))
        end = parse_hhmm(str(item.get("end", "23:59")))

        if start <= end:
            if start <= current <= end:
                return True
        else:
            if current >= start or current <= end:
                return True

    return True if not checked_any else False
