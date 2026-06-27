"""Smoke test for Manul Sentinel's Turkey-only job scoring policy."""
from __future__ import annotations

import sys
from pathlib import Path

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


def _make_job(**overrides):
    from app.models.job import Job

    base = dict(
        title="placeholder",
        company="placeholder-co",
        location="Istanbul, Turkey",
        work_type="Hybrid",
        seniority=None,
        source="policy_test",
        url="https://example.com/job",
        description=None,
        published_at=None,
        discovered_at="2026-06-26T00:00:00",
    )
    base.update(overrides)
    return Job(**base)


def _make_policy_scorer():
    from app.filters.job_scorer import JobScorer

    return JobScorer(
        include_keywords=[
            "java",
            "spring boot",
            "backend",
            "sql",
            "application support",
            "uygulama destek",
            "yazılım destek",
            "yeni mezun",
            "junior",
            "uzman yardımcısı",
            "fresh graduate",
            "new grad",
            "yazılım geliştirici",
            "yazilim gelistirici",
            "software developer",
            "software engineer",
            "developer",
        ],
        exclude_keywords=["frontend", "business analyst"],
        hard_exclude_keywords=[
            "senior",
            "kıdemli",
            "uzman erp",
            "erp mühendisi",
            "consultant",
            "devops",
            "php",
            "wordpress",
            "laravel",
        ],
        domain_required_keywords=[
            "java",
            "spring",
            "backend",
            "yazılım",
            "yazilim",
            "software",
            "uygulama destek",
            "sql",
            "erp destek",
            "bilgi teknolojileri",
            "application support",
            "yazılım geliştirici",
            "yazilim gelistirici",
            "software developer",
            "software engineer",
            "developer",
        ],
        non_target_domain_keywords=[
            "ithalat",
            "muhasebe",
            "planlama",
            "tekstil",
            "denizcilik",
            "diş sağlığı",
            "dis sagligi",
        ],
        source_boost_keywords=["junior", "java", "yazilim destek"],
        source_boost_weight=8,
        location_required_keywords=[
            "turkey",
            "türkiye",
            "turkiye",
            "istanbul",
            "ankara",
            "izmir",
            "sap turkiye",
        ],
        location_reject_keywords=["canada", "china", "united states", "europe", "global remote"],
        role_required_keywords=[
            "junior",
            "new grad",
            "fresh graduate",
            "yeni mezun",
            "yetiştirilmek üzere",
            "uygulama destek",
            "application support",
            "yazılım destek",
            "uzman yardımcısı",
        ],
        minimum_score=40,
        include_weight=20,
        exclude_weight=40,
        hard_exclude_experience_years=4,
    )


def _check_junior_passes() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Junior Java Backend Developer",
            description="Java, Spring Boot, SQL, REST. Yeni mezun adaylara uygundur.",
        )
    )
    _record("JUNIOR_POLICY", scored.relevant, f"score={scored.score} excluded={scored.excluded_keywords}")


def _check_tech_assistant_passes() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Yazılım Destek Uzman Yardımcısı",
            description="SQL ve uygulama destek süreçlerinde yeni mezun adaylar değerlendirilecektir.",
        )
    )
    _record("TECH_ASSISTANT", scored.relevant, f"score={scored.score} excluded={scored.excluded_keywords}")


def _check_non_target_assistant_rejects() -> None:
    scorer = _make_policy_scorer()
    for title in [
        "İthalat Uzman Yardımcısı",
        "Muhasebe Uzman Yardımcısı",
        "Planlama Uzman Yardımcısı",
    ]:
        scored = scorer.score(
            _make_job(
                title=title,
                company="Alan Dışı A.Ş.",
                description="Yeni mezun uzman yardımcısı aranmaktadır.",
                source="kariyer_net_yeni_mezun_yazilim",
            )
        )
        if scored.relevant:
            _record("NON_TARGET_ASSISTANT", False, f"{title} passed score={scored.score} matched={scored.matched_keywords}")
            return
    _record("NON_TARGET_ASSISTANT", True, "import/accounting/planning assistant roles rejected")


def _check_location_gate_rejects_global() -> None:
    scorer = _make_policy_scorer()
    for location in ["Toronto, Canada", "Shanghai, China", "Remote - Europe"]:
        scored = scorer.score(
            _make_job(
                title="Junior Java Backend Developer",
                location=location,
                description="Java, Spring Boot, SQL. New grad friendly.",
            )
        )
        if scored.relevant:
            _record("LOCATION_GATE", False, f"{location} passed score={scored.score}")
            return
        if not any(value.startswith("location:") for value in scored.excluded_keywords):
            _record("LOCATION_REASON", False, f"{location} excluded={scored.excluded_keywords}")
            return
    _record("LOCATION_GATE", True, "Canada/China/Europe rejected")


def _check_unknown_location_rejects() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Junior Java Backend Developer",
            location=None,
            description="Java, Spring Boot, SQL. New grad friendly.",
        )
    )
    _record(
        "UNKNOWN_LOCATION",
        (not scored.relevant) and any(v.startswith("location:") for v in scored.excluded_keywords),
        f"score={scored.score} excluded={scored.excluded_keywords}",
    )


def _check_source_boost_cannot_bypass_gates() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Muhasebe Uzman Yardımcısı",
            location="Toronto, Canada",
            description="Yeni mezun adaylar başvurabilir.",
            source="kariyer_net_junior_java_yeni_mezun_yazilim",
        )
    )
    if scored.relevant:
        _record("SOURCE_GATE", False, f"source boost bypassed gates score={scored.score}")
        return
    if any(item.startswith("source:") for item in scored.matched_keywords):
        _record("SOURCE_GATE_MATCH", False, f"source match applied before gates: {scored.matched_keywords}")
        return
    _record("SOURCE_GATE", True, "source/search terms cannot pass bad jobs")


def _check_experience_rejects() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Junior Java Backend Developer",
            description="Java, Spring Boot, SQL bilgisi ve en az 5 yıl deneyim gereklidir.",
        )
    )
    _record(
        "EXPERIENCE_REJECT",
        (not scored.relevant) and any(item.startswith("experience:") for item in scored.excluded_keywords),
        f"excluded={scored.excluded_keywords}",
    )


def _check_php_rejects() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Junior PHP Uzmanı",
            description="PHP, Laravel ve SQL bilen yeni mezun adaylar.",
        )
    )
    _record("PHP_REJECT", (not scored.relevant) and "php" in scored.excluded_keywords, f"score={scored.score} excluded={scored.excluded_keywords}")


def _check_general_software_role_passes() -> None:
    scorer = _make_policy_scorer()
    scored = scorer.score(
        _make_job(
            title="Yazılım Geliştirici",
            description="Java, Spring Boot, Backend ve SQL bilgisi beklenmektedir. Açık senior şartı yoktur.",
        )
    )
    _record(
        "GENERAL_SOFTWARE_PASS",
        scored.relevant,
        f"score={scored.score} excluded={scored.excluded_keywords} matched={scored.matched_keywords}",
    )


def main() -> int:
    _check_junior_passes()
    _check_tech_assistant_passes()
    _check_non_target_assistant_rejects()
    _check_location_gate_rejects_global()
    _check_unknown_location_rejects()
    _check_source_boost_cannot_bypass_gates()
    _check_experience_rejects()
    _check_php_rejects()
    _check_general_software_role_passes()

    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL_SCORER_POLICY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
