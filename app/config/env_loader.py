"""Environment-variable loader for local Manul Radar runs.

Secrets are never stored in ``config.yaml``. The config only stores the
names of the environment variables to read, while local development can
put the actual values in a non-committed ``.env`` file at the project
root. ``load_env`` reads that file if it exists and leaves already-set
process environment variables untouched, which keeps GitHub Secrets,
VPS env vars, and CI overrides authoritative.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_SKIP_VALUES = {"1", "true", "yes", "on"}


def load_env(path: str | Path = ".env") -> bool:
    """Load local environment variables from ``path`` if available.

    Returns ``True`` when a dotenv file was found and parsed, otherwise
    ``False``. Existing environment variables are not overwritten.

    Tests and CI smoke checks can set ``MANUL_SKIP_DOTENV=1`` to make
    the function a no-op even when a developer has a real ``.env`` file
    in the repository root.
    """
    skip_value = os.environ.get("MANUL_SKIP_DOTENV", "").strip().lower()
    if skip_value in _SKIP_VALUES:
        return False

    env_path = Path(path)
    if env_path.is_file():
        return bool(load_dotenv(dotenv_path=env_path, override=False))

    return bool(load_dotenv(override=False))


__all__ = ["load_env"]
