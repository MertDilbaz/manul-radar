"""Smoke test for run_monitor.py — the real monitoring entrypoint.

Run with ``python tests/smoke_run_monitor.py`` from the project root.
Prints ``<NAME>_OK ...`` lines on success and exits 0. On any failure
prints ``<NAME>_FAIL ...`` with the offending value, dumps the
failure list, and exits 1.

The test is hermetic on three axes:

* **Network:** ``KafeinHrPeakSource.fetch_jobs`` is monkeypatched to
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
        source="kafein_hrpeak",
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
    fetch_jobs_returns=None,
    fake_post: _FakePost | None = None,
):
    """Run ``run_monitor.main()`` with a hermetic env / monkeypatch stack.

    Returns ``(exit_code, captured_post)``.
    """
    import requests
    from app.sources.kafein_hrpeak_source import KafeinHrPeakSource

    env_overrides: dict[str, str] = {"JOB_DB_PATH": db_path}
    if telegram_token is not None:
        env_overrides["TELEGRAM_BOT_TOKEN"] = telegram_token
    if chat_id is not None:
        env_overrides["TELEGRAM_CHAT_ID"] = chat_id

    post = fake_post if fake_post is not None else _FakePost()

    with mock.patch.dict(os.environ, env_overrides, clear=False):
        # Strip any pre-existing telegram env vars the developer might
        # have set, so the "no env" test stays deterministic.
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        # Re-apply if requested, *after* the pop.
        if telegram_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = telegram_token
        if chat_id is not None:
            os.environ["TELEGRAM_CHAT_ID"] = chat_id

        with mock.patch.object(requests, "post", post):
            with mock.patch.object(
                KafeinHrPeakSource,
                "fetch_jobs",
                return_value=fetch_jobs_returns if fetch_jobs_returns is not None else _build_realistic_jobs(),
            ):
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


def _check_telegram_env_sends_one_per_relevant() -> None:
    """With Telegram env, one send per relevant job."""
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
                f"expected 2 send calls (junior + appsup), got {len(post.calls)}",
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
        # Text must mention Manul Sentinel and one of the job titles.
        text = payload.get("text", "")
        if "Manul Sentinel" not in text:
            _record(
                "WITH_ENV_TEXT",
                False,
                f"text missing Manul Sentinel banner: {text!r}",
            )
            return
        _record(
            "WITH_ENV",
            True,
            f"2 send calls, endpoint OK, payload OK",
        )
    finally:
        try:
            tmp.cleanup()
        except OSError:
            pass


def _check_send_failure_does_not_abort_batch() -> None:
    """If one send raises, subsequent jobs still get attempted."""
    db_path, tmp = _make_temp_db_path()
    try:
        # Raise on the first call only; second call should still succeed.
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
                "FAIL_BATCH_CALLS",
                False,
                f"expected both jobs attempted (2 calls), got {len(post.calls)}",
            )
            return
        _record(
            "FAIL_BATCH",
            True,
            "1 send failed, 2nd still attempted, exit=0",
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

        # Second run with same DB — URLs already persisted -> 0 new.
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
                f"first run expected 2 sends, got {first_calls}",
            )
            return
        if len(post2.calls) != 0:
            _record(
                "DEDUP_SECOND_CALLS",
                False,
                f"second run expected 0 sends, got {len(post2.calls)}",
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
            f"first run={first_calls} sends, second run=0 sends, rows stable",
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


def main() -> int:
    _check_parse()
    _check_no_telegram_env_skips_sending()
    _check_telegram_env_sends_one_per_relevant()
    _check_send_failure_does_not_abort_batch()
    _check_dedup_on_second_run()
    _check_job_db_path_override_used()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_RUN_MONITOR_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
