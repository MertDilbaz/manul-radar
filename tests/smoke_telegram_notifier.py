"""Smoke test for TelegramNotifier and format_scored_job_message.

Run with ``python tests/smoke_telegram_notifier.py`` from the project
root. Prints ``<NAME>_OK ...`` lines on success and exits 0. On any
failure prints ``<NAME>_FAIL ...`` with the offending value, dumps
the failure list, and exits 1.

The smoke test never touches the network. It monkeypatches
``requests.post`` with a recording fake so we can verify the URL,
payload, timeout, and response-handling branches of
``send_message`` in isolation. ``format_scored_job_message`` is a
pure function and is exercised directly against a hand-built
``ScoredJob``.
"""
from __future__ import annotations

import json
import sys
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
    """Build a minimal Job for formatter / send_message tests."""
    from app.models.job import Job

    base = dict(
        title="Junior Java Backend Developer",
        company="SpringyCorp",
        location="Istanbul",
        work_type="Hybrid",
        seniority="Junior",
        source="dummy",
        url="https://example.com/jobs/1",
        description="Java, Spring Boot, REST",
        published_at="2026-06-25",
        discovered_at="2026-06-26T00:00:00",
    )
    base.update(overrides)
    return Job(**base)


def _make_scored_job(**overrides):
    from app.models.scored_job import ScoredJob

    base = dict(
        job=_make_job(),
        score=140,
        matched_keywords=["java", "spring", "backend", "sql", "rest", "junior"],
        excluded_keywords=[],
        relevant=True,
    )
    base.update(overrides)
    return ScoredJob(**base)


class _FakeResponse:
    """Minimal ``requests.Response`` stand-in for monkeypatched tests."""

    def __init__(self, *, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True}
        self.raise_called = False

    def raise_for_status(self) -> None:
        # Mimic requests: 4xx / 5xx -> HTTPError.
        from requests import HTTPError

        self.raise_called = True
        if 400 <= self.status_code:
            raise HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self) -> dict:
        return self._body


