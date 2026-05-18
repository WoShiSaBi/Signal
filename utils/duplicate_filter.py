from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from strategies.mtf_fractal_ifvg import StrategySignal


@dataclass
class DuplicateFilter:
    duplicate_cooldown_minutes: int = 30
    max_alerts_per_symbol_per_hour: int = 3
    sent_keys: dict[str, datetime] = field(default_factory=dict)
    symbol_alert_times: dict[str, deque[datetime]] = field(default_factory=lambda: defaultdict(deque))

    def signal_key(self, signal: StrategySignal) -> str:
        ifvg_zone = signal.ifvg.key_zone() if signal.ifvg else "no-ifvg"
        entry = f"{signal.entry_price:.5f}" if signal.entry_price is not None else "no-entry"
        setup_time = str(signal.setup_timestamp or signal.timestamp)
        direction = signal.direction or signal.signal
        return "|".join(
            [
                signal.symbol,
                signal.timeframe_set,
                direction,
                signal.scenario,
                entry,
                ifvg_zone,
                setup_time,
            ]
        )

    def should_send(self, signal: StrategySignal, now: datetime | None = None) -> bool:
        now = now or datetime.utcnow()
        key = self.signal_key(signal)
        cooldown = timedelta(minutes=self.duplicate_cooldown_minutes)

        last_sent = self.sent_keys.get(key)
        if last_sent and now - last_sent < cooldown:
            return False

        alert_times = self.symbol_alert_times[signal.symbol]
        one_hour_ago = now - timedelta(hours=1)
        while alert_times and alert_times[0] < one_hour_ago:
            alert_times.popleft()

        if len(alert_times) >= self.max_alerts_per_symbol_per_hour:
            return False

        self.sent_keys[key] = now
        alert_times.append(now)
        return True
