# Manul Radar Architecture

## Overview

Manul Radar is a modular personal monitoring platform designed to monitor various information sources and notify the user when something relevant is detected.

The initial goal of the project is to monitor software job opportunities that match my career profile and deliver them through Telegram. In the future, the same architecture will support additional sources such as Reddit, Steam, GitHub, AI news, and company career pages.

The project is designed to be modular, lightweight and easily extendable.

---

# Product Identity

Repository Name

    manul-radar

Bot Name

    🐈 Manul Sentinel

Mission

    Watch.
    Filter.
    Notify.

---

# Version 1 Scope

The first version focuses only on software job monitoring.

Supported workflow:

- Read configured job sources
- Convert every posting into a common Job model
- Filter irrelevant jobs
- Score remaining jobs
- Ignore duplicates
- Store processed jobs
- Send Telegram notification

No AI analysis will be included in Version 1.

---

# Core Workflow

```
Scheduler
      │
      ▼
Job Sources
      │
      ▼
Normalize to Job Model
      │
      ▼
Filtering
      │
      ▼
Scoring
      │
      ▼
Duplicate Check
      │
      ▼
Database
      │
      ▼
Telegram Notification
```

Every source should produce the same Job object.

This allows new sources to be added without modifying the rest of the system.

---

# Folder Structure

```
manul-radar/

app/
    config/
    database/
    filters/
    models/
    notifier/
    scheduler/
    services/
    sources/
    utils/

data/
docs/
logs/
tests/

main.py
requirements.txt
```

---

# Module Responsibilities

## config

Contains configuration loading logic.

Examples:

- Telegram settings
- Keywords
- Source configuration
- Scheduler interval

---

## database

Responsible for persistent storage.

Responsibilities:

- Save processed jobs
- Prevent duplicate notifications
- Store timestamps

Database engine:

SQLite (Python built-in sqlite3)

---

## filters

Responsible for deciding whether a job is relevant.

Possible filters:

- Required keywords
- Excluded keywords
- Remote preference
- Country preference
- Seniority level

The filtering layer should not know where the job came from.

---

## models

Contains application models.

Version 1:

- Job

Future:

- Company
- Notification
- Source
- UserPreference

Models should remain independent from data sources.

---

## notifier

Responsible for sending notifications.

Version 1:

Telegram Bot API

Future:

- Discord
- Email
- Slack

---

## scheduler

Responsible for deciding when monitoring starts.

Possible execution methods:

- GitHub Actions
- Cron
- VPS background service

Scheduler never processes jobs directly.

It only starts the monitoring workflow.

---

## services

Contains business logic.

Examples:

JobMonitorService

Coordinates the entire monitoring process.

JobScoringService

Calculates relevance score.

NotificationService

Determines whether a notification should be sent.

---

## sources

Responsible only for collecting raw information.

Each source should expose a common interface.

Examples:

LinkedInSource

CompanyCareerSource

RSSSource

Future:

SteamSource

RedditSource

GitHubSource

Sources must never send notifications directly.

---

## utils

General helper functions.

Examples:

- Logger
- Date helpers
- Text normalization
- URL utilities

---

# Data Flow

The application follows a one-way pipeline.

```
Source

↓

Job

↓

Filter

↓

Score

↓

Database Check

↓

Telegram

↓

Save
```

Every stage performs exactly one responsibility.

---

# Design Principles

## Modular

Every component should have a single responsibility.

---

## Extensible

Adding a new source should require creating only one new source class.

Existing code should not change.

---

## Configurable

Keywords, intervals and settings should never be hardcoded.

Everything should come from configuration.

---

## Independent

Notification, filtering, storage and sources must remain isolated.

Changing one component should not affect others.

---

## Lightweight

Avoid unnecessary frameworks.

Prefer Python standard library whenever possible.

---

## Practical

This project is intended for daily personal use.

Every implemented feature should solve a real problem.

---

# Future Expansion

Possible future modules:

- AI relevance scoring
- CV matching
- Daily summary
- Company priority ranking
- Steam monitoring
- Reddit monitoring
- GitHub release monitoring
- AI news monitoring
- Exiletide mention monitoring

The architecture should support these features without requiring major refactoring.

---

# Long-Term Vision

Manul Radar is not intended to become just another job scraper.

The long-term objective is to evolve into a personal monitoring assistant capable of collecting information from multiple sources, filtering valuable content, and delivering only the information that matters.

Job monitoring is simply the first module of that ecosystem.