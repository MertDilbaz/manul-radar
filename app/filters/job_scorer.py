"""Profile-aware job scorer for Manul Radar.

Current product scope: Turkey-focused software / IT roles that Mert can
reasonably apply to. The scorer therefore applies strict gates only for
clear deal-breakers, then uses scoring to rank opportunities:

* location must be Turkey/Türkiye, a Turkish city, or a trusted Turkey source;
* posting must be in software / IT / support / ERP-support domain;
* junior/new-grad/support signals are strong bonuses, not mandatory gates;
* senior / lead / management / non-target stacks are hard rejects;
* explicit 4+ year experience requirements are hard rejects;
* generic ``Software Engineer`` listings without stack signals are
  *not* hard rejects but score low and are tagged ``low`` confidence
  so Mert can see them without acting on them.

The V2 scoring formula (added 2026-06-29) distinguishes between signal
tiers instead of treating every ``include`` keyword as equal:

* **strong** signals (java, spring boot, sql, backend, application
  support, integration, junior, new grad, intern …) carry
  ``strong_weight`` (default 25). Two or three of these already push a
  junior-aimed posting past ``minimum_score``.
* **weak / generic** signals (``software engineer``,
  ``software developer``, ``yazılım mühendisi``) carry ``weak_weight``
  (default 8). They contribute but cannot single-handedly clear the
  threshold.
* **location** keywords (``turkey``, ``istanbul``, ``remote türkiye``
  …) carry ``location_weight`` (default 10).
* **company boost** keywords (``commencis``, ``midas``, ``insider``
  …) carry ``company_weight`` (default 10) so a generic SE listing
  from a high-priority company still surfaces as ``low`` confidence
  rather than disappearing entirely.
* **mobile / iOS / Android / React Native** signals in the title or
  description trigger ``mobile_penalty`` when there is no strong
  backend / Java / support signal — the posting is *not* rejected
  (mobile is not a hard exclude) but the score drops so it falls
  below the threshold unless paired with backend / support.
* **AI Software Engineer** without backend/java/support/junior
  signals is down-weighted by ``generic_only_penalty`` and lands at
  ``low`` confidence.

The combination of weighted tiers + targeted penalties gives every
relevant job a **confidence** label (``high`` / ``medium`` / ``low``)
plus a short ``confidence_reasons`` list that the Telegram notifier
renders directly to Mert.
"""
from __future__ import annotations

import re

from app.filters.job_text import normalize_text
from app.models.job import Job
from app.models.scored_job import Confidence, ScoredJob


