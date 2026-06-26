"""Manual monitoring entrypoint for Manul Radar.

This is the *real* monitoring runner, as opposed to ``main.py`` which
is the deterministic smoke target. It wires the full V1 pipeline:

    KafeinHrPeakSource -> JobScorer -> JobMonitorService -> JobRepository
                                                    |
                                                    +-> TelegramNotifier (optional)

Run it with::

    python run_monitor.py

For a Telegram connectivity check without running the monitoring
workflow, use::

    python run_monitor.py --test-telegram

Telegram delivery is opt-in. The bot token and chat id are read from
the environment under names declared in ``config.yaml`` under
``telegram.token_env`` / ``telegram.chat_id_env`` — the config holds
*names*, never *secrets*. If either env var is missing we log a
warning, skip notification, and let the monitoring run complete
normally (the repository is still updated, scores still computed).
This makes the script safe to invoke from CI smoke checks where no
real Telegram token is wired in.

The SQLite database path can be overridden with the ``JOB_DB_PATH``
environment variable (default: ``data/jobs.db``). The smoke test
relies on this to keep runs hermetic; production deployments can
point at a different location without editing source.
"""
from __future__ import annotations

import argparse
import os

from app.config.config_loader import load_config
from app.database.job_repository import JobRepository
from app.filters.job_scorer import JobScorer
from app.notifier.telegram_notifier import (
    TelegramNotifier,
    format_scored_job_message,
)
from app.services.job_monitor_service import JobMonitorService
from app.sources.dummy_source import DummySource
from app.sources.kafein_hrpeak_source import KafeinHrPeakSource
from app.utils.logger import logger, setup_logging


_DEFAULT_DB_PATH = "data/jobs.db"


def _build_scorer(config: dict) -> JobScorer:
    """Construct a ``JobScorer`` from the loaded config dict.

    See ``main.py._build_scorer`` for the matching implementation;
    this is duplicated here intentionally so this entrypoint stays
    self-contained and ``main.py`` (the smoke target) cannot be
    changed accidentally by edits aimed at the real runner.
    """
    scoring_cfg = config.get("scoring") or {}
    keywords_cfg = config.get("keywords") or {}

    include = list(keywords_cfg.get("include") or [])
    exclude = list(keywords_cfg.get("exclude") or [])
    minimum_score = int(scoring_cfg.get("minimum_score", 0))
    include_weight = int(scoring_cfg.get("include_weight", 20))
    exclude_weight = int(scoring_cfg.get("exclude_weight", 40))

    return JobScorer(
        include_keywords=include,
        exclude_keywords=exclude,
        minimum_score=minimum_score,
        include_weight=include_weight,
        exclude_weight=exclude_weight,
    )


def _try_build_notifier(config: dict) -> TelegramNotifier | None:
    """Build a ``TelegramNotifier`` if the env vars are populated.

    Reads ``config.telegram.token_env`` / ``config.telegram.chat_id_env``
    for the env-var *names* (never the secrets themselves), then
    resolves them via ``os.environ``. Returns ``None`` if either
    name is missing from config *or* either env var is unset, so the
    caller can skip notification entirely instead of crashing on a
    partial / misconfigured setup.
    """
    telegram_cfg = config.get("telegram") or {}
    token_env_name = telegram_cfg.get("token_env")
    chat_id_env_name = telegram_cfg.get("chat_id_env")

    if not token_env_name or not chat_id_env_name:
        logger.warning(
            "telegram.token_env or telegram.chat_id_env missing from "
            "config.yaml; TelegramNotifier will not be created."
        )
        return None

    bot_token = os.environ.get(token_env_name)
    chat_id = os.environ.get(chat_id_env_name)

    if not bot_token or not chat_id:
        logger.warning(
            f"Telegram env vars not set "
            f"({token_env_name}={'<set>' if bot_token else '<missing>'}, "
            f"{chat_id_env_name}={'<set>' if chat_id else '<missing>'}); "
            "monitoring will run without notifications."
        )
        return None

    logger.info(
        f"TelegramNotifier ready "
        f"(chat_id_env={chat_id_env_name}, bot_token_env={token_env_name})."
    )
    return TelegramNotifier(bot_token=bot_token, chat_id=chat_id)


def _resolve_telegram_env_names(config: dict) -> tuple[str | None, str | None]:
    """Read env-var *names* from config.telegram.

    The config holds the *names* of the env vars to read, never the
    secrets themselves. Returns ``(None, None)`` if either name is
    missing from the config.
    """
    telegram_cfg = config.get("telegram") or {}
    token_env_name = telegram_cfg.get("token_env")
    chat_id_env_name = telegram_cfg.get("chat_id_env")
    return token_env_name, chat_id_env_name


