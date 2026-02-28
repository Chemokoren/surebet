# FuturaPredict

AI-powered football match prediction platform covering the top 5 European leagues.
Combines ensemble ML models (XGBoost + Neural Network + ELO) with a continuous
learning pipeline that improves accuracy after every game-day.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start (Docker)](#quick-start-docker)
3. [Compose Modes](#compose-modes)
4. [Quick Start (Local Dev)](#quick-start-local-dev)
5. [Leagues & Priority](#leagues--priority)
6. [Automated Pipeline](#automated-pipeline)
7. [Continuous Learning System](#continuous-learning-system)
8. [Prediction Access & Subscription](#prediction-access--subscription)
9. [Payment System](#payment-system)
10. [Management Commands](#management-commands)
11. [Project Structure](#project-structure)
12. [Current Compromises](#current-compromises)
13. [Areas of Improvement](#areas-of-improvement)

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
  │  00:05  ── Fetch & Predict ──────────────────────────────────────    │
  │  │  ESPN API → 5 leagues × 6 days → Ensemble ML → Predictions       │
  │  │                                                                   │
  │  07:00–23:59 (every 20 min)  ── Live Score Updates ──────────────   │
  │  │  ESPN Scoreboard → Update match status & scores → Lock at KO     │
  │  │                                                                   │
  │  23:30  ── End-of-Day Resolution ────────────────────────────────   │
  │  │  Fetch final scores → Resolve predictions (is_correct)           │
  │  │  → Update ELO ratings → Accuracy snapshot                        │
  │  │  → Auto-retrain if ≥ 100 new resolved or accuracy drift > 5 %   │
  │  │                                                                   │
  │  Mon 03:00  ── Weekly Full Retrain ──────────────────────────────   │
  │  │  3 seasons of data → XGBoost + Neural Network training           │
  │  │  → Evaluate → Promote if improved                                │
  │  │                                                                   │
  │  06:00  ── Drift Monitor ────────────────────────────────────────   │
  │     Compare last 7 days vs baseline → Emergency retrain if > 5 %    │
  └──────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | Django 5.x, Django REST Framework |
| Task Queue | Celery + Redis (broker), django-celery-beat (scheduler) |
| Database | PostgreSQL 15 |
| ML Models | XGBoost, TensorFlow/Keras (Neural), ELO (always-on) |
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

# 2. Build and run
docker compose build
docker compose run --rm web setup   # Migrate + seed + fetch 6 days
docker compose up -d                # Start all services

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

### Useful Commands

```bash
docker compose logs -f beat worker              # Watch scheduler + tasks
docker compose exec web python manage.py shell  # Django shell
docker compose exec web python manage.py fetch_and_predict --days 6
docker compose exec web python manage.py createsuperuser
```

---

## Compose Modes

Use the compose file that matches your edge-proxy setup:

| File | Purpose | Nginx/SSL Management | App Port Exposure |
|------|---------|----------------------|-------------------|
| `docker-compose.yaml` | Full stack with optional container-edge profile | Can run containerized `nginx` + `certbot` services | `web` via `WEB_PORT` env (default 8000) |
| `docker-compose-prd.yaml` | App-only production stack for host-managed Nginx | Host `/etc/nginx/sites-available/*` and host certbot manage TLS | Fixed `8004:8000` for host Nginx upstream |

### `docker-compose.yaml` (container-edge option)

```bash
# App services only (default)
docker compose up -d

# Include container edge proxy/certbot services explicitly
docker compose --profile container-edge up -d nginx certbot-renew
```

### `docker-compose-prd.yaml` (host Nginx recommended)

```bash
# Start app stack for host Nginx proxying to localhost:8004
docker compose -f docker-compose-prd.yaml up -d

# First-time setup
docker compose -f docker-compose-prd.yaml run --rm web setup
```

### Data Persistence Across Deployments

- Persistent Docker volumes are pinned with fixed names (`futurapredict_postgres_data`, `futurapredict_models_data`, etc.) so redeploys from different compose files or project names still reuse the same data.
- Use `docker compose -f docker-compose-prd.yaml down` (without `-v`) before upgrades.
- Do **not** run `down -v` unless you intentionally want to wipe PostgreSQL, Redis, model artifacts, and feature caches.

### Backup, Restore, and Automatic Rollback

Deployment safety scripts are included in `scripts/`:

| Script | Purpose |
|--------|---------|
| `scripts/backup_db.sh` | Create a compressed SQL backup from the running `db` service |
| `scripts/restore_db.sh` | Restore DB from a backup file |
| `scripts/deploy_prd.sh` | Production deploy wrapper: auto-backup before deploy, health-check app, auto-restore DB on failure, and auto-backfill today's predictions if missing |

Examples:

```bash
# Manual backup
./scripts/backup_db.sh --compose-file docker-compose-prd.yaml

# Manual restore
./scripts/restore_db.sh --compose-file docker-compose-prd.yaml --input backups/db/futurapredict_YYYYmmdd_HHMMSS.sql.gz

# Safe deploy with automatic backup + rollback
./scripts/deploy_prd.sh --compose-file docker-compose-prd.yaml --health-url http://127.0.0.1:8004/
```

`deploy_prd.sh` returns non-zero on failed health check even after restore, so CI/CD can mark the deployment as failed.

By default, `deploy_prd.sh` also verifies today's prediction count after a successful deploy. If count is `0`, it runs:

```bash
python manage.py fetch_and_predict --days 1
```

to backfill today immediately. You can disable this behavior for a specific run with:

```bash
AUTO_ENSURE_TODAY_PREDICTIONS=0 ./scripts/deploy_prd.sh --compose-file docker-compose-prd.yaml
```

---

## Quick Start (Local Dev)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Prerequisites: PostgreSQL + Redis running locally
python manage.py migrate
python manage.py seed_data
python manage.py fetch_and_predict --days 6
python manage.py runserver
```

---

## Leagues & Priority

All predictions are grouped and displayed by league priority:

| Priority | League | Code | Data Source |
|----------|--------|------|-------------|
| 1 | Premier League (EPL) | `PL` | ESPN → football-data.org |
| 2 | La Liga | `LL` | ESPN → football-data.org |
| 3 | Serie A | `SA` | ESPN → football-data.org |
| 4 | Bundesliga | `BL1` | ESPN → OpenLigaDB |
| 5 | Ligue 1 | `FL1` | ESPN → football-data.org |

**Data source cascade**: ESPN is always tried first (free, no API key). If ESPN
returns no data for a league, the system falls back to football-data.org (when
an API key is configured) or OpenLigaDB (free, Bundesliga only).

League priorities are set via `seed_data` and can be adjusted in Django Admin
(`/admin/core/league/`).

---

## Automated Pipeline

All scheduling is handled by **Celery Beat** inside Docker — no system cron
needed. The beat schedule is defined in `config/settings/base.py` →
`CELERY_BEAT_SCHEDULE`.

### Task Schedule (all times UTC)

| Time | Task | Purpose |
|------|------|---------|
| **00:00** | `verify_predictions_availability` | Safety net: generate missing predictions for today |
| **00:05** | `fetch_and_predict_scheduled` | Fetch ESPN fixtures + generate predictions for **today + next 5 days** |
| **00:30** | `compute_accuracy_stats_task` | Write daily `AccuracyRecord` snapshots per league |
| **06:00** | `check_accuracy_drift_task` | Compare recent accuracy vs baseline; auto-retrain if drift > 5% |
| **07:00–23:59** (every 20 min) | `update_live_scores_task` | Refresh in-play scores from ESPN, lock predictions at kickoff |
| **23:30** | `resolve_finished_matches_task` | Fetch final scores, resolve predictions, update ELO, trigger incremental retrain if needed |
| **Monday 03:00** | `weekly_full_retrain_task` | Full model retrain on 3 seasons of historical match data |

### Data Flow

```
ESPN API  ──►  Match records (DB)
                    │
                    ▼
          Feature Engineering
          (form, ELO, H2H, congestion, goals, league stats)
                    │
                    ▼
          ┌─────────────────────┐
          │   Ensemble Model    │
          │  ┌──────┬────────┐  │
          │  │ XGB  │ Neural │  │  ← Weighted average
          │  │ 0.4  │  0.3   │  │
          │  ├──────┼────────┤  │
          │  │ ELO (always)  │  │  ← Fallback when
          │  │     0.3       │  │    trained models
          │  └──────┴────────┘  │    unavailable
          └─────────────────────┘
                    │
                    ▼
          Prediction record
          (home_win_prob, draw_prob, away_win_prob, confidence)
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
  User sees prediction    End of day:
  on /predictions/        resolve(actual_outcome)
                          → is_correct = True / False
                          → ELO update
                          → AccuracyRecord snapshot
                          → Retrain decision
```

---

## Continuous Learning System

The platform uses a **supervised + online learning** loop to improve accuracy
over time.

### How It Works

1. **Supervised Learning (Weekly + On-demand)**
   - **Full retrain** (Monday 03:00): Builds training dataset from up to 3
     seasons of finished matches with known outcomes. Generates feature vectors
     via `FeatureEngineeringService`, trains XGBoost + Neural Network, evaluates
     on held-out set, and promotes the new model only if ensemble accuracy
     improved.
   - **Incremental retrain** (triggered automatically): Uses last 90 days of
     resolved data. Faster than full retrain. Triggered when:
     - ≥ 100 new resolved predictions since the last training run, **or**
     - Accuracy drift > 5% detected by the daily drift monitor.

2. **Online Learning (ELO Ratings)**
   - After every finished match, team ELO ratings are updated using the
     standard ELO formula (`K=32`). This is a form of continuous online
     learning — the ELO predictor immediately reflects the latest match
     results in subsequent predictions.

3. **Drift Detection (Daily 06:00)**
   - Compares accuracy of the last 7 days of resolved predictions against
     the all-time baseline.
   - If degradation exceeds the 5% threshold, an **emergency incremental
     retrain** is triggered automatically.

4. **Accuracy Tracking**
   - Every resolved game-day produces `AccuracyRecord` snapshots per league
     (daily granularity) stored in the `accuracy_records` table.
   - Overall + per-outcome accuracy (home win, draw, away win) tracked.
   - Powers the public accuracy dashboard at `/accuracy/`.

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

### Model Registry

Models are versioned in the `ModelVersion` table. Only one version is active
at a time. The `_register_if_improved` method in `TrainingPipeline` ensures
that a new model is only promoted if its accuracy exceeds the current active
model. Previous model versions are retained for audit.

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

### League Quota Distribution

Subscription daily limits are distributed across leagues using
`LeagueAccessRule` (configurable in Django Admin):

- **Premier League**: 50% of daily quota (default)
- **Other 4 leagues**: Equal share of remaining 50%
- If the priority league has no fixtures that day, its share redistributes
  equally across the others.

### 5-Day Lookahead (Subscribers Only)

Monthly subscribers can navigate up to 5 days into the future on the
predictions page. The system auto-fetches and generates predictions on-demand
if no data exists for the selected date.

---

## Payment System

Region-aware payment processing using the Strategy Design Pattern.

| Region | Currency | Payment Methods |
|--------|----------|-----------------|
| East Africa (KE, UG, TZ) | KES | M-Pesa |
| Global | USD | PayPal, Stripe |

See `PAYMENT_SYSTEM.md` and `IMPLEMENTATION_SUMMARY.md` for full payment
documentation.

---

## Management Commands

| Command | Description |
|---------|-------------|
| `python manage.py seed_data` | Bootstrap leagues, plans, pricing tiers |
| `python manage.py fetch_and_predict --days 6` | Fetch fixtures + generate predictions for N days |
| `python manage.py fetch_and_predict --date 2026-03-01 --days 3` | Fetch for a specific date range |
| `python manage.py fetch_and_predict --force-predict` | Regenerate predictions even if they exist |
| `python manage.py create_test_users` | Create test accounts for every subscription tier |

---

## Project Structure

```
futurapredict/
├── apps/
│   ├── core/                  # Leagues, Teams, Matches, Seasons
│   │   ├── models.py          # League, Team, Match, Season, LeagueAccessRule
│   │   ├── services/
│   │   │   ├── data_ingestion.py         # ESPN + football-data.org + OpenLigaDB
│   │   │   ├── feature_engineering.py    # 32+ ML features + ELO update
│   │   │   └── results_resolution.py     # End-of-day: resolve + retrain loop
│   │   └── management/commands/
│   │       ├── seed_data.py
│   │       └── fetch_and_predict.py
│   │
│   ├── predictions/           # ML predictions
│   │   ├── models.py          # Prediction, ModelVersion, PredictionExplanation
│   │   ├── tasks.py           # All 7 Celery tasks (pipeline)
│   │   ├── ml/
│   │   │   ├── models/
│   │   │   │   ├── ensemble_model.py     # EnsemblePredictor + ELOPredictor
│   │   │   │   ├── xgboost_model.py      # XGBoostPredictor
│   │   │   │   └── neural_model.py       # NeuralPredictor (TensorFlow)
│   │   │   ├── training_pipeline.py      # Full + incremental training
│   │   │   └── feature_store.py          # Feature caching
│   │   └── services/
│   │       ├── prediction_service.py     # Generate predictions
│   │       ├── ensemble.py               # Wire ML + explainer
│   │       ├── model_registry.py         # Active model selection
│   │       └── explainer.py              # SHAP-based explanations
│   │
│   ├── payments/              # Subscription + credits + payments
│   │   ├── models.py          # SubscriptionPlan, PricingTier, UserSubscription
│   │   ├── strategies/        # M-Pesa, PayPal, Stripe, WhatsApp (Strategy Pattern)
│   │   └── services/
│   │       ├── payment_service.py
│   │       └── subscription_service.py
│   │
│   ├── users/                 # Authentication, profiles, middleware
│   │   ├── middleware.py       # SubscriptionMiddleware, region detection
│   │   └── models.py          # UserProfile, PredictionUsage
│   │
│   ├── analytics/             # Accuracy tracking, revenue, stats
│   │   └── models.py          # AccuracyRecord, RevenueSnapshot, PredictionStats
│   │
│   └── api/                   # Views + REST API
│       └── views.py           # PredictionsView, PredictionDetailView, etc.
│
├── config/
│   ├── celery.py              # Celery app
│   ├── settings/
│   │   └── base.py            # CELERY_BEAT_SCHEDULE + ML_CONFIG
│   └── urls.py
│
├── templates/                 # Django templates (Bootstrap 5)
├── static/                    # CSS, JS, images
├── docker-compose.yaml        # 5 services: web, worker, beat, redis, db
├── Dockerfile                 # Multi-service image
├── entrypoint.sh              # Service dispatcher (web|worker|beat|setup)
└── requirements.txt
```

---

## Current Compromises

These are known trade-offs made in the current implementation:

### 1. ESPN API as Primary Data Source
- **Compromise**: ESPN's public scoreboard API is unofficial and undocumented.
  It does not require an API key, which is convenient, but ESPN could change
  endpoints, rate-limit, or deprecate them without notice.
- **Mitigation**: football-data.org (key-based) and OpenLigaDB act as
  fallbacks. The cascade pattern (`ESPN → football-data.org → OpenLigaDB`)
  ensures resilience.

### 2. ELO-Heavy Fallback When Trained Models Are Unavailable
- **Compromise**: Until the system accumulates ≥ 200 resolved matches, the
  XGBoost and Neural models cannot be trained. During this cold-start phase,
  the ensemble effectively falls back to pure ELO predictions with random
  noise for variety.
- **Mitigation**: The ELO predictor includes a home-advantage bias and
  calibrated draw margins. Once enough data accumulates, the weekly retrain
  automatically promotes a proper ensemble.

### 3. In-Process Retrain (Not Offloaded to GPU)
- **Compromise**: Model retraining runs inside the Celery worker process.
  For large datasets or deep neural architectures, this could block the worker
  for extended periods.
- **Mitigation**: `max-tasks-per-child=200` ensures worker processes are
  recycled. The weekly retrain runs Monday 03:00 when user traffic is lowest.
  Incremental retrains use only 90 days of data for speed.

### 4. Synchronous Fetch-on-Demand in PredictionsView
- **Compromise**: When a subscriber navigates to a future date with no
  predictions, the view calls `fetch_and_predict` synchronously (within the
  HTTP request). If ESPN is slow, the page load will be slow.
- **Mitigation**: The midnight pipeline pre-fetches 6 days ahead, so this
  on-demand path is rarely triggered. A loading spinner is shown in the UI.

### 5. Single-Instance Celery Beat
- **Compromise**: Only one Celery Beat instance can run at a time (it uses a
  database-backed schedule). Running multiple beat processes would cause
  duplicate task execution.
- **Mitigation**: `docker compose` enforces `replicas: 1` for the beat
  service. In production Kubernetes deployments, use a `Deployment` with
  `replicas: 1` and a leader-election sidecar.

### 6. Team Matching by Name
- **Compromise**: ESPN team names are matched to database records using
  case-insensitive name comparison. Different sources may use different
  names for the same team (e.g., "Atletico Madrid" vs "Atlético de Madrid").
- **Mitigation**: The ingestion layer does a broad cross-league search and
  creates new records when no match is found. Over time, duplicates may need
  manual deduplication via Django Admin.

### 7. No Unsupervised Learning Component (Yet)
- **Compromise**: The summary mentions unsupervised learning, but the current
  implementation focuses on supervised learning (labeled outcomes) and online
  learning (ELO). True unsupervised components (clustering, anomaly detection)
  are planned but not yet implemented.
- **Mitigation**: ELO rating updates provide a form of continuous,
  label-free adaptation. The drift detection mechanism serves as a proxy
  for distribution shift monitoring.

---

## Areas of Improvement

### High Priority

1. **Unsupervised Learning Integration**
   - Add K-means or DBSCAN clustering on feature vectors to group matches by
     "profile" (e.g., top-6 clashes, relegation battles, derbies).
   - Use Isolation Forest or autoencoders to detect **outlier matches** whose
     feature profiles are far from the training distribution — flag these with
     lower confidence or route to a specialised sub-model.
   - Apply **Platt scaling** or isotonic regression to calibrate prediction
     confidence scores against actual observed frequencies.

2. **Async Fetch-on-Demand**
   - Replace the synchronous `_ensure_predictions_for_date()` call in
     `PredictionsView` with a Celery task + WebSocket/SSE push. The user
     would see a loading state and predictions would appear when ready.

3. **Team Name Normalisation**
   - Build a `TeamAlias` model that maps variant names to canonical team
     records. Seed it with known aliases from different data sources. This
     prevents duplicate teams from accumulating.

4. **Additional Data Sources**
   - Integrate the **football-data.org v4 live endpoint** for richer in-play
     data (xG, possession, shots on target).
   - Add **historical season data import** — fetch 3+ seasons of past results
     from football-data.org or FBref on first setup to bootstrap the training
     pipeline with enough labelled data for immediate model training.

### Medium Priority

5. **Model Explainability Dashboard**
   - Surface SHAP values and feature importance charts on the prediction
     detail page. The `ExplainerService` already generates them — they just
     need a frontend.

6. **A/B Testing Framework**
   - Keep the previous ModelVersion active for a percentage of users and
     compare real-world accuracy of old vs new model before full promotion.

7. **Per-League Sub-Models**
   - Train separate XGBoost models per league instead of a single global
     model. Different leagues have different dynamics (e.g., Bundesliga is
     higher-scoring than Serie A).

8. **Kubernetes Deployment**
   - The `infrastructure/kubernetes/` directory has scaffolding but needs
     updating to match the current Docker Compose architecture. Add proper
     HPA (Horizontal Pod Autoscaler) for workers and a CronJob resource as
     a Celery Beat alternative.

### Low Priority / Nice-to-Have

9. **Prediction Confidence Calibration**
    - Compare stated confidence vs actual accuracy at each bucket (e.g., "of
      all predictions with 70–80% confidence, what % were actually correct?").
      Use this to calibrate outputs.

10. **Push Notifications**
    - Send prediction results (correct/incorrect) to subscribers via email or
      WhatsApp after match resolution.

11. **API Rate Limiting per Source**
    - Add per-source rate limiters (token bucket) to avoid hitting ESPN or
      football-data.org throttle limits during high-traffic periods.

12. **Feature Store Versioning**
    - Tag feature snapshots with the feature engineering version that produced
      them, so model retraining always uses consistently generated features.

13. **Flower Dashboard**
    - Add a `flower` service to `docker-compose.yaml` for real-time Celery
      task monitoring (already in `requirements.txt` as an optional dep).

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

---

## License

Proprietary. All rights reserved.
