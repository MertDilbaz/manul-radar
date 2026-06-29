"""Telegram notifier — sends job alerts and digest summaries via Bot API.

V3 (2026-06-29): the message format is intentionally minimal so a
phone notification carries only what matters to Mert.

**Summary message** (sent first when there is at least one relevant
job, or sent alone when there are none)::

    🐈 Manul Sentinel

    Günaydın Mert, iş ilanı taraması tamamlandı.

    🔍 Taranan ilan: 195
    ⭐ Uygun yeni ilan: 4

**Per-job card** (one per page slot, paged across multiple Telegram
messages if needed)::

    1) Software Engineer
    🏢 Midas
    📍 İstanbul, Turkey
    💼 Full-Time | İş modeli: Hybrid
    ⭐ Skor: 93
    ✅ Eşleşenler: turkey, istanbul, midas, software engineer
    🔗 İlanı Aç

Confidence tiers (``high``/``medium``/``low``) and the per-job
``Neden`` reason list are still computed by
:class:`app.filters.job_scorer.JobScorer` and stored on
:class:`app.models.scored_job.ScoredJob`, but they are **never**
rendered into the Telegram payload. The operator can still see
them in the ``data/jobs.db`` SQLite repository or in the runner's
top-rejected log if needed.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape

import requests

from app.models.scored_job import ScoredJob


_API_BASE = "https://api.telegram.org"
_TELEGRAM_HARD_LIMIT = 4096
_DEFAULT_SAFE_LIMIT = 3800


# Commitment / work-model signal vocabularies. These are matched
# case-insensitively against the job's ``work_type`` field and (if
# that does not yield a label) the description text.
_COMMITMENT_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Full-Time", ("full time", "full-time", "fulltime", "tam zamanli", "tam zamanlı")),
    ("Part-Time", ("part time", "part-time", "parttime", "yarı zamanli", "yarı zamanlı")),
    ("Internship", ("intern", "internship", "staj", "stajyer")),
    ("Contract", ("contract", "kontrat", "freelance", "sözleşmeli", "sozlesmeli")),
    ("Temporary", ("temporary", "geçici", "gecici")),
)

_WORK_MODEL_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Remote", ("remote", "tamamen uzaktan", "work from home", "wfh")),
    ("Hybrid", ("hybrid", "hibrit")),
    ("On-site", ("on-site", "on site", "office", "ofiste", "iş yerinde", "is yerinde")),
)


def _match_label(text: str, vocab: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    """Return the first vocabulary label that appears in ``text``."""
    if not text:
        return None
    lowered = text.lower()
    for label, needles in vocab:
        for needle in needles:
            if needle in lowered:
                return label
    return None


def _split_work_arrangement(work_type: str | None, description: str | None) -> tuple[str, str]:
    """Return ``(commitment_label, work_model_label)`` for a job card.

    Looks at ``work_type`` first (the most reliable field), then
    falls back to a substring scan of ``description``. The
    commitment label answers "what kind of employment"
    (``Full-Time`` / ``Internship`` / …) while the work-model label
    answers "where the work happens"
    (``Remote`` / ``Hybrid`` / ``On-site``). When either is
    unknown the card shows ``"Belirtilmemiş"`` so the line stays
    grammatical and Mert never has to guess what a missing field
    meant.
    """
    commitment = _match_label(work_type, _COMMITMENT_LABELS) or _match_label(description, _COMMITMENT_LABELS)
    work_model = _match_label(work_type, _WORK_MODEL_LABELS) or _match_label(description, _WORK_MODEL_LABELS)
    return (commitment or "Belirtilmemiş", work_model or "Belirtilmemiş")


class TelegramNotifier:
    """Send text messages to a Telegram chat via the Bot API.

    V4: supports inline keyboard buttons via ``send_message_with_keyboard``.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: int = 15,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = int(timeout)

    @property
    def endpoint(self) -> str:
        return f"{_API_BASE}/bot{self._bot_token}/sendMessage"

    def send_message(self, text: str, *, parse_mode: str | None = None) -> None:
        """Send ``text`` to the configured chat and verify the response."""
        if len(text) > _TELEGRAM_HARD_LIMIT:
            raise ValueError(
                f"Telegram message is too long ({len(text)} chars). "
                f"Limit is {_TELEGRAM_HARD_LIMIT}. Split it before sending."
            )

        payload: dict[str, object] = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        response = requests.post(self.endpoint, json=payload, timeout=self._timeout)
        response.raise_for_status()

        body = response.json()
        if not isinstance(body, dict) or not body.get("ok", False):
            raise RuntimeError(
                f"Telegram sendMessage returned ok=False: body={body!r}"
            )

    def send_message_with_buttons(
        self,
        text: str,
        *,
        inline_keyboard: list[list[dict]] | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Send a message with inline keyboard buttons.

        ``inline_keyboard`` is a list of button rows; each row is a list
        of button dicts with at least ``text`` and ``url`` (or
        ``callback_data``). Example::

            [[{"text": "🔗 Başvur", "url": "https://..."}],
             [{"text": "⬅️ Önceki", "callback_data": "page:0"},
              {"text": "Sonraki ➡️", "callback_data": "page:2"}]]
        """
        if len(text) > _TELEGRAM_HARD_LIMIT:
            raise ValueError(
                f"Telegram message is too long ({len(text)} chars). "
                f"Limit is {_TELEGRAM_HARD_LIMIT}."
            )

        payload: dict[str, object] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        response = requests.post(self.endpoint, json=payload, timeout=self._timeout)
        response.raise_for_status()

        body = response.json()
        if not isinstance(body, dict) or not body.get("ok", False):
            raise RuntimeError(
                f"Telegram sendMessage returned ok=False: body={body!r}"
            )


def _format_keywords(scored_job: ScoredJob, *, limit: int | None = None) -> str:
    keywords = list(scored_job.matched_keywords or [])
    if not keywords:
        return "Yok"
    if limit is not None and len(keywords) > limit:
        shown = keywords[:limit]
        return ", ".join(shown) + f" +{len(keywords) - limit}"
    return ", ".join(keywords)


def _get_stat(stats: object | None, key: str, default: int = 0) -> int:
    if stats is None:
        return default
    if isinstance(stats, dict):
        value = stats.get(key, default)
    else:
        value = getattr(stats, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_scored_job_message(scored_job: ScoredJob) -> str:
    """Render one ``ScoredJob`` as a standalone Telegram alert body.

    V3 (2026-06-29): minimal format that mirrors the per-job card in
    the digest. Confidence tier / reason are intentionally omitted.
    """
    url = scored_job.job.url or "(url bilinmiyor)"
    matched = _format_keywords(scored_job)
    commitment, work_model = _split_work_arrangement(
        scored_job.job.work_type, scored_job.job.description
    )

    return (
        "🐈 Manul Sentinel\n"
        "\n"
        "Yeni fırsat bulundu.\n"
        "\n"
        f"Pozisyon: {scored_job.job.title}\n"
        f"Şirket: {scored_job.job.company}\n"
        f"Konum: {scored_job.job.location or 'Belirtilmemiş'}\n"
        f"Çalışma: {commitment} | İş modeli: {work_model}\n"
        f"Skor: {scored_job.score}\n"
        f"Eşleşenler: {matched}\n"
        "\n"
        f"Link: {url}"
    )


def _format_summary_message(
    *,
    greeting: str,
    scored_jobs: Sequence[ScoredJob],
    shown_count: int,
    stats: object | None,
    empty_message: str,
) -> str:
    """Render the minimal summary line (no confidence buckets, no filter breakdowns).

    V3 (2026-06-29): the only counter the operator sees is
    ``total_seen`` + ``new_relevant``. Confidence distribution,
    per-bucket reject counts, and "Güven etiketi ayrıca
    gösteriliyor" footers are intentionally dropped so the phone
    notification stays under a single glance.
    """
    total_seen = _get_stat(stats, "total_seen")
    new_relevant = _get_stat(stats, "new_relevant", len(scored_jobs))

    if not scored_jobs:
        body = escape(empty_message)
    else:
        body = escape(greeting)

    lines = [
        "🐈 <b>Manul Sentinel</b>",
        "",
        body,
        "",
        f"🔍 Taranan ilan: <b>{total_seen}</b>",
        f"⭐ Uygun yeni ilan: <b>{new_relevant}</b>",
    ]
    return "\n".join(lines)


def _confidence_tag(scored_job: ScoredJob) -> str:
    """Return a short emoji tag for the job's confidence tier.

    V4 (2026-06-29): confidence is back in the Telegram output after
    being dropped in V3. The tier is computed by the scorer and lives
    on ``ScoredJob.confidence``. We render it as a single emoji prefix
    so Mert can triage at a glance:

    * 🟢 = high confidence (strong stack + junior/support match)
    * 🟡 = medium confidence (some stack match, may need scrutiny)
    * ⚪ = low confidence (generic listing, or mobile without backend)
    * (no tag) = rejected / no confidence assigned
    """
    confidence = (scored_job.confidence or "").strip().lower()
    if confidence == "high":
        return "🟢"
    if confidence == "medium":
        return "🟡"
    if confidence == "low":
        return "⚪"
    return ""


def _seniority_tag(scored_job: ScoredJob) -> str:
    """Return a seniority/role emoji tag if detectable from keywords.

    Scans matched_keywords for entry-level signals and returns the
    first matching tag. This lets Mert see "this is explicitly a
    junior/new-grad/intern role" without reading the full card.
    """
    matched = " ".join(scored_job.matched_keywords or []).lower()
    if any(kw in matched for kw in ("stajyer", "intern", "staj")):
        return "🎓 Staj"
    if any(kw in matched for kw in ("yeni mezun", "new grad", "fresh graduate", "recent graduate", "graduate program")):
        return "🆕 Yeni Mezun"
    if any(kw in matched for kw in ("junior", "jr", "entry level", "baslangic", "başlangıç")):
        return "🔵 Junior"
    return ""


def _format_digest_job_item(index: int, scored_job: ScoredJob) -> str:
    """Render one job card in the V4 professional format.

    V4 changes from V3:
    * Confidence emoji (🟢/🟡/⚪) is back, shown before the title.
    * Seniority tag (🆕/🔵/🎓) shown inline when detectable.
    * Source label added so the operator knows which parser found it.
    """
    job = scored_job.job
    location = job.location or "Belirtilmemiş"
    matched = _format_keywords(scored_job, limit=7)
    commitment, work_model = _split_work_arrangement(job.work_type, job.description)

    conf_tag = _confidence_tag(scored_job)
    seniority = _seniority_tag(scored_job)
    source_label = escape(job.source or "")

    title = escape(job.title or "Başlık yok")
    company = escape(job.company or "Şirket yok")
    location = escape(location)
    commitment = escape(commitment)
    work_model = escape(work_model)
    matched = escape(matched)
    url = escape(job.url or "")

    # Build tag line: 🟢 🔵 Junior  |  github:lever_midas
    tags_parts = []
    if conf_tag:
        tags_parts.append(conf_tag)
    if seniority:
        tags_parts.append(seniority)
    if source_label:
        tags_parts.append(f"📡 {source_label}")
    tag_line = "  ".join(tags_parts)

    link = f'<a href="{url}">İlanı Aç</a>' if url else "Link yok"

    header = f"<b>{index}) {title}</b>"
    if tag_line:
        header = f"{tag_line}\n{header}"

    return (
        f"{header}\n"
        f"🏢 {company}\n"
        f"📍 {location}\n"
        f"💼 {commitment} | İş modeli: {work_model}\n"
        f"⭐ Skor: <b>{scored_job.score}</b>\n"
        f"✅ Eşleşenler: {matched}\n"
        f"🔗 {link}"
    )


def format_job_digest_page_messages(
    scored_jobs: Sequence[ScoredJob],
    *,
    greeting: str = "Günaydın Mert, iş ilanı taraması tamamlandı.",
    jobs_per_page: int = 4,
    max_pages: int = 3,
    max_jobs: int | None = 12,
    safe_char_limit: int = _DEFAULT_SAFE_LIMIT,
    stats: object | None = None,
    empty_message: str = "Günaydın Mert. Bugün filtrelerine uygun yeni iş ilanı bulamadım.",
    send_empty_report: bool = True,
    with_buttons: bool = False,
) -> list[str] | list[tuple[str, list[list[dict]]]]:
    """Render a readable Telegram digest: summary + paged job cards.

    V4: When ``with_buttons=True``, returns a list of
    ``(message_text, inline_keyboard)`` tuples instead of plain
    strings. Each page gets a row of URL buttons (one per job on
    that page) so the operator can apply directly from Telegram
    instead of copying links.

    This is intentionally static pagination. It does not require a
    continuously running bot process, so it works well with GitHub
    Actions scheduled runs.
    """
    jobs = sorted(list(scored_jobs), key=lambda item: item.score, reverse=True)
    if max_jobs is not None and max_jobs > 0:
        jobs = jobs[:max_jobs]

    if not jobs:
        if not send_empty_report:
            return []
        summary = _format_summary_message(
            greeting=greeting,
            scored_jobs=[],
            shown_count=0,
            stats=stats,
            empty_message=empty_message,
        )
        if with_buttons:
            return [(summary, [])]
        return [summary]

    jobs_per_page = max(1, int(jobs_per_page))
    max_pages = max(1, int(max_pages))
    max_visible = jobs_per_page * max_pages
    visible_jobs = jobs[:max_visible]

    summary = _format_summary_message(
        greeting=greeting,
        scored_jobs=visible_jobs,
        shown_count=len(visible_jobs),
        stats=stats,
        empty_message=empty_message,
    )

    if with_buttons:
        results: list[tuple[str, list[list[dict]]]] = [(summary, [])]
    else:
        messages: list[str] = [summary]

    total_pages = (len(visible_jobs) + jobs_per_page - 1) // jobs_per_page
    for page_index in range(total_pages):
        page_jobs = visible_jobs[
            page_index * jobs_per_page : (page_index + 1) * jobs_per_page
        ]
        lines = [
            "━━━━━━━━━━━━━━",
            f"📄 <b>İş İlanları — Sayfa {page_index + 1}/{total_pages}</b>",
            "━━━━━━━━━━━━━━",
            "",
        ]
        # Build inline buttons for this page — one "Apply" button per job
        button_row: list[dict] = []
        for local_index, scored_job in enumerate(page_jobs, start=1):
            absolute_index = page_index * jobs_per_page + local_index
            lines.append(_format_digest_job_item(absolute_index, scored_job))
            lines.append("")
            lines.append("━━━━━━━━━━━━━━")
            lines.append("")
            # Add a URL button for this job
            if scored_job.job.url:
                button_row.append({
                    "text": f"{absolute_index}) Başvur",
                    "url": scored_job.job.url,
                })

        if page_index == total_pages - 1 and len(jobs) > len(visible_jobs):
            lines.append(
                f"Not: {len(jobs) - len(visible_jobs)} ilan daha vardı; "
                "mesajı kısa tutmak için gösterilmedi."
            )

        message = "\n".join(lines).strip()
        if len(message) > min(safe_char_limit, _TELEGRAM_HARD_LIMIT):
            message = message[: min(safe_char_limit, _TELEGRAM_HARD_LIMIT) - 120].rstrip()
            message += "\n\n[Bu sayfa Telegram limitine yaklaşınca kısaltıldı.]"

        if with_buttons:
            keyboard = [button_row] if button_row else []
            results.append((message, keyboard))
        else:
            messages.append(message)

    if with_buttons:
        return results
    return messages


def format_job_digest_messages(
    scored_jobs: Sequence[ScoredJob],
    *,
    greeting: str = "Günaydın Mert, iş ilanı taraması tamamlandı.",
    max_jobs: int | None = 20,
    safe_char_limit: int = _DEFAULT_SAFE_LIMIT,
) -> list[str]:
    """Backward-compatible wrapper around the paged digest formatter."""
    return format_job_digest_page_messages(
        scored_jobs,
        greeting=greeting,
        jobs_per_page=max_jobs or 20,
        max_pages=1,
        max_jobs=max_jobs,
        safe_char_limit=safe_char_limit,
        send_empty_report=False,
    )


__all__ = [
    "TelegramNotifier",
    "format_scored_job_message",
    "format_job_digest_messages",
    "format_job_digest_page_messages",
    "_split_work_arrangement",
    "_confidence_tag",
    "_seniority_tag",
]