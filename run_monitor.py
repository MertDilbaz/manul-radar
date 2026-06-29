"""Manual monitoring entrypoint for Manul Radar.

This is the *real* monitoring runner, as opposed to ``main.py`` which
is the deterministic smoke target. It wires the full V1 pipeline:

    <config-driven sources> -> JobScorer -> JobMonitorService -> JobRepository
                                                    |
                                                    +-> TelegramNotifier (optional)

Run it with::

    python run_monitor.py

For a Telegram connectivity check without running the monitoring
workflow, use::

    python run_monitor.py --test-telegram

To exercise the full persist path with deterministic fixture data
instead of the live sources, use::

    python run_monitor.py --use-dummy-source

Production safety guards (added 2026-06-29):

* ``--use-dummy-source`` runs ``DummySource`` instead of the live
  parsers. In that mode Telegram delivery is *forbidden* — the
  runner logs a warning and skips ``notifier.send_message`` so
  fixture jobs can never leak to real users.
* Telegram delivery is also gated on ``MANUL_ENABLE_TELEGRAM_SEND``
  being set to a truthy value (``"1"`` / ``"true"`` / ``"yes"`` /
  ``"on"``, case-insensitive). The CI workflow ``job-monitor.yml``
  sets this explicitly; a local checkout without it will run the
  monitor + persist path but skip notifications.
* If config + companies yield zero enabled sources, the runner
  raises ``RuntimeError("No enabled job sources configured.
  Refusing to run dummy source in production.")`` instead of
  silently falling back to ``DummySource``.
* The runner logs the loaded source list at INFO level
  (``Loaded N enabled sources: ...``) so an operator can tell from
  the CI log which parsers actually ran.

Telegram delivery uses bot token / chat id env vars whose *names*
are declared in ``config.yaml`` under ``telegram.token_env`` /
``telegram.chat_id_env``. The config holds *names*, never *secrets*.
If either env var is missing we log a warning, skip notification,
and let the monitoring run complete normally (the repository is
still updated, scores still computed). This makes the script safe
to invoke from CI smoke checks where no real Telegram token is
wired in.

Sources are read from ``config.sources`` and ``companies.yaml``.
The current contract recognises multiple parser kinds — ``hrpeak``,
``successfactors``, ``workable``, ``greenhouse``, ``lever``,
``smartrecruiters``, ``teamtailor``, ``kariyer_net``, ``peoplise``,
``hirex`` and ``zoho_recruit`` — and instantiates one source
object per enabled entry. New parsers plug into the same dispatch
in ``_build_sources_from_config``.

The SQLite database path can be overridden with the ``JOB_DB_PATH``
environment variable (default: ``data/jobs.db``). The smoke test
relies on this to keep runs hermetic; production deployments can
point at a different location without editing source.
"""
from __future__ import annotations

import argparse
import os

from app.config.config_loader import load_config, load_optional_config
from app.config.env_loader import load_env
from app.database.job_repository import JobRepository
from app.filters.job_scorer import JobScorer
from app.notifier.telegram_notifier import (
    TelegramNotifier,
    format_job_digest_page_messages,
    format_scored_job_message,
)
from app.services.job_monitor_service import JobMonitorService
from app.sources.base_source import BaseSource
from app.sources.dummy_source import DummySource
from app.sources.greenhouse_source import GreenhouseSource
from app.sources.hirex_source import HirexSource
from app.sources.hrpeak_source import HrPeakSource
from app.sources.kariyer_net_source import KariyerNetSource
from app.sources.lever_source import LeverSource
from app.sources.peoplise_source import PeopliseSource
from app.sources.smartrecruiters_source import SmartRecruitersSource
from app.sources.successfactors_source import SuccessFactorsSource
from app.sources.teamtailor_source import TeamtailorSource
from app.sources.workable_source import WorkableSource
from app.sources.zoho_recruit_source import ZohoRecruitSource
from app.utils.logger import logger, setup_logging


_DEFAULT_DB_PATH = "data/jobs.db"


