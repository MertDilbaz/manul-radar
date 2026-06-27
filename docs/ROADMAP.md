# Manul Radar Roadmap

## Current priority

Stabilize the job-monitoring product path before adding larger assistant features. Refactor only when the current structure blocks a feature.

## Phase 1 — Job monitor stabilization

- Load `.env` automatically for local runs.
- Keep secrets out of Git.
- Tune Turkish + English scoring for backend, Java, Spring Boot, application support, technical support, ERP, and integration roles.
- Expand Kariyer.net searches.
- Keep SQLite URL-based deduplication active.
- Send Telegram only for new relevant jobs.

## Phase 2 — Scheduled monitoring

- Run the job monitor every weekday at 09:00 Turkey time.
- Use GitHub Actions, VPS cron, or another scheduler.
- Preserve `data/jobs.db` between runs so duplicate alerts are not sent repeatedly.

## Phase 3 — Assistant modules

- Weather module with daily Telegram summary.
- Telegram command bot for `/havadurumu`, `/isilanlari`, `/durum`, and `/help`.
- News, GitHub releases, Steam/Epic discounts, and other personal monitoring modules after the job flow is stable.

## Deferred

- LinkedIn scraping is intentionally skipped for now.
- AI-based job analysis is out of V1 scope.
- Large architectural rewrites are avoided unless they clearly unblock a feature.
