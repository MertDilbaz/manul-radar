"""Smoke tests for run_monitor.py production-safety guards.

Run with ``python tests/smoke_run_monitor_guards.py`` from the project root.

These tests pin down the three guards added on 2026-06-29 so a future
refactor cannot silently regress them:

1. ``_is_telegram_send_enabled()`` honours ``MANUL_ENABLE_TELEGRAM_SEND``
   with the documented truthy values and rejects everything else.
2. ``--use-dummy-source`` mode refuses to invoke
   ``TelegramNotifier.send_message`` even when notifier + guard are
   otherwise wired up.
3. ``_build_sources_from_config`` raises ``RuntimeError`` (not
   ``SystemExit``) when zero enabled sources are present, so callers
   get a precise error to act on instead of an opaque process exit.
4. The runner logs a source summary (``Loaded N enabled sources: ...``)
   on every real run so an operator can see what actually ran.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import run_monitor  # noqa: E402
from app.sources.dummy_source import DummySource  # noqa: E402

failures: list[str] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"{name}_OK {detail}".rstrip())
    else:
        print(f"{name}_FAIL {detail}")
        failures.append(name)


@contextmanager
def _capture_logs():
    """Capture loguru output into a StringIO for assertion.

    loguru doesn't expose a stdlib ``Handler`` API; the way to attach
    an in-memory sink is ``logger.add(io.StringIO, format=...)`` and
    then remove it by id when the context exits. We strip the colored
    prefix noise so substring assertions stay readable.
    """
    buf = io.StringIO()
    handler_id = run_monitor.logger.add(buf, format="{message}", level="INFO")
    try:
        yield buf
    finally:
        run_monitor.logger.remove(handler_id)


def test_telegram_guard_truthy_values() -> None:
    """Truthy spellings of MANUL_ENABLE_TELEGRAM_SEND opt in."""
    for value in ("1", "true", "TRUE", "yes", "on", "Yes"):
        with mock.patch.dict(os.environ, {"MANUL_ENABLE_TELEGRAM_SEND": value}):
            _record(
                f"GUARD_TRUTHY_{value.upper()}",
                run_monitor._is_telegram_send_enabled() is True,
                repr(value),
            )


def test_telegram_guard_falsy_values() -> None:
    """Anything other than the documented truthy spellings is off."""
    for value in ("", "0", "false", "no", "off", "maybe", "enabled", "  "):
        with mock.patch.dict(os.environ, {"MANUL_ENABLE_TELEGRAM_SEND": value}, clear=False):
            # clear=False so we don't accidentally inherit a truthy value
            # from the surrounding environment.
            _record(
                f"GUARD_FALSY_{(value or '<empty>').strip() or '<empty>'}",
                run_monitor._is_telegram_send_enabled() is False,
                repr(value),
            )


def test_telegram_guard_unset() -> None:
    """No env var at all -> refused."""
    env = {k: v for k, v in os.environ.items() if k != "MANUL_ENABLE_TELEGRAM_SEND"}
    with mock.patch.dict(os.environ, env, clear=True):
        _record(
            "GUARD_UNSET",
            run_monitor._is_telegram_send_enabled() is False,
            "no env var",
        )


def test_empty_sources_raises_runtime_error() -> None:
    """No enabled sources -> RuntimeError, not silent fallback."""
    fake_config: dict = {"sources": [], "notification": {}}
    with mock.patch.object(run_monitor, "load_optional_config", return_value={}):
        with mock.patch.object(run_monitor, "load_config", return_value=fake_config):
            raised = None
            try:
                run_monitor._build_sources_from_config(fake_config)
            except RuntimeError as exc:
                raised = exc
            except SystemExit as exc:
                raised = exc
    _record(
        "EMPTY_SOURCES_RAISES_RUNTIME",
        isinstance(raised, RuntimeError),
        type(raised).__name__ if raised else "no exception",
    )
    _record(
        "EMPTY_SOURCES_MESSAGE_HAS_REFUSAL",
        isinstance(raised, RuntimeError) and "Refusing to run dummy source" in str(raised),
        str(raised) if raised else "",
    )


def test_disabled_only_sources_raises_runtime_error() -> None:
    """All entries disabled -> still RuntimeError."""
    fake_config: dict = {"sources": [], "notification": {}}
    companies_cfg = {
        "companies": [
            {"parser": "lever", "company": "X", "company_slug": "x", "enabled": False},
        ]
    }
    with mock.patch.object(run_monitor, "load_optional_config", return_value=companies_cfg):
        with mock.patch.object(run_monitor, "load_config", return_value=fake_config):
            raised = None
            try:
                run_monitor._build_sources_from_config(fake_config)
            except RuntimeError as exc:
                raised = exc
    _record(
        "DISABLED_ONLY_RAISES_RUNTIME",
        isinstance(raised, RuntimeError),
        str(raised) if raised else "no exception",
    )


def _stub_runner(sources, *, use_dummy_source: bool, telegram_send_value: str):
    """Drive ``run_monitor.main()`` end-to-end with everything stubbed.

    Returns ``(rc, log_output, sent_messages)`` so each scenario can
    assert on the result. The notifier spy is wired into the module
    via ``_try_build_notifier`` so we can count ``send_message`` calls
    without touching real network code.
    """
    sent_calls: list[str] = []

    class _SpyNotifier:
        def __init__(self, *, bot_token: str, chat_id: str) -> None:
            pass

        def send_message(self, message: str, parse_mode: str | None = None) -> None:
            sent_calls.append(message)

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()

    env = {
        "JOB_DB_PATH": tmp.name,
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_CHAT_ID": "fake-chat",
    }
    if telegram_send_value is not None:
        env["MANUL_ENABLE_TELEGRAM_SEND"] = telegram_send_value

    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(run_monitor, "setup_logging", lambda *a, **kw: None):
            with mock.patch.object(
                run_monitor,
                "_try_build_notifier",
                return_value=_SpyNotifier(bot_token="x", chat_id="y"),
            ):
                with mock.patch.object(
                    run_monitor,
                    "_build_sources_from_config",
                    return_value=list(sources),
                ):
                    with mock.patch.object(
                        run_monitor,
                        "load_config",
                        return_value={"telegram": {}, "notification": {"send_empty_report": False}},
                    ):
                        with mock.patch("run_monitor.JobRepository") as repo_cls:
                            repo_instance = repo_cls.return_value
                            repo_instance.init_db.return_value = None
                            repo_instance.db_path = tmp.name

                            with mock.patch("run_monitor.JobMonitorService") as svc_cls:
                                svc_instance = svc_cls.return_value
                                svc_instance.last_run_stats = {}
                                svc_instance.run.return_value = []

                                with mock.patch.object(run_monitor, "_parse_args") as args_mock:
                                    args_mock.return_value = mock.Mock(
                                        test_telegram=False,
                                        use_dummy_source=use_dummy_source,
                                    )
                                    with _capture_logs() as buf:
                                        rc = run_monitor.main()

    Path(tmp.name).unlink(missing_ok=True)
    return rc, buf.getvalue(), sent_calls


def test_dummy_mode_skips_telegram() -> None:
    """--use-dummy-source must NOT call send_message even with guard ON.

    Regression for the 2026-06-29 incident: dummy source + notifier
    present + opt-in env var -> a real Telegram message was sent.
    """
    rc, log_output, sent_calls = _stub_runner(
        [DummySource()],
        use_dummy_source=True,
        telegram_send_value="true",
    )
    _record("DUMMY_MODE_EXIT_CODE", rc == 0, f"rc={rc}")
    _record("DUMMY_MODE_NO_TELEGRAM_SEND", sent_calls == [], f"sent={len(sent_calls)}")
    _record(
        "DUMMY_MODE_LOGS_DUMMY_WARNING",
        "DUMMY SOURCE MODE ACTIVE" in log_output,
        "missing dummy warning",
    )
    _record(
        "DUMMY_MODE_LOGS_SKIPPED_TELEGRAM",
        "Telegram delivery SKIPPED" in log_output and "dummy source mode" in log_output,
        "missing skipped-telegram warning",
    )


def test_guard_disabled_skips_telegram() -> None:
    """Real source + MANUL_ENABLE_TELEGRAM_SEND unset -> no send."""
    rc, log_output, sent_calls = _stub_runner(
        [DummySource()],
        use_dummy_source=False,
        telegram_send_value=None,  # env var stripped
    )
    _record("GUARD_DISABLED_EXIT_CODE", rc == 0, f"rc={rc}")
    _record("GUARD_DISABLED_NO_SEND", sent_calls == [], f"sent={len(sent_calls)}")
    _record(
        "GUARD_DISABLED_LOGS_OPTIN_WARNING",
        "MANUL_ENABLE_TELEGRAM_SEND is not set" in log_output,
        "missing opt-in warning",
    )
    _record(
        "GUARD_DISABLED_LOGS_SKIPPED",
        "Telegram delivery SKIPPED" in log_output and "MANUL_ENABLE_TELEGRAM_SEND" in log_output,
        "missing skipped warning",
    )


def test_source_summary_logged() -> None:
    """Production run logs 'Loaded N enabled sources:' + per-source label."""
    fake_sources = [
        DummySource(),
        type("StubSource", (), {"company_name": "Acme", "name": "stub_acme"})(),
    ]
    rc, log_output, _ = _stub_runner(
        fake_sources,
        use_dummy_source=False,
        telegram_send_value=None,
    )
    _record("SOURCE_SUMMARY_EXIT_CODE", rc == 0, f"rc={rc}")
    _record(
        "SOURCE_SUMMARY_HEADER",
        "Loaded 2 enabled sources" in log_output,
        "missing summary header",
    )
    _record(
        "SOURCE_SUMMARY_LABELS",
        "stub_acme" in log_output or "Acme" in log_output,
        "missing per-source label",
    )


if __name__ == "__main__":
    test_telegram_guard_truthy_values()
    test_telegram_guard_falsy_values()
    test_telegram_guard_unset()
    test_empty_sources_raises_runtime_error()
    test_disabled_only_sources_raises_runtime_error()
    test_dummy_mode_skips_telegram()
    test_guard_disabled_skips_telegram()
    test_source_summary_logged()
    if failures:
        print("FAILURES", failures)
        raise SystemExit(1)
    print("RUN_MONITOR_GUARDS_OK")