class _RecordingPost:
    """A fake ``requests.post`` that records every call.

    Returns a configurable ``_FakeResponse`` and stores the most
    recent call so the test can assert on it.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._response


# ------------------------------- tests -----------------------------------------


def _check_parse() -> None:
    try:
        from app.notifier.telegram_notifier import (  # noqa: F401
            TelegramNotifier,
            format_job_digest_messages,
            format_scored_job_message,
        )
    except Exception as exc:
        _record("PARSE", False, repr(exc))
        return
    _record("PARSE", True)


def _check_endpoint_format() -> None:
    """Endpoint must include the bot token in the URL path segment."""
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="ABC123", chat_id="42")
    expected = "https://api.telegram.org/botABC123/sendMessage"
    if notifier.endpoint != expected:
        _record("ENDPOINT", False, f"expected={expected} got={notifier.endpoint}")
        return
    _record("ENDPOINT", True, notifier.endpoint)


def _check_format_full_fields() -> None:
    """format_scored_job_message must include every expected field."""
    from app.notifier.telegram_notifier import format_scored_job_message

    scored = _make_scored_job()
    msg = format_scored_job_message(scored)

    expected_fragments = [
        "🐈 Manul Sentinel",
        "Yeni fırsat bulundu.",
        "Pozisyon: Junior Java Backend Developer",
        "Şirket: SpringyCorp",
        "Kaynak: dummy",
        "Skor: 140",
        "Eşleşenler: java, spring, backend, sql, rest, junior",
        "Link: https://example.com/jobs/1",
    ]
    missing = [f for f in expected_fragments if f not in msg]
    if missing:
        _record("FORMAT_FIELDS", False, f"missing fragments: {missing}")
        return
    _record("FORMAT_FIELDS", True, f"{len(expected_fragments)} fragments present")


def _check_format_empty_matche_keywords() -> None:
    """Empty matched_keywords must render as 'Yok' (not blank / not [])."""
    from app.notifier.telegram_notifier import format_scored_job_message

    scored = _make_scored_job(matched_keywords=[])
    msg = format_scored_job_message(scored)

    if "Eşleşenler: Yok" not in msg:
        _record("FORMAT_EMPTY", False, f"missing 'Yok' marker; got: {msg!r}")
        return
    if "[]" in msg:
        _record("FORMAT_EMPTY_BRACKETS", False, f"rendered '[]' instead of 'Yok': {msg!r}")
        return
    _record("FORMAT_EMPTY", True, "empty list -> 'Yok'")


def _check_format_missing_url_safe() -> None:
    """Missing URL must render as a placeholder, not 'None' or blank."""
    from app.notifier.telegram_notifier import format_scored_job_message

    scored = _make_scored_job()
    scored.job.url = ""  # type: ignore[assignment]
    msg = format_scored_job_message(scored)

    if "Link: (url bilinmiyor)" not in msg:
        _record(
            "FORMAT_URL_SAFE",
            False,
            f"missing url placeholder; got: {msg!r}",
        )
        return
    if "Link: None" in msg or "Link: " not in msg:
        _record(
            "FORMAT_URL_RENDER",
            False,
            f"url renders incorrectly; got: {msg!r}",
        )
        return
    _record("FORMAT_URL_SAFE", True, "empty url -> placeholder")


def _check_format_digest_message() -> None:
    """Digest formatter must compact multiple jobs into a morning summary."""
    from app.notifier.telegram_notifier import format_job_digest_messages

    messages = format_job_digest_messages(
        [_make_scored_job(), _make_scored_job(job=_make_job(title="Application Support Specialist"), score=80)],
        greeting="Günaydın Mert, işte sana uygun yeni iş ilanları:",
    )

    if len(messages) != 2:
        _record("DIGEST_COUNT", False, f"expected summary + 1 page, got {len(messages)}")
        return

    summary = messages[0]
    page = messages[1]
    expected_summary = [
        "🐈 <b>Manul Sentinel</b>",
        "Günaydın Mert",
        "Uygun yeni ilan",
    ]
    expected_page = [
        "İş İlanları",
        "1) Junior Java Backend Developer",
        "2) Application Support Specialist",
        "SpringyCorp",
        "Skor:",
        "İlanı Aç",
    ]
    missing = [f for f in expected_summary if f not in summary] + [f for f in expected_page if f not in page]
    if missing:
        _record("DIGEST_FIELDS", False, f"missing fragments: {missing}; messages={messages!r}")
        return
    _record("DIGEST", True, "2 jobs -> summary + 1 page")


def _check_send_message_calls_post_with_expected_args() -> None:
    """send_message must POST to the right URL with the expected payload."""
    import requests
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="BOT_TOKEN_X", chat_id="999", timeout=7)
    fake = _RecordingPost(_FakeResponse(body={"ok": True}))

    with mock.patch.object(requests, "post", fake):
        notifier.send_message("hello world")

    if len(fake.calls) != 1:
        _record("SEND_CALL_COUNT", False, f"expected 1 post call, got {len(fake.calls)}")
        return

    call = fake.calls[0]
    expected_url = "https://api.telegram.org/botBOT_TOKEN_X/sendMessage"
    if call["url"] != expected_url:
        _record(
            "SEND_URL",
            False,
            f"expected {expected_url} got {call['url']}",
        )
        return
    if call["timeout"] != 7:
        _record("SEND_TIMEOUT", False, f"expected timeout=7, got {call['timeout']}")
        return
    payload = call.get("json")
    if payload != {
        "chat_id": "999",
        "text": "hello world",
        "disable_web_page_preview": True,
    }:
        _record(
            "SEND_PAYLOAD",
            False,
            f"unexpected payload: {payload!r}",
        )
        return

    _record(
        "SEND_CALL",
        True,
        f"POST {call['url']} json={json.dumps(payload)} timeout={call['timeout']}",
    )


def _check_send_message_ok_false_raises_runtime_error() -> None:
    """When Telegram returns ok=False, send_message must raise RuntimeError."""
    import requests
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="BOT", chat_id="1")
    fake = _RecordingPost(
        _FakeResponse(body={"ok": False, "description": "Bad Request: chat not found"})
    )

    with mock.patch.object(requests, "post", fake):
        try:
            notifier.send_message("hi")
        except RuntimeError as exc:
            msg = str(exc)
            if "ok=False" not in msg:
                _record(
                    "SEND_OK_FALSE_MSG",
                    False,
                    f"RuntimeError lacks ok=False marker: {msg!r}",
                )
                return
            _record("SEND_OK_FALSE", True, "RuntimeError raised with ok=False marker")
            return

    _record("SEND_OK_FALSE", False, "RuntimeError was not raised on ok=False")


def _check_send_message_missing_ok_field_raises() -> None:
    """Defensive: a body without an ``ok`` key must also raise."""
    import requests
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="BOT", chat_id="1")
    fake = _RecordingPost(_FakeResponse(body={"result": "unexpected"}))

    with mock.patch.object(requests, "post", fake):
        try:
            notifier.send_message("hi")
        except RuntimeError:
            _record("SEND_OK_MISSING", True, "missing ok -> RuntimeError")
            return

    _record("SEND_OK_MISSING", False, "RuntimeError was not raised on missing ok")


def _check_send_message_http_error_propagates() -> None:
    """HTTP 4xx from Telegram must propagate via raise_for_status."""
    import requests
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(bot_token="BOT", chat_id="1")
    fake = _RecordingPost(_FakeResponse(status_code=401, body={"ok": False}))

    with mock.patch.object(requests, "post", fake):
        try:
            notifier.send_message("hi")
        except requests.HTTPError:
            # We never reach the ok=False branch because raise_for_status
            # raises first — which is exactly the contract.
            _record("SEND_HTTP_RAISE", True, "HTTPError raised via raise_for_status")
            return
        except RuntimeError:
            _record(
                "SEND_HTTP_RAISE",
                False,
                "HTTPError was swallowed and RuntimeError raised instead",
            )
            return

    _record("SEND_HTTP_RAISE", False, "no exception raised on HTTP 401")


def _check_send_message_uses_configured_timeout() -> None:
    """Default timeout is 15; constructor argument overrides it."""
    import requests
    from app.notifier.telegram_notifier import TelegramNotifier

    notifier_default = TelegramNotifier(bot_token="BOT", chat_id="1")
    fake_default = _RecordingPost(_FakeResponse())
    with mock.patch.object(requests, "post", fake_default):
        notifier_default.send_message("x")
    if fake_default.calls[0]["timeout"] != 15:
        _record(
            "TIMEOUT_DEFAULT",
            False,
            f"expected default timeout=15, got {fake_default.calls[0]['timeout']}",
        )
        return
    _record("TIMEOUT", True, "default=15s, configurable via constructor")


def main() -> int:
    _check_parse()
    _check_endpoint_format()
    _check_format_full_fields()
    _check_format_empty_matche_keywords()
    _check_format_missing_url_safe()
    _check_format_digest_message()
    _check_send_message_calls_post_with_expected_args()
    _check_send_message_ok_false_raises_runtime_error()
    _check_send_message_missing_ok_field_raises()
    _check_send_message_http_error_propagates()
    _check_send_message_uses_configured_timeout()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL_TELEGRAM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
