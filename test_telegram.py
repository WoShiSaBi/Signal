from __future__ import annotations

from datetime import datetime

import pandas as pd

from alerts.telegram import TelegramAlert
from strategies.fvg import FVG
from strategies.liquidity import LiquiditySweep
from strategies.mtf_fractal_ifvg import StrategySignal
from strategies.pivots import Pivot
from utils.env import load_project_env
from utils.logger import setup_logger


def build_test_signal() -> StrategySignal:
    pivot = Pivot(
        index=1,
        timestamp=pd.Timestamp("2026-05-20 00:00"),
        price=49509.9,
        kind="low",
    )
    sweep = LiquiditySweep(
        direction="bullish",
        swept_level=49509.9,
        sweep_high=49619.4,
        sweep_low=49498.4,
        sweep_open=49520.0,
        sweep_close=49580.0,
        timestamp=pd.Timestamp("2026-05-20 00:00"),
        candle_index=10,
        pivot=pivot,
    )
    htf_fvg = FVG(
        direction="bullish",
        top=49448.5,
        bottom=49250.3,
        candle_indexes=[1, 2, 3],
        start_time=pd.Timestamp("2026-05-19 12:00"),
        end_time=pd.Timestamp("2026-05-19 13:00"),
        status="respected",
    )
    ifvg = FVG(
        direction="bearish",
        top=49582.4,
        bottom=49576.4,
        candle_indexes=[4, 5, 6],
        start_time=pd.Timestamp("2026-05-19 20:00"),
        end_time=pd.Timestamp("2026-05-19 20:15"),
        status="ifvg",
    )

    return StrategySignal(
        symbol="US30",
        timeframe_set="set_3",
        htf="H1",
        mtf="M15",
        ltf="M5",
        signal="BUY",
        scenario="Telegram Formatter Test",
        direction="BUY",
        htf_fvg=htf_fvg,
        mtf_sweep=sweep,
        ifvg=ifvg,
        entry_price=49582.4,
        hard_stop_loss=49498.4,
        tp1=49680.5,
        tp2=49768.5,
        risk_reward=2.21,
        dynamic_stop_condition="Candle body closes back inside or beyond the IFVG zone.",
        confluence_score=3,
        confluence_total=4,
        confluence_factors=[
            "HTF FVG respected",
            "Liquidity sweep confirmed",
            "IFVG conversion",
        ],
        reason=[
            "Formatter test only: new Telegram HTML layout, icons, compact sections, and escaped values.",
            "This is not a live trade signal.",
        ],
        timestamp=datetime.utcnow(),
    )


def main() -> int:
    load_project_env()
    logger = setup_logger("logs/bot.log")
    telegram = TelegramAlert(enabled=True, logger=logger)

    if telegram.send_signal(build_test_signal(), "Asia/Singapore"):
        print("Formatted Telegram signal test sent.")
        return 0

    print("Telegram test failed. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env and logs/bot.log.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
