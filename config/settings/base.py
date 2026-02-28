"""
Base settings for futurapredict.
Contains shared configuration across all environments.
"""

import os
from pathlib import Path
from datetime import timedelta

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_csv(name: str, default: str = ""):
    """
    Parse a comma-separated env variable into a clean list.
    Empty values are removed to avoid invalid Django config entries.
    """
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(',') if item.strip()]

# Load .env file when available (optional dependency: python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, '.env'))
except Exception:
    # If python-dotenv is not installed or .env missing, continue silently
    pass
# Security
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.getenv('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = env_csv('ALLOWED_HOSTS') or ['*']

# Site configuration
SITE_URL = os.getenv('SITE_URL', 'http://127.0.0.1:8000')
GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET', '')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'django_celery_beat',
    'django_celery_results',
    'drf_yasg',  # API documentation
    'django_filters',
    'django_redis',
    
    # Local apps
    'apps.users',
    'apps.core',
    'apps.predictions',
    'apps.payments',
    'apps.analytics',
    'apps.api',
]

# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.users.middleware.SubscriptionMiddleware',  # Custom middleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'futurapredict'),
        'USER': os.getenv('POSTGRES_USER', 'postgres'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'postgres'),
        'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'connect_timeout': 10,
        }
    }
}

# Cache
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0",
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PASSWORD': os.getenv('REDIS_PASSWORD', ''),
        }
    },
    'predictions': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/1",
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PASSWORD': os.getenv('REDIS_PASSWORD', ''),
        }
    }
}

# Celery Configuration
CELERY_BROKER_URL = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/2"
CELERY_RESULT_BACKEND = 'django-db'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Scheduled Tasks
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    # ── 00:05 – Fetch fixtures for today + next 5 days & generate predictions ──
    'fetch-and-predict-daily': {
        'task': 'apps.predictions.tasks.fetch_and_predict_scheduled',
        'schedule': crontab(hour=0, minute=5),
        'options': {'expires': 3600},
    },

    # ── 00:30 – Daily accuracy snapshot (yesterday's resolved predictions) ─────
    'compute-accuracy-stats-daily': {
        'task': 'apps.predictions.tasks.compute_accuracy_stats_task',
        'schedule': crontab(hour=0, minute=30),
        'options': {'expires': 3600},
    },

    # ── 06:00 – Drift monitor: auto-retrain if accuracy dropped > 5 % ─────────
    'check-accuracy-drift-daily': {
        'task': 'apps.predictions.tasks.check_accuracy_drift_task',
        'schedule': crontab(hour=6, minute=0),
        'options': {'expires': 3600},
    },

    # ── Every 20 min (07:00–23:59) – Refresh in-play scores & lock predictions ─
    'update-live-scores': {
        'task': 'apps.predictions.tasks.update_live_scores_task',
        'schedule': crontab(minute='*/20', hour='7-23'),
        'options': {'expires': 300},
    },

    # ── 23:30 – End-of-day: resolve predictions, update ELO, maybe retrain ─────
    'resolve-finished-matches': {
        'task': 'apps.predictions.tasks.resolve_finished_matches_task',
        'schedule': crontab(hour=23, minute=30),
        'options': {'expires': 7200},
    },

    # ── Monday 03:00 – Full weekly retraining (3 seasons of history) ───────────
    'weekly-full-retrain': {
        'task': 'apps.predictions.tasks.weekly_full_retrain_task',
        'schedule': crontab(hour=3, minute=0, day_of_week='monday'),
        'options': {'expires': 14400},
    },

    # ── Legacy: verify predictions exist at midnight (safety net) ───────────────
    'verify-predictions-midnight': {
        'task': 'apps.predictions.tasks.verify_predictions_availability_task',
        'schedule': crontab(hour=0, minute=0),
        'options': {'expires': 1800},
    },
}
# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': '100/hour',
        'anon': '10/hour',
        'prediction': '50/day',
    },
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

# Subscription settings
SUBSCRIPTION_CONFIG = {
    'GRACE_PERIOD_DAYS': 3,
    'MAX_RENEWAL_ATTEMPTS': 3,
    'AUTO_RENEW_DAYS_BEFORE_EXPIRY': 7,
    'PREDICTION_LIMITS': {
        'free': 5,
        'basic': 50,
        'premium': 500,
        'enterprise': float('inf')
    }
}

# ML Model settings
ML_CONFIG = {
    'MODEL_REGISTRY_PATH': os.path.join(BASE_DIR, 'models/'),
    'FEATURE_STORE_PATH': os.path.join(BASE_DIR, 'features/'),
    'TRAINING_DATA_DAYS': 365 * 3,
    'MIN_MATCHES_FOR_PREDICTION': 5,
    'ENSEMBLE_WEIGHTS': {
        'xgboost': 0.4,
        'neural': 0.3,
        'bayesian': 0.2,
        'elo': 0.1
    }
}

# Payment providers
PAYMENT_CONFIG = {
    'STRIPE': {
        'PUBLIC_KEY': os.getenv('STRIPE_PUBLIC_KEY', ''),
        'SECRET_KEY': os.getenv('STRIPE_SECRET_KEY', ''),
        'WEBHOOK_SECRET': os.getenv('STRIPE_WEBHOOK_SECRET', ''),
    },
    'MPESA': {
        'CONSUMER_KEY': os.getenv('MPESA_CONSUMER_KEY', ''),
        'CONSUMER_SECRET': os.getenv('MPESA_CONSUMER_SECRET', ''),
        'SHORTCODE': os.getenv('MPESA_SHORTCODE', ''),
        'PASSKEY': os.getenv('MPESA_PASSKEY', ''),
    },
    'PAYPAL': {
        'CLIENT_ID': os.getenv('PAYPAL_CLIENT_ID', ''),
        'SECRET': os.getenv('PAYPAL_SECRET', ''),
        'ENVIRONMENT': os.getenv('PAYPAL_MODE', 'live'),  # Default to live
    }
}

# Root URL configuration
ROOT_URLCONF = 'config.urls'

# Templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.users.context_processors.region_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'futurapredict.wsgi.application'

# Authentication
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS
CORS_ALLOWED_ORIGINS = env_csv('CORS_ALLOWED_ORIGINS', 'http://localhost:3000')
CORS_ALLOW_CREDENTIALS = True

# JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
}

# Email
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@futurapredict.com')

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs/django.log'),
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'django': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
        'propagate': False,
    },
}

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# API Documentation (Swagger/OpenAPI)
SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header'
        }
    }
}

# Monitoring
STATSD_HOST = os.getenv('STATSD_HOST', 'localhost')
STATSD_PORT = int(os.getenv('STATSD_PORT', 8125))
