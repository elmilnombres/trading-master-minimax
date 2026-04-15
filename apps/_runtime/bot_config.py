"""
Runtime configuration — loaded from YAML files.

Owned by apps/_runtime/.
All secrets come from environment variables only (API key/secret).

Two config layers:
1. RuntimeConfig  — shared runtime parameters (tick intervals, paths)
2. BotConfig      — per-bot parameters (capital, risk, symbol)

RuntimeConfig is loaded from configs/runtime/default.yaml.
BotConfig is loaded from configs/risk/{bot_id}.yaml (alpha/beta/gamma).

Path resolution policy (consistent with configs/runtime/default.yaml):
  - YAML specifies absolute paths: /app/state, /app/heartbeat
  - At runtime: if the directory exists at that absolute path → use it (production)
  - If it does not exist → resolve relative to project root (development)
  - This keeps YAML as single source of truth for both environments.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# Derive the project root from this file's location.
# apps/_runtime/bot_config.py → apps/_runtime/ → apps/ → project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIGS_DIR = _PROJECT_ROOT / "configs"
_RUNTIME_CONFIG_FILE = _CONFIGS_DIR / "runtime" / "default.yaml"


# ---- Path resolution helper ----

def _resolve_dir(raw_path: str) -> Path:
    """
    Resolve a directory path for state or heartbeat storage.

    - If absolute and exists → use it (production container)
    - If absolute and does not exist → resolve relative to project root (dev)
    - If relative → resolve relative to project root (explicit dev override)
    """
    p = Path(raw_path)
    # Only use the absolute path if it actually exists on this machine.
    # On Windows dev machines, /app/state is not a real path.
    if p.is_absolute():
        if p.exists():
            return p
        # Absolute path given but doesn't exist → treat as project-relative
        resolved = _PROJECT_ROOT / p
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    # Relative path → project-relative
    resolved = _PROJECT_ROOT / p
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


# ---- RuntimeConfig ----

@dataclass
class RuntimeConfig:
    """
    Shared runtime parameters — loaded once, used by all bots and supervisor.
    """
    tick_interval_seconds: int
    supervisor_poll_seconds: int
    state_dir: Path
    heartbeat_dir: Path
    default_symbol: str
    instrument_cache_ttl_seconds: int
    use_ws_market_data: bool = False  # True: WS-first (Alpha Phase B). False: REST (default)


def load_runtime_config(bot_id: str | None = None) -> RuntimeConfig:
    """
    Load shared runtime config from configs/runtime/default.yaml.

    If bot_id is provided, also checks configs/runtime/{bot_name}_runtime.yaml
    for per-bot overrides (extends default, does not replace).

    Raises FileNotFoundError if the config file is missing.
    """
    if not _RUNTIME_CONFIG_FILE.exists():
        raise FileNotFoundError(f"Runtime config not found: {_RUNTIME_CONFIG_FILE}")

    with open(_RUNTIME_CONFIG_FILE) as f:
        base: dict[str, Any] = yaml.safe_load(f)

    # Per-bot override (e.g. alpha_bot → configs/runtime/alpha_runtime.yaml)
    if bot_id:
        config_name = bot_id.removesuffix("_bot")
        override_file = _CONFIGS_DIR / "runtime" / f"{config_name}_runtime.yaml"
        if override_file.exists():
            with open(override_file) as f:
                override: dict[str, Any] = yaml.safe_load(f)
                base.update(override)  # per-bot keys override defaults

    return RuntimeConfig(
        tick_interval_seconds=int(base.get("tick_interval_seconds", 5)),
        supervisor_poll_seconds=int(base.get("supervisor_poll_seconds", 5)),
        state_dir=_resolve_dir(base.get("state_dir", "/app/state")),
        heartbeat_dir=_resolve_dir(base.get("heartbeat_dir", "/app/heartbeat")),
        default_symbol=str(base.get("default_symbol", "BTCUSDT")),
        instrument_cache_ttl_seconds=int(base.get("instrument_cache_ttl_seconds", 300)),
        use_ws_market_data=bool(base.get("use_ws_market_data", False)),
    )


# ---- BotConfig ----

@dataclass
class BotConfig:
    """
    Per-bot configuration — capital, risk, and execution parameters.

    Loaded from configs/risk/{bot_id}.yaml.
    API credentials come from environment variables only (never from YAML).
    """
    bot_id: str                    # "alpha_bot" | "beta_bot" | "gamma_bot"
    subaccount_name: str           # Bybit subaccount name
    symbol: str                    # trading symbol, e.g. "BTCUSDT"
    capital_usdt: float            # subaccount balance
    risk_per_trade_pct: float     # fraction of capital, e.g. 0.005 = 0.5%
    api_key: str                   # from env var
    api_secret: str                # from env var

    @property
    def risk_amount_usdt(self) -> float:
        """Pre-computed risk amount per trade."""
        return self.capital_usdt * self.risk_per_trade_pct


def load_bot_config(bot_id: str) -> BotConfig:
    """
    Load per-bot config from configs/risk/{bot_id}.yaml.

    Secrets (api_key, api_secret) are read from environment variables:
      BYBIT_API_KEY_{BOT_ID_UPPER}  (e.g. BYBIT_API_KEY_ALPHA_BOT)
      BYBIT_API_SECRET_{BOT_ID_UPPER} (e.g. BYBIT_API_SECRET_ALPHA_BOT)

    The subaccount_name from YAML is used as the Bybit subaccount header.

    Raises FileNotFoundError if the bot's risk config is missing.
    Raises ValueError if required env vars are not set.
    """
    # Strip _bot suffix to derive the config filename (alpha_bot → alpha)
    config_name = bot_id.removesuffix("_bot")
    risk_file = _CONFIGS_DIR / "risk" / f"{config_name}.yaml"
    if not risk_file.exists():
        raise FileNotFoundError(f"Bot risk config not found: {risk_file}")

    with open(risk_file) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    bot_id_upper = bot_id.upper().replace("-", "_")

    api_key = _env_or_error(f"BYBIT_API_KEY_{bot_id_upper}")
    api_secret = _env_or_error(f"BYBIT_API_SECRET_{bot_id_upper}")

    return BotConfig(
        bot_id=bot_id,
        subaccount_name=str(raw.get("subaccount_name", bot_id)),
        symbol=str(raw.get("symbol", "BTCUSDT")),
        capital_usdt=float(raw.get("capital_usdt", 50.0)),
        risk_per_trade_pct=float(raw.get("risk_per_trade_pct", 0.005)),
        api_key=api_key,
        api_secret=api_secret,
    )


def _env_or_error(name: str) -> str:
    """Read an env var or raise ValueError with a clear message."""
    import os
    val = os.environ.get(name)
    if val is None:
        raise ValueError(
            f"Required environment variable not set: {name}. "
            f"Set it in your environment before starting the bot."
        )
    return val
