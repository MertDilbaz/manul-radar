# Manul Radar

A personal monitoring and job-tracking assistant. The bot that runs it
is **Manul Sentinel**.

## Status

V1 core workflow implemented with a `DummySource`. The Source → Score
→ Filter pipeline runs end-to-end on every invocation. No persistence,
no notifications, no real scraping yet.

## Tech stack

- Python 3.11
- YAML config
- Loguru (logging)
- `dataclasses` (no ORM, no framework)

## Current workflow

```
DummySource → JobScorer → JobMonitorService → relevant ScoredJob list
```

- `app/sources/dummy_source.py` — returns three hard-coded jobs
- `app/filters/job_scorer.py` — keyword-based scoring
- `app/services/job_monitor_service.py` — runs all sources, filters by
  `relevant`, returns a clean `list[ScoredJob]`

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Layout

```
app/
  config/      # YAML loader + config.yaml
  filters/     # normalize_text, JobScorer
  models/      # Job, ScoredJob
  services/    # JobMonitorService
  sources/     # BaseSource, DummySource
  utils/       # logger
tests/         # smoke tests
main.py        # entry point
```

## Configuration

`app/config/config.yaml` ships with placeholder Telegram env-var names
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and a starter keyword list.
Secrets are **not** checked in; create a local `.env` (see
`.gitignore`) when the Telegram notifier lands.
