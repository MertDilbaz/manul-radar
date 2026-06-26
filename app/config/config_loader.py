"""YAML configuration loader.

Single source of truth for runtime configuration. The project ships a
checked-in ``app/config/config.yaml`` with non-secret settings; secrets
(telegram token, chat id) are referenced by environment-variable name
and resolved at use time, not here.

The loader is intentionally tiny: it returns a plain ``dict`` so callers
can shape access (e.g. ``cfg["scoring"]["minimum_score"]``) without us
pinning a schema in code. Schema validation is out of scope for V1.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str = "app/config/config.yaml") -> dict[str, Any]:
    """Load and return the YAML config at ``path`` as a plain dict.

    Raises:
        FileNotFoundError: If ``path`` does not exist. The message names
            the resolved path so misconfigurations are easy to diagnose.

    Returns:
        The parsed YAML as a dict. An empty YAML file yields an empty
        dict rather than ``None``.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {config_path.resolve()}"
        )

    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    return data if isinstance(data, dict) else {}


__all__ = ["load_config"]