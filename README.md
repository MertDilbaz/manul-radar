# Manul Radar / Manul Sentinel

**Manul Radar** is a lightweight personal monitoring assistant. The Telegram bot name is **Manul Sentinel**. The current V1 scope is job monitoring: configured sources are fetched, postings are normalized into a common `Job` model, scored against Mert's backend / Java / application-support profile, deduplicated in SQLite, and optionally sent to Telegram.

## Current workflow

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

`main.py` is intentionally kept as a stable smoke target that uses `DummySource`. Real monitoring starts from `run_monitor.py`.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with your real Telegram values:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

`.env` is ignored by Git. Do not commit real tokens.

## Run

```powershell
python main.py
python run_monitor.py --test-telegram
python run_monitor.py --use-dummy-source
python run_monitor.py
```

`--test-telegram` sends only one test message. `--use-dummy-source` runs the full persist + Telegram path with fixture jobs. Plain `python run_monitor.py` uses the enabled sources in `app/config/config.yaml`.

## Configuration

Runtime settings live in `app/config/config.yaml`. Telegram secrets are not stored there; the file only stores the environment variable names. The current job profile is configured through `keywords.include`, `keywords.exclude`, and `scoring`.

Current supported source types:

- `hrpeak`
- `successfactors`
- `workable`
- `greenhouse`
- `lever`
- `smartrecruiters`
- `teamtailor`
- `kariyer_net` (kept disabled by default because it often returns 403)

Runtime/scoring settings live in `app/config/config.yaml`; company/ATS boards live in `app/config/companies.yaml`. No Steam, news, weather, or HTML-report modules are active in this scope.

## Scheduling

`.github/workflows/job-monitor.yml` runs the real monitor Monday-Friday at 09:00 Turkey time. GitHub cron uses UTC, so the workflow is scheduled at `06:00 UTC`. Add these repository secrets before enabling it for real notifications:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The workflow restores and saves `data/jobs.db` through GitHub Actions cache so duplicate notifications are reduced across scheduled runs.

## Smoke tests

```powershell
python tests\smoke_env_loader.py
python tests\smoke_workflow.py
python tests\smoke_repository.py
python tests\smoke_hrpeak_source.py
python tests\smoke_kariyer_net_source.py
python tests\smoke_telegram_notifier.py
python tests\smoke_run_monitor.py
```

### SuccessFactors source

The project now includes a generic `successfactors` source for public SAP SuccessFactors / RMK career pages. The first enabled source is SAP Türkiye (`https://jobs.sap.com/go/T%C3%BCrkiye/9054501/`). Add additional SuccessFactors companies in `app/config/config.yaml` by copying that source block and changing `company`, `name`, and `url`.

## Current job-source architecture

The active scope is job monitoring only. The runtime now supports multiple ATS parser families: HRPeak, SuccessFactors, Workable, Greenhouse, Lever, SmartRecruiters, Teamtailor, and the disabled-by-default Kariyer.net parser. Company boards should be added to `app/config/companies.yaml`; scoring, Telegram, and notification settings remain in `app/config/config.yaml`.

Kariyer.net entries are kept but disabled because the site frequently returns `403 Forbidden` to scripted runs. The reliable path is to grow the ATS/company registry instead.

Useful tests after replacing files:

```powershell
python tests/smoke_ats_sources.py
python tests/smoke_successfactors_source.py
python tests/smoke_job_scorer_policy.py
python tests/smoke_telegram_notifier.py
python tests/smoke_run_monitor.py
```

