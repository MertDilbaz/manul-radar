# CHANGELOG — Manul Radar

Tarih sıralı, geriye dönük uyumlu. Breaking change'ler `⚠️` ile işaretli.

---

## 2026-06-29 — Scoring V2: tiered weights + confidence + exclusive buckets

### Added

- **`ScoredJob.confidence` + `confidence_reasons`** — every relevant job now carries a human-readable tier (`"high"` / `"medium"` / `"low"`) and a short Turkish reason list. The Telegram notifier surfaces both directly so Mert can tell at a glance whether a "Software Engineer" hit is a strong junior+java match or a generic low-confidence listing.
- **V2 tiered weights** — `JobScorer` now distinguishes between signal tiers instead of treating every `include` keyword as equal:
  - `strong_weight = 25` per matched specific stack keyword (java, sql, backend, application support, integration, junior, new grad, intern …).
  - `weak_weight = 8` per matched generic keyword (software engineer, software developer, yazılım mühendisi, ai software engineer …).
  - `location_weight = 10` per matched location keyword.
  - `company_boost_weight = 10` per matched high-priority company (commencis, midas, insider, iyzico …) so a generic SE listing from a priority company still surfaces at `low` confidence.
  - `mobile_penalty = 25` when iOS / Android / React Native / Flutter is in the title or description **and** no strong backend / Java / support signal is present. Mobile is **not** a hard reject — the posting still passes if its score stays above the threshold.
  - `generic_only_penalty = 25` when only weak / generic signals are present.
  - `high_confidence_min_score = 80` + `high_confidence_min_strong = 1` — gates for the `high` confidence tier.
- **New keyword buckets in `config.yaml`** — `weak_keywords`, `company_boost_keywords`, `mobile_negative_keywords`. Any keyword listed in both `include` and `weak_keywords` is treated as weak (down-weighted).
- **Telegram job card `Güven` + `Neden` fields** — every job card shows the confidence emoji + label (`🟢 yüksek` / `🟡 orta` / `🔴 düşük`) and a one-line `Neden: …` reason derived from `confidence_reasons`.
- **Telegram summary `Güven dağılımı` line** — `🎯 Güven dağılımı: 🟢 yüksek X, 🟡 orta Y, 🔴 düşük Z` so Mert can see at a glance how many of today's hits are confident.
- **`tests/smoke_scoring_v2.py`** — new smoke test (17 assertions) covering: junior+java+SQL → high; SQL support → high; generic SE → low or reject; company-boost delta; iOS-without-backend penalty; iOS-with-backend penalty absent; AI engineer low; senior hard reject; 5+ years hard reject; non-Turkey reject; exclusive per-bucket sum; exclusive each-bucket-one.
- **`scripts/simulate_v2_real_jobs.py`** — one-off dry-run that scores the 7 real jobs Mert reported (AI SE @ Commencis, SE @ Midas, SE iOS @ Midas, AI SE Remote @ Insider, SE @ iyzico, Junior Java Backend Developer @ Commencis, Application Support Specialist (SQL) @ iyzico) and prints the resulting score + confidence + excluded keywords. Junior+java → high (233), SQL support → high (165), generic/AI SE → low (73-83).
- **`JobMonitorStats.rejected_mobile` + `rejected_generic_only`** — new exclusive buckets for the V2 reject counters.

### Changed

- **`scoring.minimum_score`: 40 → 60.** Generic "Software Engineer" listings can no longer clear the threshold on their own; they need at least one strong stack / junior / support signal (or the company boost) to surface, and even then they land at `low` confidence.
- **`JobScorer.score()` formula** — V2: `score = strong_weight × strong_count + weak_weight × weak_count + location_weight × location_count + company_boost_weight × company_count + source_boost × source_count + strong_weight × role_count − mobile_penalty − generic_only_penalty − exclude_weight × excluded_count`. Backward-compatible: omitting the new kwargs leaves the formula equivalent to the old one (with `strong_weight = include_weight`).
- **`JobMonitorService._count_rejection()`** — now **exclusive**: every rejected job is counted in exactly one bucket based on its highest-priority reject reason (location > domain > experience > hard > non_target > mobile > role > generic_only > score). The per-bucket sum always equals `rejected_total`.
- **Telegram summary bucket names** — replaced the old overlapping "elenen" list with semantically-named buckets (e.g. `🇹🇷 Türkiye dışı / lokasyon belirsiz`, `🧱 Yazılım/IT alanı dışı`, `⏳ 4+ yıl tecrübe / senior`, `🏷️ Alan dışı etiket`, `📱 Mobil/iOS (backend sinyali yok)`, `🎓 Junior/yeni mezun/support yok`, `📉 Sadece genel başlık sinyali`, `➖ Skor eşiğin altında`).
- **`tests/smoke_run_monitor.py`** — `_run_monitor_in_temp_env` now sets `MANUL_ENABLE_TELEGRAM_SEND=true` by default so the Telegram end-to-end tests work alongside the 2026-06-29 guard. The two dummy-mode tests were rewritten to assert DB-row counts (the new guard correctly blocks all Telegram calls in dummy mode, so the send counter must be `0`).