def _build_test_telegram_notifier(config: dict) -> TelegramNotifier:
    """Build a ``TelegramNotifier`` for the ``--test-telegram`` mode.

    Unlike :func:`_try_build_notifier` (which silently returns ``None``
    on missing config so the normal monitor can keep running), this
    helper *requires* a working setup: missing config keys, missing
    env-var names, or unset env vars all raise ``SystemExit(1)`` via
    the logger so a misconfiguration is loud and visible instead of
    being swallowed.
    """
    token_env_name, chat_id_env_name = _resolve_telegram_env_names(config)

    if not token_env_name or not chat_id_env_name:
        logger.error(
            "telegram.token_env or telegram.chat_id_env missing from "
            "config.yaml; cannot run --test-telegram."
        )
        raise SystemExit(1)

    bot_token = os.environ.get(token_env_name)
    chat_id = os.environ.get(chat_id_env_name)

    if not bot_token or not chat_id:
        logger.error(
            f"Telegram env vars not set "
            f"({token_env_name}={'<set>' if bot_token else '<missing>'}, "
            f"{chat_id_env_name}={'<set>' if chat_id else '<missing>'})."
        )
        raise SystemExit(1)

    logger.info(
        f"TelegramNotifier ready for test "
        f"(chat_id_env={chat_id_env_name}, bot_token_env={token_env_name})."
    )
    return TelegramNotifier(bot_token=bot_token, chat_id=chat_id)


_TEST_TELEGRAM_MESSAGE = (
    "🐈 Manul Sentinel\n"
    "\n"
    "Telegram bağlantısı başarıyla kuruldu.\n"
    "Bu mesaj Manul Radar test mesajıdır."
)


def run_test_telegram() -> int:
    """Send a single Telegram test message and return an exit code.

    The monitoring workflow is intentionally *not* started here:
    ``--test-telegram`` is a connectivity check, not a run. The bot
    token / chat id are resolved the same way as a real run (config
    *names* → ``os.environ``) so a green test means the production
    notification path will also work.
    """
    setup_logging()

    logger.info("Manul Sentinel telegram test starting...")

    try:
        config = load_config()
    except FileNotFoundError as exc:
        logger.error(f"Configuration error: {exc}")
        return 1

    notifier = _build_test_telegram_notifier(config)

    try:
        notifier.send_message(_TEST_TELEGRAM_MESSAGE)
    except Exception as exc:  # noqa: BLE001 — surface any send failure
        logger.error(f"Telegram test message send failed: {exc}")
        return 1

    logger.info("Telegram test message sent successfully.")
    return 0


def _parse_args() -> argparse.Namespace:
    """Parse CLI flags.

    Kept tiny on purpose: the switches today are ``--test-telegram``
    (connectivity check, no monitoring) and ``--use-dummy-source``
    (swap the real Kafein source for the in-process ``DummySource``
    so the full persist + Telegram notification path can be exercised
    on a fresh checkout with no network access). ``main`` is the
    default, mirroring the pre-argparse behavior so existing
    invocations (``python run_monitor.py``) keep working.
    """
    parser = argparse.ArgumentParser(
        prog="run_monitor",
        description=(
            "Manul Sentinel real monitor (Kafein -> Score -> Persist -> "
            "Telegram). Use --test-telegram for a connectivity check "
            "without running the workflow, or --use-dummy-source to "
            "swap the real source for an in-process fixture."
        ),
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help=(
            "Skip monitoring; send a single Telegram test message to "
            "verify the bot token / chat id wiring."
        ),
    )
    parser.add_argument(
        "--use-dummy-source",
        action="store_true",
        help=(
            "Replace the Kafein source with DummySource. Useful for "
            "end-to-end smoke testing of the persist + Telegram path "
            "without scraping the live job board."
        ),
    )
    return parser.parse_args()


def main() -> int:
    """Run one monitoring pass and return a process exit code."""
    args = _parse_args()

    if args.test_telegram:
        return run_test_telegram()

    setup_logging()

    logger.info("Manul Sentinel real monitor starting...")

    try:
        config = load_config()
    except FileNotFoundError as exc:
        logger.error(f"Configuration error: {exc}")
        return 1

    scorer = _build_scorer(config)
    notifier = _try_build_notifier(config)

    db_path = os.environ.get("JOB_DB_PATH", _DEFAULT_DB_PATH)
    repository = JobRepository(db_path=db_path)
    try:
        repository.init_db()
    except Exception as exc:
        logger.error(f"Failed to initialize repository at {db_path}: {exc}")
        return 1
    logger.info(f"Repository ready at {repository.db_path}.")

    sources = [DummySource()] if args.use_dummy_source else [KafeinHrPeakSource()]

    service = JobMonitorService(
        sources=sources,
        scorer=scorer,
        repository=repository,
    )
    new_relevant = service.run()

    logger.info(
        f"Workflow produced {len(new_relevant)} new relevant job(s)."
    )

    sent = 0
    failed = 0
    for scored in new_relevant:
        logger.info(
            f"  - [{scored.job.source}] {scored.job.title} @ "
            f"{scored.job.company} | score={scored.score}"
        )
        if notifier is None:
            continue
        try:
            message = format_scored_job_message(scored)
            notifier.send_message(message)
            sent += 1
            logger.info("    -> Telegram message sent.")
        except Exception as exc:  # noqa: BLE001 — one bad send must not abort the batch
            failed += 1
            logger.error(
                f"    -> Telegram send failed for {scored.job.url}: {exc}"
            )

    logger.info(
        f"Monitoring run complete. {sent} sent, {failed} failed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
