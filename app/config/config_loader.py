"""YAML configuration loader.

Single source of truth for runtime configuration. The project ships a
checked-in ``app/config/config.yaml`` with non-secret settings; secrets
(telegram token, chat id) are referenced by environment-variable name
and resolved at use time, not here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _read_yaml_file(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def load_config(path: str = "app/config/config.yaml") -> dict[str, Any]:
    """Load and return the YAML config at ``path`` as a plain dict."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")
    return _read_yaml_file(config_path)


def load_optional_config(path: str = "app/config/companies.yaml") -> dict[str, Any]:
    """Load a YAML config if it exists; return ``{}`` when absent.

    ``companies.yaml`` is intentionally optional so older checkouts and smoke
    tests can still run with only ``config.yaml``.
    """
    config_path = Path(path)
    if not config_path.is_file():
        return {}
    return _read_yaml_file(config_path)


__all__ = ["load_config", "load_optional_config"]
