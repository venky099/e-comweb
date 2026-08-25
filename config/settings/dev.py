"""Development settings."""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR, INSTALLED_APPS, REST_FRAMEWORK, env

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "testserver"]

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-dev-only-key-do-not-use-in-production-0123456789",
)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# PostgreSQL is the target database. Set USE_SQLITE=True in .env only when you
# need to run the project without a local Postgres server (the ORM layer is
# engine-agnostic; full-text search degrades to icontains -- see
# apps.catalog.search).
if env.bool("USE_SQLITE", default=False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "init_command": "PRAGMA foreign_keys=ON;"},
        }
    }
    # django.contrib.postgres pulls in Postgres-only lookups/validators.
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "django.contrib.postgres"]
else:
    DATABASES = {
        "default": env.db(
            "DATABASE_URL",
            default="postgres://postgres:postgres@localhost:5432/ecommerce",
        )
    }
    DATABASES["default"]["CONN_MAX_AGE"] = 0

# WhiteNoise serves from the staticfiles finders in development, so there is
# no need to run collectstatic before the dev server works.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

# Serve uploaded media straight from the filesystem in development.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Browsable API is handy while developing against the REST layer.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ),
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

CORS_ALLOW_ALL_ORIGINS = True

# Passwords hash slowly by design; speed the test suite up without changing
# production behaviour.
if env.bool("FAST_HASHING", default=False):
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
