"""Smoke test for app.config.env_loader.

Run with ``python tests/smoke_env_loader.py`` from the project root.
The test writes a temporary dotenv file and verifies that ``load_env``
loads missing values without overwriting already-set process env vars.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

failures: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"{name}_OK {detail}".rstrip())
    else:
        print(f"{name}_FAIL {detail}")
        failures.append(name)


def _check_parse() -> None:
    try:
        from app.config.env_loader import load_env  # noqa: F401
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_loads_dotenv_without_override() -> None:
    from app.config.env_loader import load_env

    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text(
            "MANUL_TEST_TOKEN=from_file\n"
            "MANUL_EXISTING_VALUE=from_file\n",
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {"MANUL_EXISTING_VALUE": "from_process"},
            clear=False,
        ):
            os.environ.pop("MANUL_TEST_TOKEN", None)
            os.environ.pop("MANUL_SKIP_DOTENV", None)
            loaded = load_env(env_path)

            if not loaded:
                _record("LOAD_RETURNS_TRUE", False, "expected load_env to return True")
                return
            if os.environ.get("MANUL_TEST_TOKEN") != "from_file":
                _record("LOAD_NEW_VALUE", False, repr(os.environ.get("MANUL_TEST_TOKEN")))
                return
            if os.environ.get("MANUL_EXISTING_VALUE") != "from_process":
                _record("NO_OVERRIDE", False, repr(os.environ.get("MANUL_EXISTING_VALUE")))
                return

    _record("LOAD_DOTENV", True, "loads missing values and preserves existing values")


def _check_skip_flag() -> None:
    from app.config.env_loader import load_env

    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        env_path.write_text("MANUL_SKIPPED_VALUE=from_file\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"MANUL_SKIP_DOTENV": "1"}, clear=False):
            os.environ.pop("MANUL_SKIPPED_VALUE", None)
            loaded = load_env(env_path)
            if loaded:
                _record("SKIP_RETURNS_FALSE", False, "expected False when skip flag is set")
                return
            if os.environ.get("MANUL_SKIPPED_VALUE") is not None:
                _record("SKIP_NO_LOAD", False, repr(os.environ.get("MANUL_SKIPPED_VALUE")))
                return

    _record("SKIP_FLAG", True, "MANUL_SKIP_DOTENV=1 disables dotenv loading")


def main() -> int:
    _check_parse()
    _check_loads_dotenv_without_override()
    _check_skip_flag()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_ENV_LOADER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
