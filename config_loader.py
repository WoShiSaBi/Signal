from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from strategies.mtf_fractal_ifvg import TimeframeSet


class ConfigError(Exception):
    pass


DEFAULT_CONFIG: dict[str, Any] = {
    "symbols": [],
    "timeframe_sets": {},
    "telegram": {
        "enabled": True,
        "high_confidence_only": False,
        "minimum_risk_reward": 2.0,
        "require_complete_trade_plan": True,
        "send_wait_alerts": False,
        "send_invalidated_alerts": True,
    },
    "discord": {
        "enabled": False,
        "username": "MTF IFVG Bot",
        "high_confidence_only": True,
        "minimum_risk_reward": 2.0,
        "require_complete_trade_plan": True,
        "send_wait_alerts": False,
        "send_invalidated_alerts": False,
    },
    "scanner": {
        "scan_interval_seconds": 60,
        "max_alerts_per_symbol_per_hour": 3,
        "duplicate_cooldown_minutes": 30,
        "log_wait_states": True,
        "log_market_data_fetch": True,
    },
    "strategy": {
        "minimum_risk_reward": 2.0,
        "candles_to_fetch": 500,
        "pivots": {
            "left_bars": 3,
            "right_bars": 3,
        },
        "fvg": {
            "merge_enabled": True,
            "require_entry_fvg_opposes_sweep": True,
        },
        "liquidity_sweep": {
            "enabled": True,
        },
        "scenarios": {
            "mtf_override": {
                "enabled": True,
                "lookback_candles": 4,
            },
            "base_ltf": {
                "enabled": True,
            },
        },
        "invalidation": {
            "scenario_3_enabled": True,
            "minimum_rr_enabled": True,
        },
        "risk": {
            "minimum_risk_reward": 2.0,
            "entry_boundary": "support_resistance",
            "require_tp1": True,
            "tp2": {
                "enabled": True,
                "source": "previous_day",
            },
        },
    },
    "sessions": {
        "timezone": "Asia/Singapore",
    },
    "data": {
        "mode": "csv",
        "csv_folder": "sample_data",
        "daily_timeframe": "D1",
        "mt5": {
            "fallback_to_csv_on_error": False,
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not isinstance(config, dict):
        raise ConfigError("Config file must contain a YAML mapping.")
    return deep_merge(DEFAULT_CONFIG, config)


def get_enabled_symbols(config: dict[str, Any]) -> list[str]:
    symbols = config.get("symbols", [])
    enabled: list[str] = []

    for item in symbols:
        if isinstance(item, dict) and item.get("enabled", False):
            name = str(item.get("name", "")).strip()
            if name:
                enabled.append(name)

    return enabled


def get_symbol_settings(config: dict[str, Any], symbol: str) -> dict[str, Any]:
    for item in config.get("symbols", []):
        if isinstance(item, dict) and str(item.get("name", "")).strip() == symbol:
            return item
    return {}


def get_symbol_aliases(config: dict[str, Any]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}

    for item in config.get("symbols", []):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            continue

        candidates: list[str] = []
        broker_symbol = str(item.get("broker_symbol", "")).strip()
        if broker_symbol:
            candidates.append(broker_symbol)

        raw_aliases = item.get("aliases", [])
        if isinstance(raw_aliases, list):
            candidates.extend(str(alias).strip() for alias in raw_aliases if str(alias).strip())

        aliases[name] = list(dict.fromkeys(candidates))

    return aliases


def get_enabled_timeframe_sets(config: dict[str, Any], symbol: str | None = None) -> list[TimeframeSet]:
    raw_sets = config.get("timeframe_sets", {})
    enabled_sets: list[TimeframeSet] = []
    allowed_for_symbol: set[str] | None = None

    if symbol:
        symbol_settings = get_symbol_settings(config, symbol)
        raw_allowed = symbol_settings.get("enabled_timeframe_sets")
        if isinstance(raw_allowed, list) and raw_allowed:
            allowed_for_symbol = {str(item) for item in raw_allowed}

    if not isinstance(raw_sets, dict):
        return enabled_sets

    for name, item in raw_sets.items():
        if not isinstance(item, dict) or not item.get("enabled", False):
            continue
        if allowed_for_symbol is not None and str(name) not in allowed_for_symbol:
            continue

        htf = str(item.get("htf", "")).strip()
        mtf = str(item.get("mtf", "")).strip()
        ltf = str(item.get("ltf", "")).strip()
        if htf and mtf and ltf:
            enabled_sets.append(TimeframeSet(name=str(name), htf=htf, mtf=mtf, ltf=ltf))

    return enabled_sets


def get_timeframe_set_settings(config: dict[str, Any], timeframe_set_name: str) -> dict[str, Any]:
    item = config.get("timeframe_sets", {}).get(timeframe_set_name, {})
    return item if isinstance(item, dict) else {}


def get_candles_to_fetch(config: dict[str, Any], timeframe_set_name: str) -> int:
    timeframe_settings = get_timeframe_set_settings(config, timeframe_set_name)
    return int(timeframe_settings.get("candles_to_fetch", config.get("strategy", {}).get("candles_to_fetch", 500)))


def get_strategy_settings(config: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
    settings = config.get("strategy", {})
    if not isinstance(settings, dict):
        settings = {}

    if symbol:
        symbol_settings = get_symbol_settings(config, symbol)
        overrides = symbol_settings.get("strategy_overrides", {})
        if isinstance(overrides, dict):
            settings = deep_merge(settings, overrides)

        if "minimum_risk_reward" in symbol_settings:
            settings = deep_merge(
                settings,
                {
                    "minimum_risk_reward": symbol_settings["minimum_risk_reward"],
                    "risk": {"minimum_risk_reward": symbol_settings["minimum_risk_reward"]},
                },
            )

    return settings


def validate_config(config: dict[str, Any]) -> None:
    enabled_symbols = get_enabled_symbols(config)
    enabled_sets = get_enabled_timeframe_sets(config)

    if not enabled_symbols:
        raise ConfigError("No symbols are enabled. Enable at least one symbol in config.yaml.")

    if not enabled_sets:
        raise ConfigError("No timeframe sets are enabled. Enable at least one timeframe set in config.yaml.")

    mode = str(config.get("data", {}).get("mode", "csv")).lower()
    if mode not in {"mt5", "csv"}:
        raise ConfigError("data.mode must be either 'mt5' or 'csv'.")

    scan_interval = int(config.get("scanner", {}).get("scan_interval_seconds", 60))
    if scan_interval < 1:
        raise ConfigError("scanner.scan_interval_seconds must be at least 1.")

    valid_entry_boundaries = {"support_resistance", "midpoint", "opposite_boundary"}
    entry_boundary = str(config.get("strategy", {}).get("risk", {}).get("entry_boundary", "support_resistance"))
    if entry_boundary not in valid_entry_boundaries:
        raise ConfigError(f"strategy.risk.entry_boundary must be one of: {', '.join(sorted(valid_entry_boundaries))}.")

    valid_tp2_sources = {"previous_day", "none"}
    tp2_source = str(config.get("strategy", {}).get("risk", {}).get("tp2", {}).get("source", "previous_day"))
    if tp2_source not in valid_tp2_sources:
        raise ConfigError(f"strategy.risk.tp2.source must be one of: {', '.join(sorted(valid_tp2_sources))}.")


def print_startup_config(config: dict[str, Any]) -> None:
    enabled_symbols = get_enabled_symbols(config)
    enabled_sets = get_enabled_timeframe_sets(config)

    print(f"Enabled symbols: {', '.join(enabled_symbols)}")
    print(
        "Enabled timeframe sets: "
        + ", ".join(f"{item.name}({item.htf}/{item.mtf}/{item.ltf})" for item in enabled_sets)
    )
