"""
Django settings for the MV Alaska Ship Package Booking System.

All secrets and environment-specific values come from `.env` via django-environ.
See `.env.example` for the required variables.
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# Fail-safe: never boot a deployed environment with DEBUG on. DEBUG=True leaks
# tracebacks with SECRET_KEY/DB DSN/settings on any 500, and turns off every
# hardening flag below. Railway sets RAILWAY_ENVIRONMENT on every deploy; if we
# see that marker with DEBUG still on, refuse to start rather than silently
# serve insecure. Local dev has no such marker, so DEBUG=True stays fine there.
if DEBUG and env("RAILWAY_ENVIRONMENT", default=""):
    raise ImproperlyConfigured(
        "DEBUG must be False in a deployed environment (RAILWAY_ENVIRONMENT is "
        "set). Set DEBUG=False on the Railway service before deploying."
    )


# Production transport/cookie hardening. Only active when DEBUG is off, so local
# dev (DEBUG=True, plain HTTP) is unaffected. Railway terminates TLS at its edge
# and forwards X-Forwarded-Proto, so SECURE_PROXY_SSL_HEADER lets Django see the
# request as HTTPS and enforce the redirect/secure-cookie flags correctly.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # ExclusionConstraint (package overlap guard)
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    # Local apps
    "apps.accounts",
    "apps.ships",
    "apps.packages",
    "apps.bookings",
    "apps.staff",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database

DATABASES = {
    "default": env.db("DATABASE_URL"),
}


# Custom user model — must be set before the first migration ever runs.
AUTH_USER_MODEL = "accounts.User"


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Dhaka"

USE_I18N = True

USE_TZ = True


# Static & media files

STATIC_URL = "static/"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS — frontend origins (Vite dev server, later Vercel) come from .env.

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])


# DRF — the public API is read-only and unauthenticated; write endpoints
# (Phase 3+) will declare their own stricter permissions.

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/min",
        "booking": "10/min",
        # Live price previews (quote) — fired per guest-count change in the
        # wizard, so far looser than actual booking creation.
        "quote": "60/min",
        # Staff login + token refresh: tight bucket to blunt credential
        # stuffing / password spraying against the admin dashboard. Keyed on
        # the real client IP (NUM_PROXIES set below), not a spoofable header.
        "login": "5/min",
    },
    # Trusted proxy hop count for throttling. Without it DRF keys throttle
    # buckets on the raw client-supplied X-Forwarded-For header, which lets
    # anyone bypass every rate limit (new header per request) or poison
    # someone else's bucket. Match the real proxy depth of the deployment
    # (Railway: 1).
    "NUM_PROXIES": env.int("DRF_NUM_PROXIES", default=1),
    "EXCEPTION_HANDLER": "config.exceptions.exception_handler",
}


# Staff dashboard auth — JWT (short-lived access + rotating refresh tokens).

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}


# SSLCommerz payment gateway — credentials only ever come from .env.

SSLCOMMERZ_STORE_ID = env("SSLCOMMERZ_STORE_ID", default="")
SSLCOMMERZ_STORE_PASSWORD = env("SSLCOMMERZ_STORE_PASSWORD", default="")
SSLCOMMERZ_IS_SANDBOX = env.bool("SSLCOMMERZ_IS_SANDBOX", default=True)

_SSLCOMMERZ_BASE = (
    "https://sandbox.sslcommerz.com"
    if SSLCOMMERZ_IS_SANDBOX
    else "https://securepay.sslcommerz.com"
)
SSLCOMMERZ_SESSION_URL = f"{_SSLCOMMERZ_BASE}/gwprocess/v4/api.php"
SSLCOMMERZ_VALIDATION_URL = f"{_SSLCOMMERZ_BASE}/validator/api/validationserverAPI.php"
# Transaction Query API — look up a session by OUR tran_id (no val_id needed).
# Used by reconcile_pending_payments and the fail/cancel redirect handlers.
SSLCOMMERZ_TXN_QUERY_URL = (
    f"{_SSLCOMMERZ_BASE}/validator/api/merchantTransIDvalidationAPI.php"
)

BACKEND_URL = env("BACKEND_URL", default="http://localhost:8000")
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# Unpaid PENDING bookings are auto-cancelled after this hold window.
BOOKING_HOLD_MINUTES = env.int("BOOKING_HOLD_MINUTES", default=30)

# Authority helpline numbers printed on the guide report & customer invoice
# (comma-separated). Change these without touching code.
AUTHORITY_PHONES = env(
    "AUTHORITY_PHONES", default="01712-823482,01831-694307,01342-919795"
)

# The gateway session lifetime: once a PENDING payment is older than this
# AND SSLCommerz's Transaction Query API reports no payment attempt on it,
# reconcile_pending_payments closes it as FAILED. (Room holds themselves are
# never released on a timer while a PENDING payment exists — only after the
# gateway has confirmed the session is dead.)
# Must be >= the gateway's own session lifetime.
PAYMENT_SESSION_MINUTES = env.int("PAYMENT_SESSION_MINUTES", default=30)

# How many days before the balance deadline the one-off reminder email goes
# out (enforce_due_deadlines). The deadline itself is per-package data:
# Package.balance_due_days_before_start.
BALANCE_DUE_REMINDER_DAYS = env.int("BALANCE_DUE_REMINDER_DAYS", default=2)

# Consecutive gateway-query failures on one PENDING payment before
# reconcile_pending_payments escalates it for manual review. A payment the
# gateway won't answer for holds its room out of inventory, so it must reach a
# human rather than spin forever.
PAYMENT_MAX_RECONCILE_ATTEMPTS = env.int("PAYMENT_MAX_RECONCILE_ATTEMPTS", default=5)

# An escalated payment is not abandoned — it is retried on this slow back-off.
# Gateway outages end, and a payment nobody asks about again keeps its cabin
# out of inventory forever; a recovered gateway auto-resolves the backlog and
# only genuinely undecidable payments wait for the human already notified.
PAYMENT_ESCALATED_RETRY_MINUTES = env.int(
    "PAYMENT_ESCALATED_RETRY_MINUTES", default=60
)


# Email — provider-agnostic SMTP config, everything from .env.
# Dev default prints emails to the console; production sets a real backend.

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MV Alaska <noreply@localhost>")
