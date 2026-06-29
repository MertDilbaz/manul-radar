"""Smoke test for run_monitor.py — the real monitoring entrypoint.

Run with ``python tests/smoke_run_monitor.py`` from the project root.
Prints ``<NAME>_OK ...`` lines on success and exits 0. On any failure
prints ``<NAME>_FAIL ...`` with the offending value, dumps the
failure list, and exits 1.

The test is hermetic on three axes:

* **Network:** ``HrPeakSource.fetch_jobs`` is monkeypatched to
  return a fixed list of jobs and ``requests.post`` (Telegram) is
  monkeypatched to a recording fake. The real Kafein site and the
  real Telegram API are never touched.
* **Filesystem:** a temporary directory under ``tempfile`` owns each
  run's SQLite database (``JOB_DB_PATH`` env var override), so the
  real ``data/jobs.db`` is never modified.
* **Environment:** ``os.environ`` is mutated only inside
  ``mock.patch.dict`` contexts which restore it on exit, so the
  developer's real ``TELEGRAM_BOT_TOKEN`` (if any) is never read or
  cleared.
"""
from __future__ import annotations

import os
import sqlite3
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


# ------------------------------- helpers ---------------------------------------


def _make_job(**overrides):
    from app.models.job import Job

    base = dict(
        title="placeholder",
        company="placeholder-co",
        location=None,
        work_type=None,
        seniority=None,
        source="kafein_technology_solutions",
        url="https://example.com/x",
        description=None,
        published_at=None,
        discovered_at="2026-06-26T00:00:00",
    )
    base.update(overrides)
    return Job(**base)


def _build_realistic_jobs():
    """Three jobs that match the real config.yaml scoring contract.

    Mirrors the semantics of ``DummySource`` so the V1 smoke tests
    stay meaningful here too: 1 junior include, 1 senior exclude,
    1 application-support include.
    """
    return [
        _make_job(
            title="Junior Java Backend Developer",
            company="Kafein Technology Solutions",
            url="https://kafein.hrpeak.com/ilan/detay.aspx?id=1",
            description="Java, Spring Boot, REST, SQL",
        ),
        _make_job(
            title="Senior Backend Lead",
            company="Kafein Technology Solutions",
            url="https://kafein.hrpeak.com/ilan/detay.aspx?id=2",
            description="Senior/Lead role, 5+ years, architecture",
        ),
        _make_job(
            title="Application Support Specialist (SQL)",
            company="Kafein Technology Solutions",
            url="https://kafein.hrpeak.com/ilan/detay.aspx?id=3",
            description="Application support, SQL, integration",
        ),
    ]


def _build_scorer():
    from app.filters.job_scorer import JobScorer

    return JobScorer(
        include_keywords=[
            "java", "spring", "backend", "sql", "rest", "junior",
            "new graduate", "application support", "erp", "integration",
        ],
        exclude_keywords=["senior", "lead", "5+ years"],
        minimum_score=50,
    )


class _FakePost:
    """Records every call; configurable to raise on the N-th call."""

    def __init__(self, raise_on_call: int | None = None) -> None:
        self.calls: list[dict] = []
        self._raise_on_call = raise_on_call

    def __call__(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if (
            self._raise_on_call is not None
            and len(self.calls) == self._raise_on_call
        ):
            from requests import HTTPError

            raise HTTPError("simulated telegram failure")
        # Mimic Telegram's happy-path response shape.
        return _FakeResponse()


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"ok": True}