class JobScorer:
    """Score ``Job`` instances against the Turkey-focused candidate policy."""

    _CONTENT_FIELDS: tuple[str, ...] = (
        "title",
        "company",
        "location",
        "work_type",
        "seniority",
        "description",
        "url",
    )
    _TITLE_FIELDS: tuple[str, ...] = ("title", "seniority")
    _SOURCE_FIELDS: tuple[str, ...] = ("source",)
    _LOCATION_FIELDS: tuple[str, ...] = (
        "location",
        "work_type",
        "description",
        "company",
        "source",
        "url",
    )

    _EXPERIENCE_RANGE_PATTERN = re.compile(
        r"(?P<low>\d{1,2})\s*[-–]\s*(?P<high>\d{1,2})\s*"
        r"(?:yil|years?|yrs?)"
    )
    _EXPERIENCE_MIN_PATTERN = re.compile(
        r"(?:en\s+az|minimum|min\.?|at\s+least)\s*"
        r"(?P<years>\d{1,2})\s*(?:\+\s*)?"
        r"(?:yil|years?|yrs?)"
    )
    _EXPERIENCE_SIMPLE_PATTERN = re.compile(
        r"(?P<years>\d{1,2})\s*(?P<plus>\+)?\s*"
        r"(?:yil|years?|yrs?)"
    )
    _ENTRY_LEVEL_EXPERIENCE_PATTERN = re.compile(
        r"(?:0|1)\s*[-–]\s*(?:1|2|3)\s*(?:yil|years?|yrs?)"
    )

    def __init__(
        self,
        include_keywords: list[str],
        exclude_keywords: list[str],
        minimum_score: int,
        include_weight: int = 20,
        exclude_weight: int = 40,
        hard_exclude_keywords: list[str] | None = None,
        hard_exclude_experience_years: int | None = None,
        domain_required_keywords: list[str] | None = None,
        non_target_domain_keywords: list[str] | None = None,
        source_boost_keywords: list[str] | None = None,
        source_boost_weight: int = 8,
        location_required_keywords: list[str] | None = None,
        location_reject_keywords: list[str] | None = None,
        role_required_keywords: list[str] | None = None,
        # ---- V2 tiered weights (optional, default to include_weight) ----
        weak_keywords: list[str] | None = None,
        strong_weight: int | None = None,
        weak_weight: int = 8,
        location_weight: int = 10,
        company_boost_keywords: list[str] | None = None,
        company_boost_weight: int = 10,
        mobile_negative_keywords: list[str] | None = None,
        mobile_penalty: int = 25,
        generic_only_penalty: int = 25,
        # ---- V2 confidence thresholds ----
        high_confidence_min_score: int = 80,
        high_confidence_min_strong: int = 1,
        low_confidence_min_score: int = 40,
    ) -> None:
        self._include: list[str] = self._normalize_list(include_keywords)
        self._exclude: list[str] = self._normalize_list(exclude_keywords)
        self._hard_exclude: list[str] = self._normalize_list(hard_exclude_keywords or [])
        self._domain_required: list[str] = self._normalize_list(domain_required_keywords or [])
        self._non_target_domain: list[str] = self._normalize_list(non_target_domain_keywords or [])
        self._source_boost: list[str] = self._normalize_list(source_boost_keywords or [])
        self._location_required: list[str] = self._normalize_list(location_required_keywords or [])
        self._location_reject: list[str] = self._normalize_list(location_reject_keywords or [])
        self._role_required: list[str] = self._normalize_list(role_required_keywords or [])
        # ---- V2 ----
        self._weak: list[str] = self._normalize_list(weak_keywords or [])
        self._company_boost: list[str] = self._normalize_list(company_boost_keywords or [])
        self._mobile_negative: list[str] = self._normalize_list(mobile_negative_keywords or [])

        self._minimum_score: int = int(minimum_score)
        # strong_weight defaults to include_weight for backward compatibility
        self._strong_weight: int = (
            int(strong_weight) if strong_weight is not None else int(include_weight)
        )
        self._weak_weight: int = int(weak_weight)
        self._location_weight: int = int(location_weight)
        self._company_boost_weight: int = int(company_boost_weight)
        self._mobile_penalty: int = int(mobile_penalty)
        self._generic_only_penalty: int = int(generic_only_penalty)
        self._high_confidence_min_score: int = int(high_confidence_min_score)
        self._high_confidence_min_strong: int = max(0, int(high_confidence_min_strong))
        self._low_confidence_min_score: int = int(low_confidence_min_score)

        # Back-compat: keep include_weight / exclude_weight / source_boost_weight
        # attributes used by older callers / tests.
        self._include_weight: int = int(include_weight)
        self._exclude_weight: int = int(exclude_weight)
        self._source_boost_weight: int = int(source_boost_weight)
        self._hard_exclude_experience_years: int | None = (
            int(hard_exclude_experience_years)
            if hard_exclude_experience_years is not None
            else None
        )

    @property
    def minimum_score(self) -> int:
        return self._minimum_score

    @property
    def hard_exclude_keywords(self) -> tuple[str, ...]:
        return tuple(self._hard_exclude)

    @property
    def soft_exclude_keywords(self) -> tuple[str, ...]:
        return tuple(self._exclude)

    @property
    def weak_keywords(self) -> tuple[str, ...]:
        return tuple(self._weak)

    @property
    def mobile_negative_keywords(self) -> tuple[str, ...]:
        return tuple(self._mobile_negative)

    @staticmethod
    def _normalize_list(keywords: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            normalized = normalize_text(keyword)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _text_from_fields(self, job: Job, fields: tuple[str, ...]) -> str:
        parts: list[str] = []
        for field_name in fields:
            parts.append(normalize_text(getattr(job, field_name, None)))
        return " ".join(part for part in parts if part)

    def _content_text(self, job: Job) -> str:
        return self._text_from_fields(job, self._CONTENT_FIELDS)

    def _title_text(self, job: Job) -> str:
        return self._text_from_fields(job, self._TITLE_FIELDS)

    def _source_text(self, job: Job) -> str:
        return self._text_from_fields(job, self._SOURCE_FIELDS)

    def _location_text(self, job: Job) -> str:
        return self._text_from_fields(job, self._LOCATION_FIELDS)

    def _match_keywords(self, text: str, keywords: list[str]) -> list[str]:
        return [kw for kw in keywords if kw and kw in text]

    def _entry_level_experience_matches(self, text: str) -> list[str]:
        matches: list[str] = []
        for match in self._ENTRY_LEVEL_EXPERIENCE_PATTERN.finditer(text):
            value = f"entry_experience:{match.group(0).strip()}"
            if value not in matches:
                matches.append(value)
        return matches

    def _experience_exclusions(self, text: str) -> list[str]:
        threshold = self._hard_exclude_experience_years
        if threshold is None or threshold <= 0:
            return []

        reasons: list[str] = []

        for match in self._EXPERIENCE_RANGE_PATTERN.finditer(text):
            high = int(match.group("high"))
            if high >= threshold:
                reasons.append(f"experience:{match.group(0).strip()}")

        for match in self._EXPERIENCE_MIN_PATTERN.finditer(text):
            years = int(match.group("years"))
            if years >= threshold:
                reasons.append(f"experience:{match.group(0).strip()}")

        for match in self._EXPERIENCE_SIMPLE_PATTERN.finditer(text):
            years = int(match.group("years"))
            has_plus = bool(match.group("plus"))
            if years >= threshold or (has_plus and years >= max(1, threshold - 1)):
                reasons.append(f"experience:{match.group(0).strip()}")

        deduped: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            if reason not in seen:
                seen.add(reason)
                deduped.append(reason)
        return deduped

    def _split_strong_weak(self, include_matches: list[str]) -> tuple[list[str], list[str]]:
        """Split matched include keywords into strong and weak buckets.

        A keyword in both ``include_keywords`` and ``weak_keywords``
        is treated as *weak* (its contribution is down-weighted). A
        keyword in ``include_keywords`` only is treated as *strong*.
        """
        weak_set = set(self._weak)
        strong: list[str] = []
        weak: list[str] = []
        for keyword in include_matches:
            if keyword in weak_set:
                weak.append(keyword)
            else:
                strong.append(keyword)
        return strong, weak

    def _confidence_for(
        self,
        *,
        score: int,
        strong_count: int,
        junior_or_support_count: int,
        mobile_penalty_applied: bool,
        generic_only: bool,
    ) -> tuple[Confidence, list[str]]:
        """Decide the confidence tier and the human-readable reasons.

        Returned tuple is ``(tier, reasons)``. The tier is always one
        of ``"high"``, ``"medium"``, ``"low"`` when the job is
        relevant. Reasons are short strings ready for Telegram output.
        """
        reasons: list[str] = []
        if score >= self._high_confidence_min_score and strong_count >= self._high_confidence_min_strong:
            if junior_or_support_count > 0:
                reasons.append("güçlü sinyal: stack + junior veya destek")
                return "high", reasons
            reasons.append("güçlü sinyal: stack eşleşmesi")
            return "high", reasons
        if strong_count == 0:
            reasons.append("sadece genel başlık sinyali")
            if generic_only:
                reasons.append("backend/java/destek/junior sinyali yok")
            return "low", reasons
        if mobile_penalty_applied and score >= self._minimum_score:
            reasons.append("mobil sinyali var ama backend/java desteği dengeledi")
            return "medium", reasons
        if junior_or_support_count > 0 and strong_count >= 1:
            reasons.append("orta güven: stack + junior veya destek")
            return "medium", reasons
        reasons.append("orta güven: stack eşleşmesi var")
        return "medium", reasons

    def score(self, job: Job) -> ScoredJob:
        """Score ``job`` and return a new ``ScoredJob`` wrapping it."""
        content_text = self._content_text(job)
        title_text = self._title_text(job)
        source_text = self._source_text(job)
        location_text = self._location_text(job)

        domain_matches = self._match_keywords(content_text, self._domain_required)
        non_target_matches = self._match_keywords(content_text, self._non_target_domain)
        title_non_target_matches = self._match_keywords(title_text, self._non_target_domain)
        location_matches = self._match_keywords(location_text, self._location_required)
        location_reject_matches = self._match_keywords(location_text, self._location_reject)
        role_matches = self._match_keywords(content_text, self._role_required)
        entry_experience_matches = self._entry_level_experience_matches(content_text)

        hard_excluded = self._match_keywords(title_text, self._hard_exclude)
        experience_excluded = self._experience_exclusions(content_text)
        soft_excluded = self._match_keywords(content_text, self._exclude)
        matched = self._match_keywords(content_text, self._include)

        # ---- V2 tiered scoring ----
        strong_matched, weak_matched = self._split_strong_weak(matched)
        company_boost_matches = self._match_keywords(source_text, self._company_boost)
        mobile_negative_matches = self._match_keywords(content_text, self._mobile_negative)

        location_reasons: list[str] = []
        if self._location_required and not location_matches:
            location_reasons.append("location:not_turkey")
        if location_reject_matches:
            location_reasons.extend(f"location:{kw}" for kw in location_reject_matches[:3])

        domain_reasons: list[str] = []
        if self._domain_required and not domain_matches:
            domain_reasons.append("domain:no_technology_signal")

        role_reasons: list[str] = []

        non_target_reasons: list[str] = []
        if title_non_target_matches:
            non_target_reasons.extend(f"non_target:{kw}" for kw in title_non_target_matches[:5])
        elif not domain_matches:
            non_target_reasons.extend(f"non_target:{kw}" for kw in non_target_matches[:5])

        # V2: penalise but do not hard-reject mobile postings without
        # any strong backend / Java / support signal. Hard rejection
        # is reserved for senior / lead / management / non-target stacks.
        mobile_penalty_applied = bool(
            mobile_negative_matches and not strong_matched and not role_matches
        )
        # V2: penalise postings whose only positive signals are weak /
        # generic (e.g. just "Software Engineer" without any specific
        # stack or support keywords). The penalty nudges them below
        # the minimum score; high-priority companies still surface
        # them via the company boost + low_confidence tier.
        generic_only = bool(weak_matched and not strong_matched and not role_matches)

        source_matches: list[str] = []
        if domain_matches and location_matches:
            source_matches = [
                f"source:{kw}"
                for kw in self._match_keywords(source_text, self._source_boost)
                if kw not in matched
            ]

        excluded = (
            soft_excluded
            + hard_excluded
            + experience_excluded
            + location_reasons
            + domain_reasons
            + role_reasons
            + non_target_reasons
        )
        if mobile_penalty_applied:
            excluded.append("mobile:no_backend_signal")
        if generic_only:
            excluded.append("generic_only:no_strong_signal")

        hard_rejected = bool(
            location_reasons
            or domain_reasons
            or role_reasons
            or non_target_reasons
            or hard_excluded
            or experience_excluded
        )

        # --- score formula (V2) ---
        score_value = (
            self._strong_weight * len(strong_matched)
            + self._weak_weight * len(weak_matched)
            + self._location_weight * len(location_matches)
            + self._company_boost_weight * len(company_boost_matches)
            + self._source_boost_weight * len(source_matches)
            + self._strong_weight * len(role_matches)
            + self._strong_weight * len(entry_experience_matches)
            - self._exclude_weight * len(soft_excluded)
            - self._exclude_weight * len(hard_excluded)
            - self._exclude_weight * len(experience_excluded)
            - (self._mobile_penalty if mobile_penalty_applied else 0)
            - (self._generic_only_penalty if generic_only else 0)
        )
        relevant = (not hard_rejected) and score_value >= self._minimum_score

        # --- confidence tier (only meaningful for relevant jobs) ---
        confidence: Confidence | str = ""
        confidence_reasons: list[str] = []
        if relevant:
            # Junior OR support signal — counts towards high confidence.
            junior_or_support_count = sum(
                1 for keyword in (role_matches + entry_experience_matches)
                if any(t in keyword for t in (
                    "junior",
                    "new grad",
                    "new graduate",
                    "fresh graduate",
                    "trainee",
                    "intern",
                    "stajyer",
                    "yeni mezun",
                    "yetistirilmek",
                    "yetiştirilmek",
                    "uzman yardimcisi",
                    "uzman yardımcısı",
                    "asistan",
                    "application support",
                    "uygulama destek",
                    "yazilim destek",
                    "yazılım destek",
                    "sql destek",
                    "erp destek",
                    "entegrasyon destek",
                    "integration support",
                    "implementation support",
                    "support",
                    "destek",
                ))
            )
            confidence, confidence_reasons = self._confidence_for(
                score=score_value,
                strong_count=len(strong_matched),
                junior_or_support_count=junior_or_support_count,
                mobile_penalty_applied=mobile_penalty_applied,
                generic_only=generic_only,
            )

        combined_matched: list[str] = []
        seen_matched: set[str] = set()
        for value in (
            location_matches
            + domain_matches
            + role_matches
            + entry_experience_matches
            + strong_matched
            + weak_matched
            + source_matches
            + company_boost_matches
        ):
            if value in seen_matched:
                continue
            seen_matched.add(value)
            combined_matched.append(value)

        return ScoredJob(
            job=job,
            score=score_value,
            matched_keywords=combined_matched,
            excluded_keywords=excluded,
            relevant=relevant,
            confidence=confidence,
            confidence_reasons=confidence_reasons,
        )


__all__ = ["JobScorer"]