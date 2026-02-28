# FuturaPredict

AI-powered football match prediction platform covering the top 5 European
domestic leagues **plus** all 3 continental UEFA competitions (Champions League,
Europa League, Conference League). Combines ensemble ML models
(XGBoost + Neural Network + ELO) with a **continuous learning pipeline**,
**external intelligence engine** (20+ prediction sites & pundits), and
**consensus blending** to improve accuracy after every game-day.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start (Docker)](#quick-start-docker)
3. [Compose Modes](#compose-modes)
4. [Quick Start (Local Dev)](#quick-start-local-dev)
5. [Leagues & Priority](#leagues--priority)
6. [Automated Pipeline](#automated-pipeline)
7. [Continuous Learning System](#continuous-learning-system)
8. [External Intelligence Engine](#external-intelligence-engine)
9. [Prediction Access & Subscription](#prediction-access--subscription)
10. [Payment System](#payment-system)
11. [Management Commands](#management-commands)
12. [Project Structure](#project-structure)
13. [Current Compromises & Weaknesses](#current-compromises--weaknesses)
14. [What's Remaining](#whats-remaining)
15. [Areas of Improvement](#areas-of-improvement)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     FuturaPredict  –  System Overview                    │
└─────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │                 DOCKER  (docker-compose.yaml)                        │
  │                                                                      │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐ │
  │  │   web    │  │  worker  │  │   beat   │  │  redis   │  │  db   │ │
  │  │ Gunicorn │  │  Celery  │  │  Celery  │  │  Broker  │  │ PG 15 │ │
  │  │  :8000   │  │ 4 procs  │  │ Beat     │  │  + Cache │  │       │ │
  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └───────┘ │
  └──────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │                  AUTOMATED  DAILY  PIPELINE                          │
  │                                                                      │
  │  BOOT ── Sync leagues + Seed intelligence sources + Fetch & Predict  │
  │  │  8 leagues × 6 days, 20+ external sources seeded & scraped        │
  │  │                                                                   │
  │  00:05  ── Fetch & Predict ──────────────────────────────────────    │
  │  │  ESPN API → 8 leagues × 6 days → Ensemble ML                     │
  │  │  → Consensus Blend → Intelligence Blend → Predictions             │
  │  │                                                                   │
  │  Every 6h ── Scrape External Sources ────────────────────────────   │
  │  │  20+ sites → store predictions → accuracy tracking                │
  │  │                                                                   │
  │  07:00–23:59 (every 20 min)  ── Live Score Updates ──────────────   │
  │  │  ESPN Scoreboard → Update match status & scores → Lock at KO     │
  │  │                                                                   │
  │  23:30  ── End-of-Day Resolution ────────────────────────────────   │
  │  │  Fetch final scores → Resolve predictions (is_correct)           │
  │  │  → Resolve external predictions → Update ELO ratings             │
  │  │  → Accuracy snapshot → Auto-retrain if needed                    │
  │  │                                                                   │
  │  04:00  ── Source Evaluation ────────────────────────────────────   │
  │  │  Resolve external predictions → Daily accuracy records            │
  │  │  → Auto-promote (learning → active) or retire                     │
  │  │                                                                   │
  │  06:00  ── Drift Monitor ────────────────────────────────────────   │
  │  │  Compare last 7 days vs baseline → Emergency retrain if > 5%     │
  │  │                                                                   │
  │  Mon 03:00  ── Weekly Full Retrain ──────────────────────────────   │
  │     3 seasons of data → XGBoost + Neural Network training            │
  │     → Evaluate → Promote if improved                                 │
  └──────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | Django 5.x, Django REST Framework |
| Task Queue | Celery + Redis (broker), django-celery-beat (scheduler) |
| Database | PostgreSQL 15 |
| ML Models | XGBoost, TensorFlow/Keras (Neural), ELO (always-on) |
| Intelligence Engine | Supervised (accuracy-weighted) + Unsupervised (K-means clustering) |
| External Sources | 20+ prediction websites + 3 expert pundits, web scraping framework |
| Feature Store | In-DB + file-based cache (`features/`) |
| Data Sources | ESPN API (primary, free), football-data.org (secondary), OpenLigaDB (BL1 fallback) |
| Payments | M-Pesa (East Africa), PayPal, Stripe (global) — Strategy Pattern |
| Frontend | Django Templates, Bootstrap 5, PWA-ready |
| Containerization | Docker Compose (5 services) |

---

## Quick Start (Docker)

```bash
# 1. Clone and configure
git clone <repo_url> && cd futurapredict
cp .env.example .env            # Edit with your secrets

# 2. Build and run (everything runs automatically!)
docker compose build
docker compose up -d

# On boot, the system automatically:
#   ✅ Applies all migrations
#   ✅ Syncs 8 leagues (PL, LL, SA, BL1, FL1, UCL, UEL, UECL)
#   ✅ Seeds 20+ intelligence sources (learning phase)
#   ✅ Fetches fixtures for 6 days + generates predictions
#   ✅ Starts Gunicorn, Celery Worker, and Celery Beat

# 3. Access
open http://localhost:8000
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| `web` | 8000 | Django / Gunicorn HTTP server |
| `worker` | — | Celery worker (executes tasks) |
| `beat` | — | Celery Beat scheduler (triggers tasks on schedule) |
| `redis` | 6379 | Message broker + cache |
| `db` | 5432 | PostgreSQL 15 |

### Web Startup Sequence

On every `docker compose up`, the `web` service runs:

| Step | What Runs | Purpose |
|------|-----------|---------|
| 1 | `wait_for_postgres` | Wait for DB to accept connections |
| 2 | `ensure_migrations` | `python manage.py migrate --noinput` |
| 3 | `sync_leagues` | `DataIngestionService.sync_teams_and_leagues()` — ensures all 8 leagues exist |
| 4 | `seed_prediction_sources` | Seeds 20+ prediction websites & 3 pundits (idempotent) |
| 5 | `ensure_predictions` | Fetches fixtures for 6 days + generates predictions if none exist |
| 6 | `collectstatic` | Django static file collection |
| 7 | `gunicorn` | Start the HTTP server |

### Useful Commands

```bash
docker compose logs -f beat worker              # Watch scheduler + tasks
docker compose exec web python manage.py shell  # Django shell
docker compose exec web python manage.py fetch_and_predict --days 6
docker compose exec web python manage.py seed_prediction_sources
docker compose exec web python manage.py createsuperuser
```

---

## Compose Modes

| File | Purpose | SSL Management | App Port |
|------|---------|----------------|----------|
| `docker-compose.yaml` | Full stack + optional container-edge | Container nginx + certbot | `WEB_PORT` (default 8000) |
| `docker-compose-prd.yaml` | App-only production stack | Host nginx + certbot | Fixed `8004:8000` |

### Backup, Restore, and Automatic Rollback

| Script | Purpose |
|--------|---------|
| `scripts/backup_db.sh` | Create compressed SQL backup from running `db` |
| `scripts/restore_db.sh` | Restore DB from a backup file |
| `scripts/deploy_prd.sh` | Production deploy: auto-backup → deploy → health-check → auto-rollback if failed |

---

## Quick Start (Local Dev)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Prerequisites: PostgreSQL + Redis running locally
python manage.py migrate
python manage.py seed_data
python manage.py seed_prediction_sources
python manage.py fetch_and_predict --days 6
python manage.py runserver
```

---

## Leagues & Priority

### 8 Leagues Covered

| Priority | League | Code | Type | Data Source | Season |
|----------|--------|------|------|-------------|--------|
| **0** | 🏆 UEFA Champions League | `UCL` | Continental | ESPN → football-data.org | Sep–Jun |
| **0** | 🏅 UEFA Europa League | `UEL` | Continental | ESPN → football-data.org | Sep–Jun |
| **0** | 🥉 UEFA Conference League | `UECL` | Continental | ESPN → football-data.org | Sep–Jun |
| 1 | Premier League (EPL) | `PL` | Domestic | ESPN → football-data.org | Year-round |
| 2 | La Liga | `LL` | Domestic | ESPN → football-data.org | Year-round |
| 3 | Serie A | `SA` | Domestic | ESPN → football-data.org | Year-round |
| 4 | Bundesliga | `BL1` | Domestic | ESPN → OpenLigaDB | Year-round |
| 5 | Ligue 1 | `FL1` | Domestic | ESPN → football-data.org | Year-round |

**Continental competitions (UCL, UEL, UECL):**
- Priority 0 (highest) — they always appear above domestic leagues
- **Seasonal**: Active September through June, skipped in off-season (July–August)
- **League type**: `continental` — only fetched during their active months
- Same subscription access model as domestic leagues

**Data source cascade**: ESPN (free, no key) → football-data.org (key-based) → OpenLigaDB (free, Bundesliga only).

---

## Automated Pipeline

All scheduling is handled by **Celery Beat** — no system cron needed.

### Task Schedule (all times UTC)

| Time | Task | Purpose |
|------|------|---------|
| **00:00** | `verify_predictions_availability` | Safety net: generate missing predictions for today |
| **00:05** | `fetch_and_predict_scheduled` | Fetch ESPN fixtures + generate predictions for **today + next 5 days** |
| **00:30** | `compute_accuracy_stats_task` | Write daily `AccuracyRecord` snapshots per league |
| **Every 6h** | `scrape_external_predictions` | Scrape 20+ external prediction sources |
| **04:00** | `evaluate_sources` | Resolve external predictions, daily accuracy records, auto-promote/retire sources |
| **06:00** | `check_accuracy_drift_task` | Compare recent accuracy vs baseline; auto-retrain if drift > 5% |
| **Every 20 min** (07–23) | `update_live_scores_task` | Refresh in-play scores from ESPN, lock predictions at kickoff |
| **23:30** | `resolve_finished_matches_task` | Fetch final scores, resolve predictions, update ELO, trigger retrain |
| **Monday 03:00** | `weekly_full_retrain_task` | Full model retrain on 3 seasons of historical match data |

> **Note**: The intelligence engine tasks (`scrape_external_predictions`, `evaluate_sources`) need to be registered in Django Admin's Celery Beat schedule (Periodic Tasks) until they are added to `CELERY_BEAT_SCHEDULE` in settings.

### Prediction Data Flow

```
ESPN API  ──►  Match records (DB)
                    │
                    ▼
          Feature Engineering (32+ dimensions)
                    │
                    ▼
          ┌─────────────────────┐
          │   Ensemble Model    │
          │  ┌──────┬────────┐  │
          │  │ XGB  │ Neural │  │  ← Weighted average
          │  │ 0.4  │  0.3   │  │
          │  ├──────┼────────┤  │
          │  │ ELO (always)  │  │  ← Fallback for cold start
          │  │     0.3       │  │
          │  └──────┴────────┘  │
          └─────────┬───────────┘
                    │
                    ▼
          ┌───────────────────────────────────┐
          │   Intelligence Blending            │
          │                                    │
          │   If external sources available:   │
          │     70% Model + 30% Intelligence   │
          │   Else:                             │
          │     75% Model + 25% Basic Consensus │
          │     (ESPN + H2H + ELO + League)     │
          └─────────┬─────────────────────────┘
                    │
                    ▼
          Final Prediction Record
          (home_win_prob, draw_prob, away_win_prob, confidence)
```

---

## Continuous Learning System

The platform uses a **3-layer learning loop**:

### Layer 1: Supervised Learning (Weekly + On-Demand)

- **Full retrain** (Monday 03:00): 3 seasons of finished matches → generate features → train XGBoost + Neural → promote if improved
- **Incremental retrain**: Last 90 days of resolved data. Triggered when:
  - ≥ 100 new resolved predictions since last training, **or**
  - Accuracy drift > 5% detected by the daily drift monitor

### Layer 2: Online Learning (ELO Ratings)

- After every finished match, team ELO ratings update instantly (K=32)
- Immediately reflected in the next prediction cycle

### Layer 3: External Intelligence (Supervised + Unsupervised)

- **Supervised**: 20+ external sources weighted by their historical accuracy
- **Unsupervised**: K-means clustering detects consensus groups and filters outliers
- **Auto-evaluation**: 90-day learning phase → auto-promote accurate sources

### ML Features (32+ dimensions)

| Category | Features |
|----------|----------|
| Recent form | Wins, draws, losses, goals scored/conceded, points, win rate (last 10) |
| Home/Away split | Home win rate, away win rate, home advantage delta |
| Head-to-head | H2H wins, draws, total meetings, dominance ratio |
| ELO ratings | Home ELO, away ELO, ELO difference, expected score |
| Schedule congestion | Matches in last 7/14/30 days per team |
| Goal patterns | Average total goals, over-2.5 rate |
| League context | League home win rate, draw rate, average goals |

---

## External Intelligence Engine

### Overview

A comprehensive system that scrapes predictions from **20+ top prediction websites and expert pundits**, tracks accuracy during a **90-day learning phase**, then dynamically weights them into the prediction pipeline.

### Top Sources Integrated

| # | Source | Type | Scraper Status |
|---|--------|------|---------------|
| 1 | Sports Mole | Website | Stub |
| 2 | WhoScored | Website | Stub |
| 3 | **PredictZ** | Website | ✅ Full |
| 4 | Football Whispers | Website | Stub |
| 5 | FootyStats | Website | Stub |
| 6 | Understat | Website | Stub |
| 7 | Dimers | ML Model | Stub |
| 8 | Squawka | Website | Stub |
| 9 | MrFixitsTips | Community | No scraper |
| 10 | **Betensured** | Website | ✅ Full |
| 11 | SportyTrader | Website | No scraper |
| 12 | SoccerStats | Website | No scraper |
| 13 | Eagle Predict | ML Model | No scraper |
| 14 | RatingBet | Website | No scraper |
| 15 | SoccerVista | Website | No scraper |
| 16 | Betalyst | Website | No scraper |
| 17 | FreeSuperTips | Website | No scraper |
| 18 | **Vitibet** | Website | ✅ Full |
| 19 | Tips180 | Website | No scraper |
| 20 | Sportsgambler | Website | No scraper |
| — | **Forebet** | ML Model | ✅ Full |
| — | Paul Merson | Pundit | No scraper |
| — | Mark Lawrenson | Pundit | No scraper |
| — | Michael Owen | Pundit | No scraper |

### Source Lifecycle

```
New Source → Learning Phase (90 days, tracked but not used in blending)
                ↓
          After 90 days + ≥ 50 predictions:
            If accuracy ≥ 50%  → Promoted to Active (used in blending)
            If accuracy < 50%  → Retired (not used)
                ↓
          Active sources can be Paused/Retired by admin at any time
```

### Admin Interface

Admin can manage sources via Django Admin (`/admin/predictions/predictionsource/`):
- Add new sources with custom scraper class
- Configure scrape URLs, intervals, and config
- Monitor overall and 30-day accuracy
- Inline accuracy records (last 30 days)
- Bulk actions: Promote, Pause, Start Learning Phase

---

## Prediction Access & Subscription

### Access Tiers

| User Type | Access |
|-----------|--------|
| **Not logged in** | 2 free preview predictions |
| **Signed up (no subscription)** | 3 free predictions (signup bonus) |
| **Monthly subscriber** | Daily quota (5–30/day depending on plan) + **5-day lookahead** |
| **Pay-per-game** | Any prediction beyond quota for 1 credit each |

### Subscription Plans

| Plan | Price | Daily Limit |
|------|-------|-------------|
| Starter | $5/mo | 5 predictions/day |
| Pro | $10/mo | 10 predictions/day |
| Business | $20/mo | 20 predictions/day |
| Monthly All-Access | $30/mo | 30 predictions/day |

### Continental League Access

All three UEFA competitions share the same access model:
- **Monthly subscribers**: Automatic access — included in plan
- **Weekly/Daily subscribers**: Via daily quota
- **Credit users**: On-demand purchase
- **Free users**: Free-tier predictions only

---

## Payment System

Region-aware payment processing using the Strategy Design Pattern.

| Region | Currency | Payment Methods |
|--------|----------|-----------------|
| East Africa (KE, UG, TZ) | KES | M-Pesa |
| Global | USD | PayPal, Stripe |

See `PAYMENT_SYSTEM.md` for full details.

---

## Management Commands

| Command | Description |
|---------|-------------|
| `python manage.py seed_data` | Bootstrap leagues, plans, pricing tiers |
| `python manage.py seed_prediction_sources` | Seed 20+ external prediction sources (learning phase) |
| `python manage.py seed_prediction_sources --reset` | Reset and re-seed all sources |
| `python manage.py fetch_and_predict --days 6` | Fetch fixtures + generate predictions for N days |
| `python manage.py fetch_and_predict --date 2026-03-01` | Fetch for a specific date |
| `python manage.py fetch_and_predict --force-predict` | Regenerate predictions even if they exist |
| `python manage.py seed_matches` | Seed test match data for all leagues |
| `python manage.py create_test_users` | Create test accounts for every subscription tier |

---

## Project Structure

```
futurapredict/
├── apps/
│   ├── core/                         # Leagues, Teams, Matches, Seasons
│   │   ├── models.py                 # League, Team, Match, Season, LeagueAccessRule
│   │   ├── services/
│   │   │   ├── data_ingestion.py     # ESPN + football-data.org + OpenLigaDB (8 leagues)
│   │   │   ├── feature_engineering.py # 32+ ML features + ELO updates
│   │   │   └── results_resolution.py # End-of-day: resolve + retrain loop
│   │   ├── management/commands/
│   │   │   ├── seed_data.py
│   │   │   ├── seed_matches.py
│   │   │   └── fetch_and_predict.py
│   │   └── migrations/
│   │       ├── 0001_initial.py
│   │       ├── 0002_add_league_access_rule.py
│   │       ├── 0003_add_champions_league_support.py
│   │       ├── 0004_seed_champions_league.py
│   │       └── 0005_seed_europa_conference_leagues.py
│   │
│   ├── predictions/                  # ML predictions + Intelligence Engine
│   │   ├── models.py                 # Prediction, ModelVersion, PredictionExplanation
│   │   ├── models_intelligence.py    # PredictionSource, ExternalPrediction, SourceAccuracyRecord
│   │   ├── tasks.py                  # 10 Celery tasks (pipeline + intelligence)
│   │   ├── ml/
│   │   │   ├── models/
│   │   │   │   ├── ensemble_model.py     # EnsemblePredictor + ELOPredictor
│   │   │   │   ├── xgboost_model.py      # XGBoostPredictor
│   │   │   │   └── neural_model.py       # NeuralPredictor (TensorFlow)
│   │   │   ├── training_pipeline.py      # Full + incremental training
│   │   │   └── feature_store.py          # Feature caching
│   │   ├── services/
│   │   │   ├── prediction_service.py     # Generate predictions (with intelligence blend)
│   │   │   ├── consensus.py              # Basic consensus: ESPN + H2H + ELO + league bias
│   │   │   ├── learning_engine.py        # Full intelligence engine (supervised + unsupervised)
│   │   │   ├── ensemble.py               # Wire ML + explainer
│   │   │   ├── model_registry.py         # Active model selection
│   │   │   └── explainer.py              # SHAP-based explanations
│   │   ├── scrapers/                     # External prediction scrapers
│   │   │   ├── base.py                   # BaseScraper (rate limiting, retries, fuzzy matching)
│   │   │   ├── registry.py               # Slug → scraper class resolution
│   │   │   └── builtin.py               # PredictZ, Forebet, Vitibet, Betensured + stubs
│   │   └── management/commands/
│   │       ├── seed_prediction_sources.py # Seed 20+ sources
│   │       └── setup_prediction_schedule.py
│   │
│   ├── payments/                     # Subscription + credits + payments
│   │   ├── models.py                 # SubscriptionPlan, PricingTier, UserSubscription
│   │   ├── strategies/               # M-Pesa, PayPal, Stripe, WhatsApp (Strategy Pattern)
│   │   └── services/
│   │       ├── payment_service.py
│   │       └── subscription_service.py
│   │
│   ├── users/                        # Authentication, profiles, middleware
│   │   ├── middleware.py             # SubscriptionMiddleware, region detection
│   │   └── models.py                # UserProfile, PredictionUsage
│   │
│   ├── analytics/                    # Accuracy tracking, revenue, stats
│   │   └── models.py                # AccuracyRecord, RevenueSnapshot, PredictionStats
│   │
│   └── api/                          # Views + REST API
│       └── views.py                  # PredictionsView, PredictionDetailView, etc.
│
├── config/
│   ├── celery.py                     # Celery app
│   ├── settings/
│   │   └── base.py                   # CELERY_BEAT_SCHEDULE + ML_CONFIG
│   └── urls.py
│
├── templates/                        # Django templates (Bootstrap 5)
├── static/                           # CSS, JS, images
├── docker-compose.yaml               # 5 services: web, worker, beat, redis, db
├── docker-compose-prd.yaml           # Production stack (host nginx)
├── Dockerfile                        # Multi-service image
├── entrypoint.sh                     # Service dispatcher (web|worker|beat|setup|bootstrap)
└── requirements.txt
```

---

## Current Compromises & Weaknesses

### 1. ESPN API as Primary Data Source
- **Weakness**: ESPN's public scoreboard API is unofficial and undocumented. Could change or disappear without notice.
- **Mitigation**: Cascade fallback to football-data.org and OpenLigaDB.

### 2. ELO-Heavy Fallback on Cold Start
- **Weakness**: Until ≥ 200 resolved matches exist, XGBoost and Neural models can't train. During this phase, the system falls back to pure ELO predictions with random noise.
- **Mitigation**: ELO includes home-advantage bias. Weekly retrain auto-promotes ensemble once enough data exists.

### 3. In-Process Retrain (No GPU Offloading)
- **Weakness**: Model retraining runs inside Celery worker. Could block workers for extended periods with large datasets.
- **Mitigation**: Runs Monday 03:00 (low traffic). `max-tasks-per-child=200` recycles workers.

### 4. Synchronous Fetch-on-Demand
- **Weakness**: When a subscriber views a future date with no predictions, `fetch_and_predict` runs within the HTTP request. If ESPN is slow, the page load is slow.
- **Mitigation**: Midnight pipeline pre-fetches 6 days ahead, so on-demand is rarely triggered.

### 5. Limited Scraper Coverage
- **Weakness**: Only 4 of 20+ scrapers are fully implemented (PredictZ, Forebet, Vitibet, Betensured). 7 have stub implementations. 9 have no scraper.
- **Mitigation**: More scrapers can be added incrementally. Admin can also configure custom scraper classes.

### 6. Regex-Based HTML Scraping
- **Weakness**: Built-in scrapers use regex to parse HTML. This is fragile — any site redesign will break the scraper.
- **Mitigation**: Rate limiting, error handling, and graceful degradation. Each scraper failure is logged and non-fatal.

### 7. No JavaScript Rendering
- **Weakness**: Sites like WhoScored, Understat, and Dimers render data via JavaScript. The current `requests`-based scrapers can't access this data.
- **Mitigation**: Stub scrapers exist. Can be upgraded to use Playwright or Selenium when needed.

### 8. Team Matching by Name
- **Weakness**: ESPN team names are matched using fuzzy string comparison. Different sources may use different names for the same team, leading to mismatches or duplicates.
- **Mitigation**: Broad cross-league search + deduplication via Admin.

### 9. No Unit Tests for Intelligence Engine
- **Weakness**: The scraper framework, learning engine, and consensus service have no automated tests yet.
- **Impact**: Refactoring risk; harder to verify correctness after changes.

### 10. Intelligence Engine Tasks Not in `CELERY_BEAT_SCHEDULE`
- **Weakness**: The 3 new intelligence engine Celery tasks (`scrape_external_predictions`, `evaluate_sources`, `resolve_external_predictions`) are defined but not yet added to `CELERY_BEAT_SCHEDULE` in `config/settings/base.py`. They must be manually registered in Django Admin's Periodic Tasks.
- **Mitigation**: Easy fix — add 3 entries to `CELERY_BEAT_SCHEDULE`.

### 11. Single-Instance Celery Beat
- **Weakness**: Only one Beat process can run at a time. Multiple instances cause duplicate tasks.
- **Mitigation**: `docker compose` enforces single replica. Use leader election in Kubernetes.

---

## What's Remaining

### High Priority (Must-Have)

1. **Register intelligence tasks in `CELERY_BEAT_SCHEDULE`**
   - Add `scrape_external_predictions` (every 6h), `evaluate_sources` (4:00 daily), `resolve_external_predictions` (2h or chained after match resolution) to settings.
   - Status: **Not done** — tasks exist but aren't scheduled.

2. **Complete scraper implementations (remaining 16)**
   - Sports Mole, WhoScored (needs JS rendering), Football Whispers, FootyStats, Understat (needs JS), Dimers (needs JS), Squawka, MrFixitsTips, SportyTrader, SoccerStats, Eagle Predict, RatingBet, SoccerVista, Betalyst, FreeSuperTips, Tips180, Sportsgambler.
   - Status: **Stubs exist** for 7, no scraper for 9.

3. **Unit & integration tests**
   - Test the scraper framework (BaseScraper, fuzzy matching, rate limiting)
   - Test the learning engine (resolve, accuracy tracking, auto-promotion)
   - Test consensus blending (verify weighted averages are correct)
   - Test intelligence service integration in PredictionService
   - Status: **Not started**.

4. **Team name alias/normalisation system**
   - Build a `TeamAlias` model mapping variant names to canonical records
   - Critical for scraper accuracy — different sites use different names
   - Status: **Not started**.

### Medium Priority (Should-Have)

5. **Pundit prediction scraping**
   - Paul Merson (Sky Sports), Mark Lawrenson (BBC), Michael Owen (BetVictor)
   - Need site-specific scrapers for pundit columns
   - Status: **Source records exist**, scrapers not implemented.

6. **JavaScript-rendering scrapers**
   - Integrate Playwright or Selenium for WhoScored, Understat, Dimers
   - These are high-value data sources but require headless browser
   - Status: **Not started**.

7. **Async fetch-on-demand**
   - Replace synchronous `fetch_and_predict` in PredictionsView with Celery task + WebSocket/SSE push
   - Status: **Not started**.

8. **Confidence calibration**
   - Platt scaling or isotonic regression to calibrate confidence scores against actual observed frequencies
   - Status: **Not started**.

### Low Priority (Nice-to-Have)

9. **Model explainability dashboard** — Surface SHAP values on prediction detail page
10. **A/B testing framework** — Keep old model active for % of users, compare accuracy
11. **Per-league sub-models** — Train separate XGBoost per league
12. **Push notifications** — Send prediction results via email/WhatsApp after match resolution
13. **Flower dashboard** — Real-time Celery task monitoring
14. **Kubernetes deployment** — HPA for workers, CronJob as Beat alternative
15. **Feature store versioning** — Tag feature snapshots with engineering version

---

## Areas of Improvement

### Accuracy Improvements

1. **More external sources with full scrapers** — Currently only 4/24 sources have working scrapers. Each additional source improves the consensus signal.
2. **JavaScript rendering** — WhoScored (Opta data), Understat (xG), and Dimers (ML models) are high-value sources locked behind JS rendering.
3. **Per-league sub-models** — Different leagues have different dynamics (Bundesliga is higher-scoring than Serie A). Per-league models could capture these nuances.
4. **Confidence calibration** — Current confidence scores are not calibrated against actual observed frequencies. Platt scaling would improve this.
5. **Historical data bootstrap** — Import 3+ seasons from football-data.org or FBref on first setup to immediately train ML models instead of waiting for data accumulation.
6. **BTTS, Over/Under markets** — FootyStats and Betalyst specialise in these. Adding these market predictions would broaden the system's appeal.

### System Improvements

7. **Async fetch-on-demand** — Replace synchronous ESPN calls in views with background tasks + SSE/WebSocket push.
8. **API rate limiting per source** — Token bucket rate limiters to avoid hitting ESPN/football-data.org throttle limits.
9. **Team alias model** — Canonical team names with alias mapping for cross-source matching.
10. **Scraper health monitoring** — Dashboard showing scraper success/failure rates, last scrape time, and data freshness.
11. **GPU offloading** — Offload model training to a GPU worker (or external service like AWS SageMaker).
12. **Automated scraper repair** — When a site changes its HTML structure, detect the breakage and alert admin.

### Infrastructure Improvements

13. **Add intelligence tasks to `CELERY_BEAT_SCHEDULE`** — Currently requires manual Django Admin setup.
14. **Kubernetes deployment** — Proper HPA, CronJobs, and pod anti-affinity rules.
15. **Monitoring & alerting** — Prometheus metrics for prediction accuracy, scraper health, and task latency.
16. **Feature store versioning** — Ensure model retraining always uses consistently generated features.

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DJANGO_SECRET_KEY` | Yes | Django secret key |
| `POSTGRES_PASSWORD` | Yes | Database password |
| `FOOTBALL_DATA_API_KEY` | No | football-data.org API key (fallback source) |
| `STRIPE_SECRET_KEY` | For payments | Stripe API key |
| `MPESA_CONSUMER_KEY` | For M-Pesa | Safaricom Daraja key |
| `BOOT_FETCH_DAYS` | No | Days to fetch on boot (default: 6) |
| `AUTO_ENSURE_TODAY_PREDICTIONS_ON_BOOT` | No | Set to `0` to skip boot-time prediction generation |

---

## License

Proprietary. All rights reserved.
