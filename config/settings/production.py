"""
Production environment settings.
"""

from .base import *

DEBUG = False

ALLOWED_HOSTS = env_csv('ALLOWED_HOSTS') or ['*']

# Trust HTTPS info from reverse proxies (Nginx/Load Balancer).
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Accept CSRF POSTs from configured HTTPS origins (e.g. apex + www domains).
CSRF_TRUSTED_ORIGINS = env_csv('CSRF_TRUSTED_ORIGINS')
if not CSRF_TRUSTED_ORIGINS and SITE_URL.startswith('https://'):
    CSRF_TRUSTED_ORIGINS = [SITE_URL]

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_SECURITY_POLICY = {
    'default-src': ("'self'",),
    'script-src': ("'self'", "'unsafe-inline'"),
    'style-src': ("'self'", "'unsafe-inline'"),
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        # Support both legacy DB_* and current POSTGRES_* env variable names.
        'NAME': os.environ.get('DB_NAME') or os.environ.get('POSTGRES_DB', 'futurapredict'),
        'USER': os.environ.get('DB_USER') or os.environ.get('POSTGRES_USER', 'futurapredict'),
        'PASSWORD': os.environ.get('DB_PASSWORD') or os.environ.get('POSTGRES_PASSWORD', 'futurapredict_pass'),
        'HOST': os.environ.get('DB_HOST') or os.environ.get('POSTGRES_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT') or os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': 600,
    }
}

LOGGING['root']['level'] = 'WARNING'

STATIC_URL = '/static/'
STATIC_ROOT = '/app/staticfiles'

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', True)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL') or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/2"
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND') or 'django-db'
