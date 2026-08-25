"""Production settings.

Every secret is read from the environment. Nothing sensitive is defaulted here
except values that are safe to be public.
"""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

# Fail loudly at boot rather than silently serving with an insecure key.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = env("DB_SSLMODE", default="require")

# ---------------------------------------------------------------------------
# HTTPS / cookies / headers
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # templates read the token via {% csrf_token %}
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# The admin lives at a non-guessable path in production (see config/urls.py).
ADMIN_URL = env("ADMIN_URL", default="staff-console/")

# ---------------------------------------------------------------------------
# Static & media -- S3 via django-storages when USE_S3 is on
# ---------------------------------------------------------------------------
if env.bool("USE_S3", default=False):
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default=None)
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default=None)
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="ap-south-1")
    AWS_S3_CUSTOM_DOMAIN = env(
        "AWS_S3_CUSTOM_DOMAIN",
        default=f"{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com",
    )
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_FILE_OVERWRITE = False

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {"location": "media", "default_acl": None},
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {"location": "static", "default_acl": None},
        },
    }
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/media/"
else:
    # WhiteNoise serves hashed static files straight from Gunicorn/Nginx.
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

ADMINS = [("Ops", env("OPS_EMAIL", default="ops@example.com"))]
SERVER_EMAIL = env("SERVER_EMAIL", default="django@example.com")

LOGGING["handlers"]["mail_admins"] = {  # noqa: F405
    "class": "django.utils.log.AdminEmailHandler",
    "level": "ERROR",
}
LOGGING["loggers"]["django.request"] = {  # noqa: F405
    "handlers": ["console", "mail_admins"],
    "level": "ERROR",
    "propagate": False,
}