def _build_scorer(config: dict) -> JobScorer:
    """Construct a ``JobScorer`` from the loaded config dict.

    See ``main.py._build_scorer`` for the matching implementation;
    this is duplicated here intentionally so this entrypoint stays
    self-contained and ``main.py`` (the smoke target) cannot be
    changed accidentally by edits aimed at the real runner.

    V2 (2026-06-29): reads the new tiered-weight and penalty settings
    (``strong_weight``, ``weak_weight``, ``location_weight``,
    ``company_boost_weight``, ``mobile_penalty``,
    ``generic_only_penalty``, ``high_confidence_min_score``,
    ``low_confidence_min_score``) and the new keyword buckets
    (``weak_keywords``, ``company_boost_keywords``,
    ``mobile_negative_keywords``) introduced so generic
    "Software Engineer" listings no longer crowd out junior+java hits.
    Older keys remain honoured for backward compatibility.
    """
    scoring_cfg = config.get("scoring") or {}
    keywords_cfg = config.get("keywords") or {}

    include = list(keywords_cfg.get("include") or [])
    exclude = list(keywords_cfg.get("exclude") or [])
    hard_exclude = list(keywords_cfg.get("hard_exclude") or [])
    domain_required = list(keywords_cfg.get("domain_required") or [])
    non_target_domain = list(keywords_cfg.get("non_target_domain") or [])
    source_boost = list(keywords_cfg.get("source_boost") or [])
    location_required = list(keywords_cfg.get("location_required") or [])
    location_reject = list(keywords_cfg.get("location_reject") or [])
    role_required = list(keywords_cfg.get("role_required") or [])
    # V2 keyword buckets
    weak_keywords = list(keywords_cfg.get("weak_keywords") or [])
    company_boost_keywords = list(keywords_cfg.get("company_boost_keywords") or [])
    mobile_negative_keywords = list(keywords_cfg.get("mobile_negative_keywords") or [])

    minimum_score = int(scoring_cfg.get("minimum_score", 0))
    include_weight = int(scoring_cfg.get("include_weight", 20))
    exclude_weight = int(scoring_cfg.get("exclude_weight", 40))
    source_boost_weight = int(scoring_cfg.get("source_boost_weight", 8))
    hard_exclude_experience_years_raw = scoring_cfg.get(
        "hard_exclude_experience_years",
        4,
    )
    hard_exclude_experience_years = (
        int(hard_exclude_experience_years_raw)
        if hard_exclude_experience_years_raw is not None
        else None
    )

    # V2 tiered weights
    strong_weight = scoring_cfg.get("strong_weight")
    weak_weight = int(scoring_cfg.get("weak_weight", 8))
    location_weight = int(scoring_cfg.get("location_weight", 10))
    company_boost_weight = int(scoring_cfg.get("company_boost_weight", 10))
    mobile_penalty = int(scoring_cfg.get("mobile_penalty", 25))
    generic_only_penalty = int(scoring_cfg.get("generic_only_penalty", 25))
    high_confidence_min_score = int(scoring_cfg.get("high_confidence_min_score", 80))
    high_confidence_min_strong = int(scoring_cfg.get("high_confidence_min_strong", 1))
    low_confidence_min_score = int(scoring_cfg.get("low_confidence_min_score", 40))

    return JobScorer(
        include_keywords=include,
        exclude_keywords=exclude,
        minimum_score=minimum_score,
        include_weight=include_weight,
        exclude_weight=exclude_weight,
        hard_exclude_keywords=hard_exclude,
        hard_exclude_experience_years=hard_exclude_experience_years,
        domain_required_keywords=domain_required,
        non_target_domain_keywords=non_target_domain,
        source_boost_keywords=source_boost,
        source_boost_weight=source_boost_weight,
        location_required_keywords=location_required,
        location_reject_keywords=location_reject,
        role_required_keywords=role_required,
        # V2 keyword buckets
        weak_keywords=weak_keywords,
        company_boost_keywords=company_boost_keywords,
        mobile_negative_keywords=mobile_negative_keywords,
        # V2 tiered weights
        strong_weight=strong_weight,
        weak_weight=weak_weight,
        location_weight=location_weight,
        company_boost_weight=company_boost_weight,
        mobile_penalty=mobile_penalty,
        generic_only_penalty=generic_only_penalty,
        high_confidence_min_score=high_confidence_min_score,
        high_confidence_min_strong=high_confidence_min_strong,
        low_confidence_min_score=low_confidence_min_score,
    )


