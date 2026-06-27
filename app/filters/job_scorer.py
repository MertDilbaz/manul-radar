"""Profile-aware job scorer for Manul Radar.

Current product scope: Turkey-focused software / IT roles that Mert can
reasonably apply to. The scorer therefore applies strict gates only for
clear deal-breakers, then uses scoring to rank opportunities:

* location must be Turkey/Türkiye, a Turkish city, or a trusted Turkey source;
* posting must be in software / IT / support / ERP-support domain;
* junior/new-grad/support signals are strong bonuses, not mandatory gates;
* senior / lead / management / non-target stacks are hard rejects;
* explicit 4+ year experience requirements are hard rejects;
* source/search terms only add a small boost after location + domain gates.
"""
from __future__ import annotations

import re

from app.filters.job_text import normalize_text
from app.models.job import Job
from app.models.scored_job import ScoredJob


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
        self._minimum_score: int = int(minimum_score)
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

        location_reasons: list[str] = []
        if self._location_required and not location_matches:
            location_reasons.append("location:not_turkey")
        if location_reject_matches:
            location_reasons.extend(f"location:{kw}" for kw in location_reject_matches[:3])

        domain_reasons: list[str] = []
        if self._domain_required and not domain_matches:
            domain_reasons.append("domain:no_technology_signal")

        # Role/entry-level/support signals are bonuses. They are intentionally
        # not hard gates because many Turkish software postings are simply
        # titled "Yazılım Geliştirici", "Yazılım Uzmanı" or
        # "Application Support Specialist" without saying junior/new-grad.
        role_reasons: list[str] = []

        non_target_reasons: list[str] = []
        # Non-target departments in the title are hard rejects. In the
        # description they are useful debug signals, but do not reject a valid
        # tech/support posting by themselves.
        if title_non_target_matches:
            non_target_reasons.extend(f"non_target:{kw}" for kw in title_non_target_matches[:5])
        elif not domain_matches:
            non_target_reasons.extend(f"non_target:{kw}" for kw in non_target_matches[:5])

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
        hard_rejected = bool(
            location_reasons
            or domain_reasons
            or role_reasons
            or non_target_reasons
            or hard_excluded
            or experience_excluded
        )

        score_value = (
            self._include_weight * len(matched)
            + self._include_weight * len(role_matches)
            + self._include_weight * len(entry_experience_matches)
            + self._source_boost_weight * len(source_matches)
            - self._exclude_weight * len(soft_excluded)
            - self._exclude_weight * len(hard_excluded)
            - self._exclude_weight * len(experience_excluded)
        )
        relevant = (not hard_rejected) and score_value >= self._minimum_score

        combined_matched: list[str] = []
        seen_matched: set[str] = set()
        for value in (
            location_matches
            + domain_matches
            + role_matches
            + entry_experience_matches
            + matched
            + source_matches
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
        )


__all__ = ["JobScorer"]