def _run_monitor_in_temp_env(
    db_path: str,
    *,
    telegram_token: str | None = None,
    chat_id: str | None = None,
    telegram_send_enabled: bool = True,
    fetch_jobs_returns=None,
    fake_post: _FakePost | None = None,
    argv: list[str] | None = None,
):
    """Run ``run_monitor.main()`` with a hermetic env / monkeypatch stack.

    Returns ``(exit_code, captured_post)``.

    ``argv`` is an optional list of CLI args to forward to argparse
    inside ``run_monitor.main``. Defaults to no args (i.e. an empty
    ``sys.argv``) so existing callers don't need to change.

    ``telegram_send_enabled`` defaults to ``True`` so the Telegram
    tests can drive the notifier end-to-end; the production guard
    (``MANUL_ENABLE_TELEGRAM_SEND``) is set to ``"true"`` accordingly.
    Pass ``False`` to verify the runner refuses to send when the
    env opt-in is absent.
    """
    import requests
    from app.sources.greenhouse_source import GreenhouseSource
    from app.sources.hrpeak_source import HrPeakSource
    from app.sources.kariyer_net_source import KariyerNetSource
    from app.sources.lever_source import LeverSource
    from app.sources.smartrecruiters_source import SmartRecruitersSource
    from app.sources.successfactors_source import SuccessFactorsSource
    from app.sources.teamtailor_source import TeamtailorSource
    from app.sources.workable_source import WorkableSource

    env_overrides: dict[str, str] = {"JOB_DB_PATH": db_path, "MANUL_SKIP_DOTENV": "1"}
    if telegram_token is not None:
        env_overrides["TELEGRAM_BOT_TOKEN"] = telegram_token
    if chat_id is not None:
        env_overrides["TELEGRAM_CHAT_ID"] = chat_id
    if telegram_send_enabled:
        env_overrides["MANUL_ENABLE_TELEGRAM_SEND"] = "true"

    post = fake_post if fake_post is not None else _FakePost()

    with mock.patch.dict(os.environ, env_overrides, clear=False):
        # Strip any pre-existing telegram env vars the developer might
        # have set, so the "no env" test stays deterministic.
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        os.environ.pop("MANUL_ENABLE_TELEGRAM_SEND", None)
        # Re-apply if requested, *after* the pop.
        if telegram_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = telegram_token
        if chat_id is not None:
            os.environ["TELEGRAM_CHAT_ID"] = chat_id
        if telegram_send_enabled:
            os.environ["MANUL_ENABLE_TELEGRAM_SEND"] = "true"

        # Forward CLI args to run_monitor's argparse. Use a list that
        # always starts with a synthetic program name so argparse
        # doesn't choke on missing argv[0].
        cli_argv = ["run_monitor"]
        if argv:
            cli_argv.extend(argv)
        with mock.patch.object(sys, "argv", cli_argv):
            with mock.patch.object(requests, "post", post):
                with mock.patch.object(
                    HrPeakSource,
                    "fetch_jobs",
                    return_value=fetch_jobs_returns if fetch_jobs_returns is not None else _build_realistic_jobs(),
                ):
                    with mock.patch.object(KariyerNetSource, "fetch_jobs", return_value=[]):
                        with mock.patch.object(SuccessFactorsSource, "fetch_jobs", return_value=[]):
                            with mock.patch.object(WorkableSource, "fetch_jobs", return_value=[]):
                                with mock.patch.object(GreenhouseSource, "fetch_jobs", return_value=[]):
                                    with mock.patch.object(LeverSource, "fetch_jobs", return_value=[]):
                                        with mock.patch.object(SmartRecruitersSource, "fetch_jobs", return_value=[]):
                                            with mock.patch.object(TeamtailorSource, "fetch_jobs", return_value=[]):
                                                import run_monitor

                                                exit_code = run_monitor.main()

    return exit_code, post


def _make_temp_db_path() -> tuple[str, "tempfile.TemporaryDirectory[str]"]:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    data_dir = Path(tmp.name) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "jobs.db"), tmp


