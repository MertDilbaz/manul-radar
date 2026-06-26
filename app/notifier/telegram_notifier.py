"""Telegram notifier — sends ``ScoredJob`` alerts via the Bot API.

The notifier is intentionally minimal: it owns the HTTP shape of a
Telegram ``sendMessage`` call and the text formatting for a job alert.
It deliberately knows nothing about scoring, persistence, or the
monitoring workflow — those are upstream concerns that hand the
notifier a finished ``ScoredJob`` (or any pre-formatted text) and let
it speak to Telegram.

Failure model:

* Network / HTTP errors (4xx, 5xx, timeouts) propagate up via
  :func:`requests.Response.raise_for_status` and the underlying
  ``requests`` exceptions. Callers (the future monitor wiring) are
  expected to log + continue so one bad message does not abort a
  batch of notifications.
* Telegram returns 200 OK even when the message itself was rejected
  (bad chat id, message too long, etc.) — the ``ok`` field on the
  JSON body is the real signal. We inspect it explicitly and raise
  :class:`RuntimeError` when it is ``False`` so the failure surfaces
  instead of being silently swallowed.

Testability:

* ``send_message`` calls ``requests.post`` directly at call time, so
  the smoke test can ``monkeypatch`` (or ``unittest.mock.patch``)
  ``requests.post`` without touching import order or DI plumbing.
* ``format_scored_job_message`` is a pure function over a
  ``ScoredJob`` — no I/O, no clock, trivially testable.
"""
from __future__ import annotations

import requests

from app.models.scored_job import ScoredJob


_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Send text messages to a Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: int = 15,
    ) -> None:
        """Store the bot token, target chat id, and request timeout.

        ``bot_token`` and ``chat_id`` are taken verbatim — this class
        does **not** resolve environment variables. The caller (e.g.
        ``main.py``) is responsible for reading
        ``os.environ[config["telegram"]["token_env"]]`` and passing
        the resulting value here, so the notifier stays free of
        implicit I/O.
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = int(timeout)

    @property
    def endpoint(self) -> str:
        """The full URL ``send_message`` POSTs to (handy for tests)."""
        return f"{_API_BASE}/bot{self._bot_token}/sendMessage"

    def send_message(self, text: str) -> None:
        """Send ``text`` to the configured chat and verify the response.

        Raises:
            requests.exceptions.RequestException: On any HTTP / network
                failure (``raise_for_status``).
            RuntimeError: When Telegram returns ``{"ok": False, ...}``
                — i.e. the message was rejected by the API even though
                the HTTP call succeeded.
        """
        url = self.endpoint
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        response = requests.post(url, json=payload, timeout=self._timeout)
        response.raise_for_status()

        # Telegram returns 200 OK even on logical errors; the ``ok``
        # field is the real signal.
        body = response.json()
        if not isinstance(body, dict) or not body.get("ok", False):
            raise RuntimeError(
                f"Telegram sendMessage returned ok=False: body={body!r}"
            )


def format_scored_job_message(scored_job: ScoredJob) -> str:
    """Render ``scored_job`` as a Telegram-friendly alert body.

    The format is the contract between the notifier and any future
    caller that wants to share the same shape (e.g. an alternative
    channel like Discord or email). Empty keyword lists collapse to
    ``"Yok"`` so the rendered message is never an awkward empty
    bullet; a missing URL becomes a placeholder instead of crashing
    or rendering ``"None"``.
    """
    matched = (
        ", ".join(scored_job.matched_keywords)
        if scored_job.matched_keywords
        else "Yok"
    )
    url = scored_job.job.url or "(url bilinmiyor)"

    return (
        "🐈 Manul Sentinel\n"
        "\n"
        "Yeni fırsat bulundu.\n"
        "\n"
        f"Pozisyon: {scored_job.job.title}\n"
        f"Şirket: {scored_job.job.company}\n"
        f"Kaynak: {scored_job.job.source}\n"
        f"Skor: {scored_job.score}\n"
        f"Eşleşenler: {matched}\n"
        "\n"
        f"Link: {url}"
    )


__all__ = ["TelegramNotifier", "format_scored_job_message"]
