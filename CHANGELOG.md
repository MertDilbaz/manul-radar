# CHANGELOG — Manul Radar

Tarih sıralı, geriye dönük uyumlu. Breaking change'ler `⚠️` ile işaretli.

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