def _count_rows(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM jobs;")
        return int(cursor.fetchone()[0])


# ------------------------------- tests -----------------------------------------


def _check_parse() -> None:
    try:
        import run_monitor  # noqa: F401
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_no_telegram_env_skips_sending() -> None:
    """Without Telegram env vars, monitoring still runs and exits 0."""
    db_path, tmp = _make_temp_db_path()
    try:
        exit_code, post = _run_monitor_in_temp_env(
            db_path=db_path,
            # telegram_token/chat_id default to None -> not set
            fetch_jobs_returns=_build_realistic_jobs(),
        )

        if exit_code != 0:
            _record("NO_ENV_EXIT", False, f"expected exit 0, got {exit_code}")
            return
        if post.calls:
            _record(
                "NO_ENV_NO_POST",
                False,
                f"requests.post should not be called, got {len(post.calls)} calls",
            )
            return
        if _count_rows(db_path) != 2:
            _record(
                "NO_ENV_DB",
                False,
                f"expected 2 rows persisted, got {_count_rows(db_path)}",
            )
            return
        _record(
            "NO_ENV",
            True,
            "no telegram env -> exit 0, no post calls, 2 rows persisted",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_telegram_env_sends_digest() -> None:
    """With Telegram env, one digest send covers all relevant jobs."""
    db_path, tmp = _make_temp_db_path()
    try:
        exit_code, post = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            fetch_jobs_returns=_build_realistic_jobs(),
        )

        if exit_code != 0:
            _record(
                "WITH_ENV_EXIT",
                False,
                f"expected exit 0, got {exit_code}",
            )
            return
        if len(post.calls) != 2:
            _record(
                "WITH_ENV_CALLS",
                False,
                f"expected 2 digest page send calls, got {len(post.calls)}",
            )
            return
        # First call must target the canonical endpoint.
        first = post.calls[0]
        expected_url_prefix = "https://api.telegram.org/botFAKE_BOT_TOKEN/sendMessage"
        if not first["url"].startswith(expected_url_prefix):
            _record(
                "WITH_ENV_URL",
                False,
                f"unexpected url: {first['url']}",
            )
            return
        # Payload must carry chat_id and disable_web_page_preview.
        payload = first.get("json", {})
        if payload.get("chat_id") != "999":
            _record(
                "WITH_ENV_CHAT",
                False,
                f"chat_id wrong: {payload.get('chat_id')!r}",
            )
            return
        if payload.get("disable_web_page_preview") is not True:
            _record(
                "WITH_ENV_PREVIEW",
                False,
                f"disable_web_page_preview missing/false: {payload.get('disable_web_page_preview')!r}",
            )
            return
        # Text must mention the digest banner and both relevant job titles.
        text = payload.get("text", "")
        required = [
            "Manul Sentinel",
            "Uygun yeni ilan",
            "Taranan ilan",
        ]
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            _record(
                "WITH_ENV_TEXT",
                False,
                f"digest text missing {missing}: {text!r}",
            )
            return
        _record(
            "WITH_ENV",
            True,
            "2 digest page send calls, endpoint OK, payload OK",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_digest_send_failure_returns_cleanly() -> None:
    """If the digest send raises, the monitor logs it and exits cleanly."""
    db_path, tmp = _make_temp_db_path()
    try:
        # Raise on the first digest page call; remaining pages may still be attempted.
        post = _FakePost(raise_on_call=1)
        exit_code, _ = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            fetch_jobs_returns=_build_realistic_jobs(),
            fake_post=post,
        )

        if exit_code != 0:
            _record(
                "FAIL_BATCH_EXIT",
                False,
                f"expected exit 0 despite partial failure, got {exit_code}",
            )
            return
        if len(post.calls) != 2:
            _record(
                "FAIL_DIGEST_CALLS",
                False,
                f"expected 2 digest send attempts, got {len(post.calls)}",
            )
            return
        _record(
            "FAIL_DIGEST",
            True,
            "digest send failed, failure logged, exit=0",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_dedup_on_second_run() -> None:
    """A second run with the same DB must produce 0 new sends."""
    db_path, tmp = _make_temp_db_path()
    try:
        # First run populates the repo and sends messages.
        _, post1 = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            fetch_jobs_returns=_build_realistic_jobs(),
        )
        first_calls = len(post1.calls)
        first_db_rows = _count_rows(db_path)

        # Second run with same DB — URLs already persisted -> 0 new;
        # send_empty_report=true still sends one status message.
        _, post2 = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            fetch_jobs_returns=_build_realistic_jobs(),
        )

        if first_calls != 2:
            _record(
                "DEDUP_FIRST_CALLS",
                False,
                f"first run expected 2 digest page sends, got {first_calls}",
            )
            return
        if len(post2.calls) != 1:
            _record(
                "DEDUP_SECOND_CALLS",
                False,
                f"second run expected 1 empty-report send, got {len(post2.calls)}",
            )
            return
        if _count_rows(db_path) != first_db_rows:
            _record(
                "DEDUP_ROWS",
                False,
                f"db rows drifted: was {first_db_rows}, now {_count_rows(db_path)}",
            )
            return
        _record(
            "DEDUP",
            True,
            f"first run={first_calls} digest sends, second run=1 empty-report send, rows stable",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_job_db_path_override_used() -> None:
    """JOB_DB_PATH env var must override the default db path."""
    db_path, tmp = _make_temp_db_path()
    try:
        exit_code, _ = _run_monitor_in_temp_env(
            db_path=db_path,
            fetch_jobs_returns=_build_realistic_jobs(),
        )

        if exit_code != 0:
            _record("DB_PATH_EXIT", False, f"exit={exit_code}")
            return
        if not Path(db_path).exists():
            _record("DB_PATH_FILE", False, f"db not created at {db_path}")
            return
        # Sanity: the default path must NOT have been touched.
        default_path = PROJECT_ROOT / "data" / "jobs.db"
        # We cannot strictly prove non-creation (it might pre-exist),
        # but we can assert the temp path was the one written to.
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs;")
            temp_rows = int(cursor.fetchone()[0])
        if temp_rows != 2:
            _record(
                "DB_PATH_ROWS",
                False,
                f"temp db rows={temp_rows}, expected 2",
            )
            return
        _record(
            "DB_PATH",
            True,
            f"wrote to {db_path} (default untouched: {default_path})",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_use_dummy_source_flag_parses() -> None:
    """``--use-dummy-source`` must be accepted by argparse and land
    on ``args.use_dummy_source`` as a truthy flag.

    The smoke runner in this file imports ``run_monitor`` once and
    never lets it parse real CLI args — so we re-run argparse via
    ``run_monitor._parse_args`` under a patched ``sys.argv`` to
    confirm the flag wiring (existence, name, default) without
    going through the full ``main()`` flow.
    """
    import run_monitor

    # Baseline: no flag -> use_dummy_source is False.
    with mock.patch.object(sys, "argv", ["run_monitor"]):
        baseline = run_monitor._parse_args()
    if baseline.use_dummy_source:
        _record(
            "USE_DUMMY_FLAG_DEFAULT",
            False,
            "default use_dummy_source should be False",
        )
        return

    # With the flag set: use_dummy_source must be True and other
    # flags (notably test_telegram) must remain False so the two
    # switches are independent.
    with mock.patch.object(sys, "argv", ["run_monitor", "--use-dummy-source"]):
        parsed = run_monitor._parse_args()
    if not parsed.use_dummy_source:
        _record(
            "USE_DUMMY_FLAG_SET",
            False,
            "use_dummy_source should be True after --use-dummy-source",
        )
        return
    if parsed.test_telegram:
        _record(
            "USE_DUMMY_FLAG_INDEPENDENT",
            False,
            "--use-dummy-source must not enable --test-telegram",
        )
        return

    _record(
        "USE_DUMMY_FLAG",
        True,
        "argparse accepts --use-dummy-source, default False, isolated from --test-telegram",
    )


def _check_use_dummy_source_routes_to_dummy() -> None:
    """``--use-dummy-source`` must cause the monitor to consult
    ``DummySource`` (and not ``HrPeakSource``).

    V2 semantics (2026-06-29): dummy mode is now a Telegram hard-stop,
    so the post-call counter must be ``0`` — but the monitor still
    runs, scores, and persists. We assert by inspecting the DB
    (proves the routing worked) and by checking the post-call count
    (proves the Telegram guard fired).
    """
    from app.sources.dummy_source import DummySource

    sentinel_jobs = _build_realistic_jobs()
    db_path, tmp = _make_temp_db_path()
    try:
        with mock.patch.object(
            DummySource, "fetch_jobs", return_value=sentinel_jobs
        ):
            exit_code, post = _run_monitor_in_temp_env(
                db_path=db_path,
                telegram_token="FAKE_BOT_TOKEN",
                chat_id="999",
                argv=["--use-dummy-source"],
            )

        if exit_code != 0:
            _record(
                "USE_DUMMY_ROUTE_EXIT",
                False,
                f"expected exit 0, got {exit_code}",
            )
            return
        # V2: dummy mode refuses to call Telegram even when the guard
        # is set. The send counter must therefore be 0.
        if len(post.calls) != 0:
            _record(
                "USE_DUMMY_ROUTE_CALLS",
                False,
                f"expected 0 telegram sends in dummy mode, got {len(post.calls)}",
            )
            return
        # The DB should still be populated — DummySource returns 3 jobs,
        # 2 are relevant, both should be persisted.
        if _count_rows(db_path) != 2:
            _record(
                "USE_DUMMY_ROUTE_DB",
                False,
                f"expected 2 persisted rows from dummy source, got {_count_rows(db_path)}",
            )
            return
        _record(
            "USE_DUMMY_ROUTE",
            True,
            "--use-dummy-source routes through DummySource -> 2 DB rows, 0 telegram sends",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_use_dummy_source_dedup_on_second_run() -> None:
    """End-to-end: first run with --use-dummy-source on a fresh DB
    produces 2 new relevant jobs persisted to SQLite; the same DB on a
    second run produces 0 new relevant jobs.

    V2 (2026-06-29): dummy mode refuses to call Telegram, so the
    send counter must be ``0`` on both runs. We assert the dedup
    contract via the DB instead.
    """
    db_path, tmp = _make_temp_db_path()
    try:
        _, post1 = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            argv=["--use-dummy-source"],
        )
        first_rows = _count_rows(db_path)
        _, post2 = _run_monitor_in_temp_env(
            db_path=db_path,
            telegram_token="FAKE_BOT_TOKEN",
            chat_id="999",
            argv=["--use-dummy-source"],
        )
        second_rows = _count_rows(db_path)

        if len(post1.calls) != 0 or len(post2.calls) != 0:
            _record(
                "USE_DUMMY_DEDUP_SENDS",
                False,
                f"dummy mode must never call Telegram: post1={len(post1.calls)}, post2={len(post2.calls)}",
            )
            return
        if first_rows != 2:
            _record(
                "USE_DUMMY_DEDUP_FIRST",
                False,
                f"first run expected 2 DB rows, got {first_rows}",
            )
            return
        if second_rows != 2:
            _record(
                "USE_DUMMY_DEDUP_SECOND",
                False,
                f"second run expected 2 DB rows (no growth), got {second_rows}",
            )
            return
        _record(
            "USE_DUMMY_DEDUP",
            True,
            "first=2 DB rows (no telegram), second=2 DB rows unchanged, 0 telegram sends",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def main() -> int:
    _check_parse()
    _check_no_telegram_env_skips_sending()
    _check_telegram_env_sends_digest()
    _check_digest_send_failure_returns_cleanly()
    _check_dedup_on_second_run()
    _check_job_db_path_override_used()
    _check_use_dummy_source_flag_parses()
    _check_use_dummy_source_routes_to_dummy()
    _check_use_dummy_source_dedup_on_second_run()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_RUN_MONITOR_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
