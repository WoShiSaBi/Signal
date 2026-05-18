from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from strategies.mtf_fractal_ifvg import TimeframeSet


class ConfigError(Exception):
    pass


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not isinstance(config, dict):
        raise ConfigError("Config file must contain a YAML mapping.")
    return config


def get_enabled_symbols(config: dict[str, Any]) -> list[str]:
    symbols = config.get("symbols", [])
    enabled: list[str] = []

    for item in symbols:
        if isinstance(item, dict) and item.get("enabled", False):
            name = str(item.get("name", "")).strip()
            if name:
                enabled.append(name)

    return enabled


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


def get_enabled_timeframe_sets(config: dict[str, Any]) -> list[TimeframeSet]:
    raw_sets = config.get("timeframe_sets", {})
    enabled_sets: list[TimeframeSet] = []

    if not isinstance(raw_sets, dict):
        return enabled_sets

    for name, item in raw_sets.items():
        if not isinstance(item, dict) or not item.get("enabled", False):
            continue

        htf = str(item.get("htf", "")).strip()
        mtf = str(item.get("mtf", "")).strip()
        ltf = str(item.get("ltf", "")).strip()
        if htf and mtf and ltf:
            enabled_sets.append(TimeframeSet(name=str(name), htf=htf, mtf=mtf, ltf=ltf))

    return enabled_sets


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


def print_startup_config(config: dict[str, Any]) -> None:
    enabled_symbols = get_enabled_symbols(config)
    enabled_sets = get_enabled_timeframe_sets(config)

    print(f"Enabled symbols: {', '.join(enabled_symbols)}")
    print(
        "Enabled timeframe sets: "
        + ", ".join(f"{item.name}({item.htf}/{item.mtf}/{item.ltf})" for item in enabled_sets)
    )
