"""Settings used by the test suite.

``manage.py test`` selects this module automatically (see manage.py) so the
suite is deterministic: no rate limiting, no shared cache between test cases,
and fast password hashing.
"""
from .base import *  # noqa: F401,F403
from . import db
from .base import BASE_DIR, INSTALLED_APPS, REST_FRAMEWORK, env

DEBUG = False
SECRET_KEY = "test-only-secret-key-not-used-anywhere-else"
ALLOWED_HOSTS = ["*"]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Same resolution as development, so `manage.py test` works on a fresh clone
# with no PostgreSQL installed.
_db_config, _db_backend, _db_reason = db.resolve(env, BASE_DIR)
DATABASES = {"default": _db_config}

if _db_backend == "sqlite":
    DATABASES["default"]["TEST"] = {"NAME": None}  # in-memory
    INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "django.contrib.postgres"]

# ---------------------------------------------------------------------------
# Speed & determinism
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# django-ratelimit shares state through the cache; leaving it on would make
# test order affect test outcomes.
RATELIMIT_ENABLE = False

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Media written by tests goes to a scratch directory, not the real one.
MEDIA_ROOT = BASE_DIR / "media_test"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

WHITENOISE_AUTOREFRESH = True

# The mock gateway keeps payment tests hermetic -- no network calls.
PAYMENT_GATEWAY = "mock"
PAYMENT_KEY = ""
PAYMENT_SECRET = ""

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    # Throttling is a production concern; it only makes tests order-dependent.
    # The scoped rates stay defined (views declare their scope explicitly and
    # ScopedRateThrottle raises without one) but are set high enough never to
    # trip during a test run.
    "DEFAULT_THROTTLE_CLASSES": (),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100000/min",
        "user": "100000/min",
        "login": "100000/min",
        "register": "100000/min",
        "checkout": "100000/min",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
    "loggers": {
        "django.request": {"handlers": ["null"], "level": "CRITICAL", "propagate": False},
        "ecommerce": {"handlers": ["null"], "level": "CRITICAL", "propagate": False},
    },
}