def _is_telegram_send_enabled() -> bool:
    """Return True if real Telegram sending is allowed for this run.

    Production safety guard: the runner refuses to call
    ``TelegramNotifier.send_message`` unless the ``MANUL_ENABLE_TELEGRAM_SEND``
    env var is explicitly set to a truthy value (``"1"``, ``"true"``,
    ``"yes"``, ``"on"``; case-insensitive). This makes it structurally
    impossible to spam real users from a local checkout, a forgotten
    test run, or a CI matrix job that doesn't opt in. CI workflows
    that *want* real delivery must set the env explicitly.
    """
    raw = os.environ.get("MANUL_ENABLE_TELEGRAM_SEND", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _try_build_notifier(config: dict) -> TelegramNotifier | None:
    """Build a ``TelegramNotifier`` if the env vars are populated.

    Reads ``config.telegram.token_env`` / ``config.telegram.chat_id_env``
    for the env-var *names* (never the secrets themselves), then
    resolves them via ``os.environ``. Returns ``None`` if either
    name is missing from config *or* either env var is unset, so the
    caller can skip notification entirely instead of crashing on a
    partial / misconfigured setup.

    Note: building the notifier does *not* mean the runner will
    actually send — the call sites in ``main()`` additionally check
    :func:`_is_telegram_send_enabled`. The two checks are layered so
    a missing opt-in env var is logged explicitly even when the
    notifier was constructed (e.g. for diagnostic purposes).
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

    if not _is_telegram_send_enabled():
        logger.warning(
            "MANUL_ENABLE_TELEGRAM_SEND is not set to a truthy value; "
            "TelegramNotifier will be constructed for diagnostics but "
            "the runner will refuse to call send_message(). Set "
            "MANUL_ENABLE_TELEGRAM_SEND=true to allow real delivery."
        )
        # We still construct the notifier so downstream code can
        # surface a consistent warning, but main() will short-circuit
        # the send path via the same env-var check.
        return TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    logger.info(
        f"TelegramNotifier ready "
        f"(chat_id_env={chat_id_env_name}, bot_token_env={token_env_name}, "
        "MANUL_ENABLE_TELEGRAM_SEND=true)."
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
    env_loaded = load_env()

    logger.info("Manul Sentinel telegram test starting...")
    logger.info(
        "Local .env loaded." if env_loaded else "No local .env loaded; using process environment only."
    )

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


def _build_sources_from_config(config: dict) -> list[BaseSource]:
    """Instantiate source objects from config.yaml + optional companies.yaml.

    ``config.sources`` remains supported for backward compatibility. The new
    ``app/config/companies.yaml`` file can also define a ``companies`` list
    using the same fields; this lets the project scale to many company boards
    without turning the main runtime config into a huge source registry.
    """
    raw_sources = config.get("sources") or []
    if not isinstance(raw_sources, list):
        logger.warning(
            "config.sources is not a list; treating as empty "
            f"(got {type(raw_sources).__name__})."
        )
        raw_sources = []

    companies_config = load_optional_config()
    raw_companies = companies_config.get("companies") or []
    if not isinstance(raw_companies, list):
        logger.warning(
            "companies.yaml companies is not a list; treating as empty "
            f"(got {type(raw_companies).__name__})."
        )
        raw_companies = []

    combined_entries: list[dict] = []
    for entry in raw_sources:
        if isinstance(entry, dict):
            combined_entries.append(entry)
        else:
            logger.warning("config.sources contains a non-mapping entry; skipping.")
    for entry in raw_companies:
        if isinstance(entry, dict):
            merged = dict(entry)
            merged.setdefault("enabled", True)
            combined_entries.append(merged)
        else:
            logger.warning("companies.yaml contains a non-mapping entry; skipping.")

    built: list[BaseSource] = []
    for index, entry in enumerate(combined_entries):
        if not entry.get("enabled", True):
            logger.info(f"sources[{index}] disabled by config; skipping.")
            continue

        parser = str(entry.get("parser") or "").strip().lower()
        company = str(entry.get("company") or "").strip()
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or entry.get("careers_url") or "").strip()

        if not parser:
            logger.warning(f"sources[{index}] has no 'parser' key; skipping.")
            continue

        try:
            if parser == "hrpeak":
                if not company or not url:
                    raise ValueError("hrpeak requires company and url")
                built.append(HrPeakSource(company_name=company, careers_url=url))
                logger.info(f"Registered source: hrpeak / {company} -> {url}")

            elif parser == "successfactors":
                if not company or not url:
                    raise ValueError("successfactors requires company and url")
                built.append(
                    SuccessFactorsSource(
                        company_name=company,
                        careers_url=url,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: successfactors / {company} -> {url}")

            elif parser == "workable":
                account = str(entry.get("account") or entry.get("slug") or "").strip()
                if not company or not (account or url):
                    raise ValueError("workable requires company and account or url")
                built.append(
                    WorkableSource(
                        company_name=company,
                        account=account or None,
                        careers_url=url or None,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: workable / {company} -> {url or account}")

            elif parser == "greenhouse":
                board_token = str(entry.get("board_token") or entry.get("token") or entry.get("slug") or "").strip()
                if not company or not board_token:
                    raise ValueError("greenhouse requires company and board_token")
                built.append(
                    GreenhouseSource(
                        company_name=company,
                        board_token=board_token,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: greenhouse / {company} -> {board_token}")

            elif parser == "lever":
                company_slug = str(entry.get("company_slug") or entry.get("slug") or "").strip()
                if not company or not company_slug:
                    raise ValueError("lever requires company and company_slug")
                built.append(
                    LeverSource(
                        company_name=company,
                        company_slug=company_slug,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: lever / {company} -> {company_slug}")

            elif parser == "smartrecruiters":
                company_slug = str(entry.get("company_slug") or entry.get("slug") or "").strip()
                if not company or not company_slug:
                    raise ValueError("smartrecruiters requires company and company_slug")
                built.append(
                    SmartRecruitersSource(
                        company_name=company,
                        company_slug=company_slug,
                        careers_url=url or None,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: smartrecruiters / {company} -> {url or company_slug}")

            elif parser == "teamtailor":
                if not company or not url:
                    raise ValueError("teamtailor requires company and url")
                built.append(
                    TeamtailorSource(
                        company_name=company,
                        careers_url=url,
                        source_name=name or None,
                    )
                )
                logger.info(f"Registered source: teamtailor / {company} -> {url}")

            elif parser == "kariyer_net":
                if not url:
                    raise ValueError("kariyer_net requires url")
                built.append(
                    KariyerNetSource(
                        search_url=url,
                        source_name=name or "kariyer_net",
                    )
                )
                logger.info(
                    f"Registered source: kariyer_net (name={name or 'kariyer_net'}) -> {url}"
                )

            elif parser == "peoplise":
                if not company or not url:
                    raise ValueError("peoplise requires company and url")
                account = str(entry.get("account") or "").strip()
                built.append(
                    PeopliseSource(
                        company_name=company,
                        careers_url=url,
                        account=account or None,
                    )
                )
                logger.info(f"Registered source: peoplise / {company} -> {url}")

            elif parser == "hirex":
                if not company or not url:
                    raise ValueError("hirex requires company and url")
                slug = str(entry.get("account") or entry.get("slug") or "").strip()
                built.append(
                    HirexSource(
                        company_name=company,
                        careers_url=url,
                        slug=slug or None,
                    )
                )
                logger.info(f"Registered source: hirex / {company} -> {url}")

            elif parser == "zoho_recruit":
                if not company or not url:
                    raise ValueError("zoho_recruit requires company and url")
                portal_name = str(
                    entry.get("portal_name") or entry.get("account") or ""
                ).strip()
                built.append(
                    ZohoRecruitSource(
                        company_name=company,
                        careers_url=url,
                        portal_name=portal_name or None,
                    )
                )
                logger.info(f"Registered source: zoho_recruit / {company} -> {url}")

            else:
                logger.warning(
                    f"Unknown parser {parser!r} at sources[{index}]; skipping. "
                    "Supported: hrpeak, successfactors, workable, greenhouse, "
                    "lever, smartrecruiters, teamtailor, kariyer_net, "
                    "peoplise, hirex, zoho_recruit."
                )
        except ValueError as exc:
            logger.warning(f"{parser} source at index {index} rejected: {exc}")
            continue

    if not built:
        logger.error(
            "No usable sources in config/companies. Add at least one enabled source."
        )
        raise RuntimeError(
            "No enabled job sources configured. "
            "Refusing to run dummy source in production."
        )

    return built

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
    env_loaded = load_env()

    logger.info("Manul Sentinel real monitor starting...")
    logger.info(
        "Local .env loaded." if env_loaded else "No local .env loaded; using process environment only."
    )

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

    sources: list[BaseSource] = (
        [DummySource()]
        if args.use_dummy_source
        else _build_sources_from_config(config)
    )

    # Loud, structured source summary so an operator can tell at a
    # glance what the runner is actually doing — especially important
    # to spot a dummy-source leak into a real Telegram run, which
    # is the failure mode this log line was introduced for.
    if args.use_dummy_source:
        logger.warning(
            "DUMMY SOURCE MODE ACTIVE: using DummySource (in-process "
            "fixture, no network). Telegram delivery is forbidden in "
            "this mode — see _send_telegram_messages() for the guard."
        )
        logger.info("Loaded 1 enabled source: dummy")
    else:
        logger.info(f"Loaded {len(sources)} enabled sources:")
        for src in sources:
            # Each source exposes either ``company_name`` (most parsers)
            # or ``name`` (e.g. DummySource); fall back gracefully.
            label = getattr(src, "company_name", None) or getattr(src, "name", "<unnamed>")
            parser = getattr(src, "__class__", type(src)).__name__
            logger.info(f"  - {parser.lower()} / {label}")

    telegram_send_allowed = _is_telegram_send_enabled()
    if not telegram_send_allowed:
        logger.warning(
            "MANUL_ENABLE_TELEGRAM_SEND is not set to a truthy value. "
            "The monitor will run and persist results, but will NOT "
            "send any Telegram messages. Set MANUL_ENABLE_TELEGRAM_SEND=true "
            "(case-insensitive) to opt in."
        )
    else:
        logger.info("MANUL_ENABLE_TELEGRAM_SEND=true — Telegram delivery is enabled.")

    scoring_cfg = config.get("scoring") or {}
    try:
        debug_rejected_limit = int(scoring_cfg.get("debug_top_rejected", 20))
    except (TypeError, ValueError):
        debug_rejected_limit = 20

    service = JobMonitorService(
        sources=sources,
        scorer=scorer,
        repository=repository,
        debug_rejected_limit=debug_rejected_limit,
    )
    new_relevant = service.run()

    logger.info(
        f"Workflow produced {len(new_relevant)} new relevant job(s)."
    )

    for scored in new_relevant:
        logger.info(
            f"  - [{scored.job.source}] {scored.job.title} @ "
            f"{scored.job.company} | score={scored.score}"
        )

    sent = 0
    failed = 0
    if (
        notifier is not None
        and not args.use_dummy_source
        and telegram_send_allowed
    ):
        notification_cfg = config.get("notification") or {}
        mode = str(notification_cfg.get("mode") or "digest_pages").strip().lower()

        if mode == "single":
            # Legacy mode: one Telegram message per job. Kept as an
            # escape hatch, but the default is digest_pages to avoid spam.
            for scored in new_relevant:
                try:
                    message = format_scored_job_message(scored)
                    notifier.send_message(message)
                    sent += 1
                    logger.info("    -> Telegram single-job message sent.")
                except Exception as exc:  # noqa: BLE001 — one bad send must not abort the batch
                    failed += 1
                    logger.error(
                        f"    -> Telegram send failed for {scored.job.url}: {exc}"
                    )
        else:
            greeting = str(
                notification_cfg.get("greeting")
                or "Günaydın Mert, işte sana uygun yeni iş ilanları:"
            )
            empty_message = str(
                notification_cfg.get("empty_message")
                or "Günaydın Mert. Bugün filtrelerine uygun yeni iş ilanı bulamadım."
            )
            send_empty_report = bool(notification_cfg.get("send_empty_report", True))

            def _cfg_int(key: str, default: int) -> int:
                try:
                    return int(notification_cfg.get(key, default))
                except (TypeError, ValueError):
                    return default

            max_jobs = _cfg_int("max_jobs_in_digest", 12)
            safe_char_limit = _cfg_int("safe_char_limit", 3800)
            jobs_per_page = _cfg_int("jobs_per_page", 4)
            max_pages = _cfg_int("max_pages", 3)

            messages = format_job_digest_page_messages(
                new_relevant,
                greeting=greeting,
                jobs_per_page=jobs_per_page,
                max_pages=max_pages,
                max_jobs=max_jobs,
                safe_char_limit=safe_char_limit,
                stats=service.last_run_stats,
                empty_message=empty_message,
                send_empty_report=send_empty_report,
            )

            for index, message in enumerate(messages, start=1):
                try:
                    notifier.send_message(message, parse_mode="HTML")
                    sent += 1
                    logger.info(
                        f"    -> Telegram digest page sent "
                        f"({index}/{len(messages)})."
                    )
                except Exception as exc:  # noqa: BLE001 — one bad send must not abort the batch
                    failed += 1
                    logger.error(
                        f"    -> Telegram digest page send failed "
                        f"({index}/{len(messages)}): {exc}"
                    )
    else:
        # Single-shot guard summary: if we end up here, the runner
        # deliberately skipped Telegram delivery. Surface the exact
        # reason so a CI log makes the cause obvious without grep.
        if args.use_dummy_source:
            logger.warning(
                "Telegram delivery SKIPPED: dummy source mode is active. "
                "Dummy data must never leave the process."
            )
        elif not telegram_send_allowed:
            logger.warning(
                "Telegram delivery SKIPPED: MANUL_ENABLE_TELEGRAM_SEND is "
                "not truthy. Monitoring + persist still ran; only the "
                "outbound notification was suppressed."
            )
        elif notifier is None:
            logger.warning(
                "Telegram delivery SKIPPED: notifier is None "
                "(missing env vars or config keys)."
            )

    logger.info(
        f"Monitoring run complete. {sent} sent, {failed} failed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
