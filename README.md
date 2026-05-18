# MTF Fractal IFVG Telegram Bot

A rule-based Python signal bot for a Multi-Timeframe Fractal IFVG workflow. It scans enabled symbols and timeframe sets, detects BUY / SELL / WAIT / INVALIDATED setups, and sends qualifying alerts to a Telegram group.

This bot is for signals only. It does not place live trades or auto-execute broker orders.

## 1. Install Requirements

Use Python 3.10+.

```bash
cd MTF_Fractal_IFVG_Telegram_Bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`MetaTrader5` is only required for MT5 mode. CSV mode can be used for testing without MT5.

## 2. Set Up Telegram Bot With BotFather

1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`.
3. Follow the prompts and copy the bot token.
4. Add the bot to your Telegram group.
5. Give it permission to send messages.

## 3. Get Telegram Group Chat ID

Common ways:

1. Add your bot to the group.
2. Send a test message in the group.
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
4. Look for `chat.id`.

Group chat IDs often start with `-`.

## 4. Configure `.env`

Create a `.env` file from `.env.example`:

```bash
copy .env.example .env
```

Fill in:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_group_chat_id_here

DISCORD_WEBHOOK_URL=your_discord_webhook_url_here

MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
```

No secrets are hardcoded in the project.

### Discord Webhook Testing

To test Discord before Telegram or MT5:

1. In Discord, open your server channel settings.
2. Go to `Integrations`.
3. Create a webhook and copy the webhook URL.
4. Paste it into `.env`:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Then run:

```bash
python test_discord.py
```

Or double-click:

```text
test_discord.bat
```

Discord alerts are controlled in `config.yaml`:

```yaml
discord:
  enabled: true
  username: MTF IFVG Bot
  high_confidence_only: true
  minimum_risk_reward: 2.0
  require_complete_trade_plan: true
  send_wait_alerts: false
  send_invalidated_alerts: false
```

When `high_confidence_only` is `true`, Discord only sends completed BUY/SELL setups that have:

- Risk/reward at or above `minimum_risk_reward`
- HTF FVG
- MTF liquidity sweep
- IFVG zone
- Entry price
- Hard stop loss
- TP1

WAIT and INVALIDATED updates are filtered out for Discord in this mode.

## 5. Add Or Remove Trading Pairs

Edit `config.yaml`:

```yaml
symbols:
  - name: XAUUSD
    enabled: true
  - name: EURUSD
    enabled: true
```

Add a new item to add a pair. Remove an item to remove a pair.

For brokers that use different MT5 names, keep `name` as the clean alert name and set `broker_symbol` or `aliases`:

```yaml
symbols:
  - name: NAS100
    enabled: true
    broker_symbol: USTEC
    aliases:
      - USTEC
      - US100
      - NAS100.cash
```

On IC Markets MT5, Nasdaq is commonly exposed as `USTEC`.

## 6. Enable Or Disable Pairs

Use `enabled: true` or `enabled: false`.

The scanner uses `get_enabled_symbols(config)` and never hardcodes pairs inside the strategy.

## 7. Configure Timeframe Sets

Edit the `timeframe_sets` section:

```yaml
timeframe_sets:
  set_2:
    enabled: true
    htf: H4
    mtf: H1
    ltf: M15
    candles_to_fetch: 500
```

Disable a set by changing `enabled` to `false`.

You can restrict a symbol to only specific sets:

```yaml
symbols:
  - name: XAUUSD
    enabled: true
    enabled_timeframe_sets:
      - set_2
      - set_3
```

## 8. Run In CSV Mode

In `config.yaml`:

```yaml
data:
  mode: csv
  csv_folder: sample_data
```

Run one scan:

```bash
python main.py --once
```

Run continuously:

```bash
python main.py
```

CSV files can be named:

```text
sample_data/XAUUSD_H1.csv
sample_data/XAUUSD.csv
sample_data/example.csv
```

Expected CSV columns:

```csv
symbol,timeframe,time,open,high,low,close,tick_volume
```

`symbol` and `timeframe` are optional but recommended.

## 9. Run In MT5 Mode

Install MetaTrader 5 and make sure the terminal is logged in, or set MT5 credentials in `.env`.

In `config.yaml`:

```yaml
data:
  mode: mt5
  daily_timeframe: D1

  mt5:
    fallback_to_csv_on_error: false
```

Then run:

```bash
python main.py
```

If a symbol is unavailable in MT5, the bot logs the issue and continues scanning other symbols.

## 10. Strategy Summary

1. Find HTF FVG.
2. Wait for price to enter and respect the HTF FVG.
3. If HTF candle body closes fully beyond the FVG, invalidate.
4. While HTF FVG is respected, scan MTF for liquidity sweep.
5. After MTF sweep, look back 3-4 MTF candles.
6. If MTF FVG exists before the sweep, use Scenario 1 and wait for that MTF FVG to become IFVG.
7. If no MTF FVG exists, drop to LTF and find an LTF FVG formed during/after the sweep.
8. Wait for selected FVG to be disrespected by candle close.
9. If the disrespect candle already touches nearest liquidity TP1, cancel setup.
10. If not, plan limit entry at IFVG boundary.
11. SL goes at original MTF sweep wick extreme.
12. TP1 is nearest liquidity.
13. TP2 is PDH/PDL if available.
14. If TP2 exists, close 50% at TP1, move SL to breakeven, close rest at TP2.
15. If no TP2, close 100% at TP1.

## 11. Modify Rules

Main rule modules:

- `strategies/fvg.py`: FVG detection, merging, respect, IFVG conversion.
- `strategies/liquidity.py`: MTF liquidity sweep detection.
- `strategies/pivots.py`: swing high / swing low detection.
- `strategies/risk.py`: entry, hard SL, TP1, TP2, RR.
- `strategies/mtf_fractal_ifvg.py`: full strategy orchestration.

Configurable settings live in `config.yaml` under `strategy`.

Useful config controls:

```yaml
strategy:
  candles_to_fetch: 500
  minimum_risk_reward: 2.0

  pivots:
    left_bars: 3
    right_bars: 3

  fvg:
    merge_enabled: true
    require_entry_fvg_opposes_sweep: true

  scenarios:
    mtf_override:
      enabled: true
      lookback_candles: 4

    base_ltf:
      enabled: true

  invalidation:
    scenario_3_enabled: true
    minimum_rr_enabled: true

  risk:
    minimum_risk_reward: 2.0
    entry_boundary: support_resistance
    require_tp1: true

    tp2:
      enabled: true
      source: previous_day
```

`entry_boundary` options:

- `support_resistance`: BUY uses IFVG top, SELL uses IFVG bottom.
- `midpoint`: entry at the middle of the IFVG zone.
- `opposite_boundary`: BUY uses IFVG bottom, SELL uses IFVG top.

Per-symbol strategy overrides are supported:

```yaml
symbols:
  - name: XAUUSD
    enabled: true
    minimum_risk_reward: 2.5
    strategy_overrides:
      scenarios:
        mtf_override:
          lookback_candles: 5
```

## 12. Duplicate Alerts

`utils/duplicate_filter.py` prevents repeated alerts using:

- Symbol
- Timeframe set
- Direction
- Scenario
- Entry price
- IFVG zone
- Setup timestamp

It also respects:

- `scanner.duplicate_cooldown_minutes`
- `scanner.max_alerts_per_symbol_per_hour`

## 13. Trading Risk Disclaimer

This bot is for educational and signal purposes only.
It does not guarantee profit.
Forex trading is risky.
No real trades are placed by this bot.
