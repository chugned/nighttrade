"""Configuration loading and validation tests."""

from __future__ import annotations

import pytest

from nighttrade.config import ConfigError, load_config, load_config_dict


def test_default_config_loads(config):
    assert config.profile == "default"
    assert config.symbol == "AAPL"


def test_default_config_is_paper_only(config):
    assert config.safety.paper_trading is True
    assert config.safety.live_trading_enabled is False
    assert config.safety.allow_real_orders is False


def test_config_rejects_live_trading():
    with pytest.raises(ConfigError):
        load_config_dict({"safety": {"live_trading_enabled": True}})


def test_config_rejects_real_orders():
    with pytest.raises(ConfigError):
        load_config_dict({"safety": {"allow_real_orders": True}})


def test_config_rejects_disabling_paper():
    with pytest.raises(ConfigError):
        load_config_dict({"safety": {"paper_trading": False}})


def test_config_rejects_unknown_key():
    with pytest.raises(ConfigError):
        load_config_dict({"definitely_not_a_key": 1})


def test_config_rejects_bad_ema_order():
    with pytest.raises(ConfigError):
        load_config_dict({"indicators": {"ema_fast": 30, "ema_slow": 10}})


def test_config_rejects_missing_profile():
    with pytest.raises(ConfigError):
        load_config(profile="does_not_exist", load_dotenv_file=False)


def test_fusion_weights_present(config):
    w = config.fusion.weights
    assert w.technical > 0 and w.macro > 0


def test_env_override(monkeypatch):
    monkeypatch.setenv("NIGHTTRADE_ALLOW_NETWORK", "true")
    cfg = load_config(load_dotenv_file=False)
    assert cfg.runtime.allow_network is True
