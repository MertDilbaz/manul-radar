# Manul Sentinel

**Manul Sentinel** is a lightweight personal job monitoring assistant built with Python. It scans configured job sources, normalizes postings into a common model, scores them against a target software/IT profile, prevents duplicate notifications with SQLite, and sends clean Telegram digest messages through scheduled GitHub Actions.

The repository name is `manul-radar`; the Telegram bot/product name is **Manul Sentinel**.

## Current Scope

This version focuses only on job monitoring. Weather, news, Steam deals, HTML reports, and general chatbot features are intentionally out of scope for now.

The current profile is tuned for Turkey-based, applicable software/IT roles such as:

- Junior / new graduate software roles
- Backend / Java / Spring Boot roles
- SQL, integration, and application support roles
- ERP support and software support positions
- Non-senior software engineering opportunities that are reasonable for an early-career candidate

## Features

- Modular source architecture for multiple job boards and ATS platforms
- Common `Job` and `ScoredJob` models for normalized processing
- Rule-based scoring for software/IT relevance, seniority, location, and target role fit
- Hard filtering for non-target roles, senior/lead positions, high experience requirements, and non-Turkey locations
- SQLite-backed duplicate detection to avoid repeatedly sending the same posting
- Telegram digest notifications with summary statistics and paginated job cards
- GitHub Actions workflow for scheduled weekday monitoring
- YAML-based configuration for runtime settings and company/ATS sources
- Smoke tests for core workflow, sources, repository, Telegram formatting, and scoring policy

## Architecture

```text
Configured job sources
        ↓
Job model
        ↓
JobScorer
        ↓
ScoredJob
        ↓
JobMonitorService
        ↓
SQLite repository
        ↓
Telegram notifier
```

`main.py` is intentionally kept as a stable smoke-test entrypoint that uses `DummySource`.

Real monitoring starts from:

```bash
python run_monitor.py
```

## Supported Source Types

The runtime currently supports these source families:

- HRPeak
- SAP SuccessFactors / RMK
- Workable
- Greenhouse
- Lever
- SmartRecruiters
- Teamtailor
- Kariyer.net *(kept disabled by default because scripted runs often receive 403 Forbidden)*

Runtime/scoring settings live in:

```text
app/config/config.yaml
```

Company and ATS board definitions live in:

```text
app/config/companies.yaml
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with your real Telegram values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

`.env` is ignored by Git. Do not commit real tokens, chat IDs, logs, databases, or virtual environment files.

## Run Locally

```bash
python main.py
python run_monitor.py --test-telegram
python run_monitor.py --use-dummy-source
python run_monitor.py
```

Command behavior:

- `python main.py` runs the stable smoke workflow with dummy data.
- `python run_monitor.py --test-telegram` sends one Telegram test message.
- `python run_monitor.py --use-dummy-source` runs the full persistence + Telegram path with fixture jobs.
- `python run_monitor.py` uses the enabled sources from configuration files.

## Scheduling with GitHub Actions

The real monitor is scheduled through:

```text
.github/workflows/job-monitor.yml
```

The workflow runs Monday-Friday at **09:00 Turkey time**. GitHub cron uses UTC, so the schedule is configured as **06:00 UTC**.

Before enabling scheduled notifications, add these repository secrets in GitHub:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The workflow restores and saves `data/jobs.db` using GitHub Actions cache. This reduces duplicate notifications between scheduled runs.

## Smoke Tests

Useful tests after replacing or modifying files:

```bash
python tests/smoke_env_loader.py
python tests/smoke_workflow.py
python tests/smoke_repository.py
python tests/smoke_hrpeak_source.py
python tests/smoke_kariyer_net_source.py
python tests/smoke_successfactors_source.py
python tests/smoke_ats_sources.py
python tests/smoke_job_scorer_policy.py
python tests/smoke_telegram_notifier.py
python tests/smoke_run_monitor.py
```

## Scoring Strategy

The scoring policy is designed to show applicable early-career software/IT roles rather than only perfect keyword matches. It prioritizes roles related to backend development, Java/Spring Boot, SQL, software support, application support, integration support, and ERP support.

The system filters out postings that are clearly outside the target profile, including:

- Senior, lead, manager, principal, and architect roles
- Positions requiring high experience such as 4+ or 5+ years
- Non-software departments such as accounting, import/export, sales, logistics, planning, and HR
- Non-target technical tracks such as PHP/WordPress, mobile-only, QA, DevOps, data analyst, and business analyst roles
- Non-Turkey or global-only roles by default

The Telegram digest includes summary statistics so the user can see how many jobs were scanned, rejected, deduplicated, and sent.

## Security Notes

Do not commit:

```text
.env
.venv/
data/
logs/
*.db
*.log
__pycache__/
monitor_debug.txt
```

Telegram credentials must be stored locally in `.env` or in GitHub Actions secrets. They should never be hardcoded into source files.

## Project Status

The project is currently at V1: a scheduled job monitoring assistant. The main goal is reliability and useful Telegram job digests for Turkey-based early-career software/IT opportunities.

Future improvements may include more curated Turkey-based company sources, better parser coverage, richer scoring diagnostics, and eventually a hosted dashboard if the project moves beyond GitHub Actions.
