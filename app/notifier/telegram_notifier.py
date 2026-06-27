"""Telegram notifier — sends job alerts and digest summaries via Bot API."""
from __future__ import annotations

from collections.abc import Sequence
from html import escape

import requests

from app.models.scored_job import ScoredJob


_API_BASE = "https://api.telegram.org"
_TELEGRAM_HARD_LIMIT = 4096
_DEFAULT_SAFE_LIMIT = 3800


class TelegramNotifier:
    """Send text messages to a Telegram chat via the Bot API."""

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


def _format_keywords(scored_job: ScoredJob, *, limit: int | None = None) -> str:
    keywords = list(scored_job.matched_keywords or [])
    if not keywords:
        return "Yok"
    if limit is not None and len(keywords) > limit:
        shown = keywords[:limit]
        return ", ".join(shown) + f" +{len(keywords) - limit}"
    return ", ".join(keywords)


def format_scored_job_message(scored_job: ScoredJob) -> str:
    """Render one ``ScoredJob`` as a standalone Telegram alert body."""
    url = scored_job.job.url or "(url bilinmiyor)"
    matched = _format_keywords(scored_job)

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


def _format_summary_message(
    *,
    greeting: str,
    scored_jobs: Sequence[ScoredJob],
    shown_count: int,
    stats: object | None,
    empty_message: str,
) -> str:
    total_seen = _get_stat(stats, "total_seen")
    relevant_total = _get_stat(stats, "relevant_total", len(scored_jobs))
    new_relevant = _get_stat(stats, "new_relevant", len(scored_jobs))
    already_seen = _get_stat(stats, "already_seen")
    rejected_no_domain = _get_stat(stats, "rejected_no_domain")
    rejected_location = _get_stat(stats, "rejected_location")
    rejected_role = _get_stat(stats, "rejected_role")
    rejected_non_target = _get_stat(stats, "rejected_non_target")
    rejected_experience = _get_stat(stats, "rejected_experience")
    rejected_hard = _get_stat(stats, "rejected_hard")
    rejected_soft = _get_stat(stats, "rejected_soft")
    rejected_score = _get_stat(stats, "rejected_score")
    source_errors = _get_stat(stats, "source_errors")

    if not scored_jobs:
        body = escape(empty_message)
    else:
        body = escape(greeting)

    lines = [
        "🐈 <b>Manul Sentinel</b>",
        "",
        body,
        "",
        "━━━━━━━━━━━━━━",
        f"🔍 Taranan ilan: <b>{total_seen}</b>",
        f"⭐ Uygun yeni ilan: <b>{new_relevant}</b>",
    ]
    if relevant_total != new_relevant:
        lines.append(f"📌 Toplam uygun: <b>{relevant_total}</b>")
    if already_seen:
        lines.append(f"♻️ Daha önce görülen uygun ilan: <b>{already_seen}</b>")
    lines.extend(
        [
            f"🇹🇷 Türkiye dışı / lokasyon belirsiz elenen: <b>{rejected_location}</b>",
            f"🚫 Yazılım/IT alanı dışı elenen: <b>{rejected_no_domain}</b>",
            f"🎓 Junior/yeni mezun/support değil: <b>{rejected_role}</b>",
            f"⏳ Tecrübe/senior elenen: <b>{rejected_experience + rejected_hard}</b>",
            f"📉 Skor yetersiz elenen: <b>{rejected_score}</b>",
        ]
    )
    if rejected_non_target:
        lines.append(f"🏷️ Alan dışı etiket yakalanan: <b>{rejected_non_target}</b>")
    if rejected_soft:
        lines.append(f"➖ Negatif sinyal alan: <b>{rejected_soft}</b>")
    if source_errors:
        lines.append(f"⚠️ Hata veren kaynak: <b>{source_errors}</b>")
    lines.append("━━━━━━━━━━━━━━")
    if scored_jobs:
        lines.append(f"Aşağıda en yüksek skordan başlayarak ilk <b>{shown_count}</b> ilan var.")
    else:
        lines.append("Filtreler aktif: Türkiye içi junior/yeni mezun yazılım, backend ve yazılım destek odaklı.")
    return "\n".join(lines)


def _format_digest_job_item(index: int, scored_job: ScoredJob) -> str:
    job = scored_job.job
    location = job.location or "Belirtilmemiş"
    work_type = job.work_type or "Belirtilmemiş"
    matched = _format_keywords(scored_job, limit=7)

    title = escape(job.title or "Başlık yok")
    company = escape(job.company or "Şirket yok")
    location = escape(location)
    work_type = escape(work_type)
    matched = escape(matched)
    url = escape(job.url or "")

    link = f'<a href="{url}">İlanı Aç</a>' if url else "Link yok"

    return (
        f"<b>{index}) {title}</b>\n"
        f"🏢 {company}\n"
        f"📍 {location} | Çalışma: {work_type}\n"
        f"⭐ Skor: <b>{scored_job.score}</b>\n"
        f"✅ Eşleşenler: {matched}\n"
        f"🔗 {link}"
    )


def format_job_digest_page_messages(
    scored_jobs: Sequence[ScoredJob],
    *,
    greeting: str = "Günaydın Mert, işte sana uygun yeni iş ilanları:",
    jobs_per_page: int = 4,
    max_pages: int = 3,
    max_jobs: int | None = 12,
    safe_char_limit: int = _DEFAULT_SAFE_LIMIT,
    stats: object | None = None,
    empty_message: str = "Günaydın Mert. Bugün filtrelerine uygun yeni iş ilanı bulamadım.",
    send_empty_report: bool = True,
) -> list[str]:
    """Render a readable Telegram digest: summary + paged job cards.

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
        return [
            _format_summary_message(
                greeting=greeting,
                scored_jobs=[],
                shown_count=0,
                stats=stats,
                empty_message=empty_message,
            )
        ]

    jobs_per_page = max(1, int(jobs_per_page))
    max_pages = max(1, int(max_pages))
    max_visible = jobs_per_page * max_pages
    visible_jobs = jobs[:max_visible]

    messages = [
        _format_summary_message(
            greeting=greeting,
            scored_jobs=visible_jobs,
            shown_count=len(visible_jobs),
            stats=stats,
            empty_message=empty_message,
        )
    ]

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
        for local_index, scored_job in enumerate(page_jobs, start=1):
            absolute_index = page_index * jobs_per_page + local_index
            lines.append(_format_digest_job_item(absolute_index, scored_job))
            lines.append("")
            lines.append("━━━━━━━━━━━━━━")
            lines.append("")

        if page_index == total_pages - 1 and len(jobs) > len(visible_jobs):
            lines.append(
                f"Not: {len(jobs) - len(visible_jobs)} ilan daha vardı; "
                "mesajı kısa tutmak için gösterilmedi."
            )

        message = "\n".join(lines).strip()
        if len(message) > min(safe_char_limit, _TELEGRAM_HARD_LIMIT):
            message = message[: min(safe_char_limit, _TELEGRAM_HARD_LIMIT) - 120].rstrip()
            message += "\n\n[Bu sayfa Telegram limitine yaklaşınca kısaltıldı.]"
        messages.append(message)

    return messages


def format_job_digest_messages(
    scored_jobs: Sequence[ScoredJob],
    *,
    greeting: str = "Günaydın Mert, işte sana uygun yeni iş ilanları:",
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
]
