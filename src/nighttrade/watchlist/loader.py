"""Loader for the standalone ``configs/watchlist.yaml`` file."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..config.schema import WatchlistConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WATCHLIST_PATH = _REPO_ROOT / "configs" / "watchlist.yaml"


def load_watchlist_config(path: Optional[Path | str] = None) -> WatchlistConfig:
    """Load and validate ``configs/watchlist.yaml``.

    Falls back to schema defaults when the file is absent, so the observatory
    runs out of the box. A malformed file fails loudly via validation.
    """
    path = Path(path) if path else DEFAULT_WATCHLIST_PATH
    if not path.exists():
        return WatchlistConfig()
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return WatchlistConfig.model_validate(data)
