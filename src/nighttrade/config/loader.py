"""Config loading: YAML file + .env overrides -> validated ``AppConfig``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

from .schema import AppConfig

# Repo root = three parents up from this file (src/nighttrade/config/loader.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = _REPO_ROOT / "configs"

# Maps an environment variable to a dotted path inside the config tree.
_ENV_OVERRIDES: Dict[str, str] = {
    "NIGHTTRADE_LOG_LEVEL": "runtime.log_level",
    "NIGHTTRADE_DETERMINISTIC": "runtime.deterministic",
    "NIGHTTRADE_ALLOW_NETWORK": "runtime.allow_network",
}


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or fails validation."""


def _coerce_env(raw: str) -> Any:
    """Best-effort scalar coercion for environment-variable strings."""
    low = raw.strip().lower()
    if low in {"true", "yes", "1", "on"}:
        return True
    if low in {"false", "no", "0", "off"}:
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _set_path(tree: Dict[str, Any], dotted: str, value: Any) -> None:
    """Set ``tree[a][b][c] = value`` for a dotted path 'a.b.c'."""
    keys = dotted.split(".")
    node = tree
    for key in keys[:-1]:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise ConfigError(f"env override path '{dotted}' conflicts with config")
    node[keys[-1]] = value


def _apply_env_overrides(tree: Dict[str, Any]) -> Dict[str, Any]:
    for env_key, dotted in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            _set_path(tree, dotted, _coerce_env(raw))
    return tree


def load_config(
    profile: str | None = None,
    config_dir: Path | str | None = None,
    *,
    load_dotenv_file: bool = True,
) -> AppConfig:
    """Load and validate the application config.

    Resolution order (later wins):
      1. schema defaults
      2. ``configs/<profile>.yaml``
      3. environment variables (``NIGHTTRADE_*``)

    The active profile is ``profile`` arg -> ``NIGHTTRADE_PROFILE`` env -> "default".
    """
    if load_dotenv_file:
        load_dotenv(_REPO_ROOT / ".env")

    profile = profile or os.environ.get("NIGHTTRADE_PROFILE", "default")
    cfg_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    path = cfg_dir / f"{profile}.yaml"

    tree: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level")
        tree = loaded
    elif profile != "default":
        # An explicitly requested profile that does not exist is an error;
        # a missing 'default' just means "use schema defaults".
        raise ConfigError(f"config profile not found: {path}")

    tree.setdefault("profile", profile)
    _apply_env_overrides(tree)

    try:
        return AppConfig.model_validate(tree)
    except Exception as exc:  # pydantic ValidationError or ValueError
        raise ConfigError(f"invalid configuration ({path}): {exc}") from exc


def load_config_dict(data: Dict[str, Any]) -> AppConfig:
    """Validate an in-memory config dict (used by tests)."""
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
