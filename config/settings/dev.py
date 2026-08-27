"""Development settings."""
from .base import *  # noqa: F401,F403
from . import db
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
# PostgreSQL is the target. When no server is answering -- a fresh clone, a
# stopped container, the placeholder URL from .env.example -- this drops to
# SQLite so the project runs immediately, and says so on stdout.
#
#   USE_SQLITE=True    force SQLite, skip the probe
#   DB_FALLBACK=False  require PostgreSQL and fail loudly if it is absent
#
# See config/settings/db.py. Production never does this.
_db_config, _db_backend, _db_reason = db.resolve(env, BASE_DIR)
DATABASES = {"default": _db_config}

if _db_backend == "sqlite":
    # django.contrib.postgres pulls in Postgres-only lookups and validators.
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "django.contrib.postgres"]
    if "no server answering" in _db_reason:
        # Printed rather than logged: logging is not configured this early, and
        # a newcomer needs to see why their data is not in Postgres.
        print(
            f"\n  Using SQLite -- {_db_reason}."
            "\n  Set DATABASE_URL and start PostgreSQL to use it instead,"
            "\n  or set DB_FALLBACK=False to make its absence an error.\n"
        )
else:
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