### Not changed (intentional)

- **All 2026-06-29 Telegram production safety guards** (`MANUL_ENABLE_TELEGRAM_SEND`, `--use-dummy-source` Telegram block, source summary log, empty-source `RuntimeError`) remain intact and re-tested by `tests/smoke_run_monitor_guards.py`.
- **All source parsers** (hrpeak, successfactors, workable, greenhouse, lever, smartrecruiters, teamtailor, kariyer_net, peoplise, hirex, zoho_recruit) — unchanged; verified by `tests/smoke_ats_sources.py`.
- **Existing keyword lists** (`include`, `exclude`, `hard_exclude`, `domain_required`, `non_target_domain`, `location_required`, `location_reject`, `role_required`) — unchanged; the V2 scoring reads them as before.

### Verification

- 88 OK / 0 FAIL across `smoke_job_scorer_policy.py`, `smoke_telegram_notifier.py`, `smoke_run_monitor_guards.py`, `smoke_ats_sources.py`, `smoke_run_monitor.py`, `smoke_scoring_v2.py`.
- Real-job simulation (`scripts/simulate_v2_real_jobs.py`) confirms the V2 contract: junior+java+SQL postings surface as `high 🟢`, generic SE / AI SE postings surface as `low 🔴` (still shown, tagged appropriately) — the 5 jobs that previously appeared undifferentiated in Telegram now have a clear rank.

---

## 2026-06-29 — Production Telegram safety guards

### Added

- **`MANUL_ENABLE_TELEGRAM_SEND` env guard**: Runner refuses to call `TelegramNotifier.send_message()` unless this env var is set to a truthy value (`1` / `true` / `yes` / `on`, case-insensitive). CI workflow `job-monitor.yml` sets it to `"true"` explicitly.
- **`--use-dummy-source` Telegram block**: When the runner is in dummy mode (in-process fixture data), it logs `Telegram delivery SKIPPED: dummy source mode is active.` and refuses to call the notifier, even if the opt-in env var is set.
- **Source summary log**: Every production run now logs `Loaded N enabled sources:` followed by one `- parser / company` line per source, so CI logs make it obvious which parsers actually ran.
- **`tests/smoke_run_monitor_guards.py`**: New smoke test (22 assertions) covering truthy/falsy guard values, empty-source `RuntimeError`, dummy-mode Telegram block, env-disabled Telegram block, and source summary logging.

### Changed

- **`run_monitor._build_sources_from_config`**: Empty source list now raises `RuntimeError("No enabled job sources configured. Refusing to run dummy source in production.")` instead of `SystemExit(1)`. The error message is precise so a future incident can grep for it.
- **`.github/workflows/job-monitor.yml`**: Added `MANUL_ENABLE_TELEGRAM_SEND: "true"` to the job `env:` block with an inline comment explaining the opt-in contract.
- **`run_monitor` module docstring**: Updated to document all four safety guards (dummy mode, env opt-in, RuntimeError, source summary log).

### Not changed (intentional)

- Telegram bot token / chat id resolution: still `config.telegram.token_env` / `config.telegram.chat_id_env` env-var *names* from `config.yaml`. Secrets remain in GH Secrets, never in config.
- `--test-telegram` mode: still permitted (manual connectivity check uses a single fixed message; not gated by `MANUL_ENABLE_TELEGRAM_SEND` because it's a deliberate operator action).

---

## 2026-06-29 — Sprint 1 new source parsers (iyzico, Logo, Papara, İnnova + best-effort Param)

See `docs/PLAN_next_sources.md` for the Sprint 1 outcome. Summary: 6 new companies wired into `companies.yaml`, 3 new parsers (peoplise, hirex, zoho_recruit) added, `hrpeak_source` gained `/jobs` listing path support, Peoplise absolute-URL bug fixed.

---

## 2026-06-26 — Manul Radar initial stabilization

Refactor + docs pass. See `docs/ARCHITECTURE.md` for the V1 pipeline layout, `docs/SOURCES.md` for the parser catalog, and `docs/ROADMAP.md` for the current phase.