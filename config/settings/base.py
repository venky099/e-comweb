"""
Base settings shared by every environment.

Environment-specific modules (``dev.py`` / ``prod.py``) import everything from
here and then override what differs. Never point DJANGO_SETTINGS_MODULE at this
module directly -- use ``config.settings.dev`` or ``config.settings.prod``.
"""
from datetime import timedelta
from pathlib import Path

import environ
from django.contrib.messages import constants as message_constants

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# base.py -> settings/ -> config/ -> BASE_DIR
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    USE_S3=(bool, False),
    PAYMENT_GATEWAY=(str, "mock"),
    REDIS_URL=(str, ""),
)

# Read .env when present. Hosting platforms that inject real environment
# variables work without the file.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-CHANGE-ME-IN-PRODUCTION")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "crispy_forms",
    "crispy_bootstrap5",
]

LOCAL_APPS = [
    "apps.core.apps.CoreConfig",
    "apps.geo.apps.GeoConfig",
    "apps.tax.apps.TaxConfig",
    "apps.shipping.apps.ShippingConfig",
    "apps.invoices.apps.InvoicesConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.inventory.apps.InventoryConfig",
    "apps.cart.apps.CartConfig",
    "apps.wishlist.apps.WishlistConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.coupons.apps.CouponsConfig",
    "apps.reviews.apps.ReviewsConfig",
    "apps.marketing.apps.MarketingConfig",
    "apps.dashboard.apps.DashboardConfig",
    "apps.api.apps.ApiConfig",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.geo.locale_context.LocaleMiddleware",
    "apps.cart.middleware.CartMergeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_context",
                "apps.geo.context_processors.locale",
                "apps.catalog.context_processors.navigation",
                "apps.cart.context_processors.cart_summary",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database -- PostgreSQL via DATABASE_URL
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/ecommerce",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailOrUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

# ---------------------------------------------------------------------------
# I18N / TZ
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Sessions / messages
# ---------------------------------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # two weeks
SESSION_COOKIE_HTTPONLY = True
SESSION_SAVE_EVERY_REQUEST = False

MESSAGE_TAGS = {
    message_constants.DEBUG: "secondary",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
            "KEY_PREFIX": "ecom",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ecommerce-locmem",
        }
    }

CACHE_TTL_SHORT = 60
CACHE_TTL_MEDIUM = 60 * 15
CACHE_TTL_LONG = 60 * 60

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "600/min",
        "login": "10/min",
        "register": "10/hour",
        "checkout": "30/min",
    },
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "E-Commerce API",
    "DESCRIPTION": "REST API for the Django e-commerce platform (storefront + staff operations).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/",
    "SORT_OPERATIONS": False,
    # Several models expose a "status" field with different choice sets;
    # naming them explicitly keeps the generated client readable.
    "ENUM_NAME_OVERRIDES": {
        "OrderStatusEnum": "apps.orders.models.ORDER_STATUS_CHOICES",
        "OrderPaymentStatusEnum": "apps.orders.models.ORDER_PAYMENT_STATUS_CHOICES",
        "OrderItemStatusEnum": "apps.orders.models.ORDER_ITEM_STATUS_CHOICES",
        "ReturnStatusEnum": "apps.orders.models.RETURN_STATUS_CHOICES",
        "PaymentStatusEnum": "apps.payments.models.PAYMENT_STATUS_CHOICES",
        "ProductStatusEnum": "apps.catalog.models.PRODUCT_STATUS_CHOICES",
    },
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Business configuration (single source of truth -- never duplicated in JS)
# ---------------------------------------------------------------------------
SITE_NAME = env("SITE_NAME", default="Lumen Store")
SITE_TAGLINE = env("SITE_TAGLINE", default="Everything you need, delivered.")
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="support@lumenstore.test")
SUPPORT_PHONE = env("SUPPORT_PHONE", default="+91 90000 00000")
DEFAULT_CURRENCY = env("DEFAULT_CURRENCY", default="INR")
CURRENCY_SYMBOL = env("CURRENCY_SYMBOL", default="₹")

DELIVERY_CHARGE = env.str("DELIVERY_CHARGE", default="49.00")
FREE_DELIVERY_THRESHOLD = env.str("FREE_DELIVERY_THRESHOLD", default="999.00")
# Legacy flat rate. Used only where no tax rule covers the destination and
# the product carries no rate of its own -- see apps/tax/services.py.
TAX_RATE_PERCENT = env.str("TAX_RATE_PERCENT", default="0.00")

# The state the business ships from. India splits GST by destination: CGST
# and SGST within this state, IGST outside it.
TAX_ORIGIN_STATE = env.str("TAX_ORIGIN_STATE", default="Karnataka")

# Printed on invoices (spec sections 26 and 59).
COMPANY_ADDRESS = env.str("COMPANY_ADDRESS", default="")
COMPANY_TAX_NUMBER = env.str("COMPANY_TAX_NUMBER", default="")
DOCUMENT_PREFIX = env.str("DOCUMENT_PREFIX", default="MST")

# Section 59: whether catalogue prices already include tax.
PRICES_INCLUDE_TAX = env.bool("PRICES_INCLUDE_TAX", default=False)
COD_EXTRA_CHARGE = env.str("COD_EXTRA_CHARGE", default="0.00")
MAX_CART_QUANTITY_PER_ITEM = env.int("MAX_CART_QUANTITY_PER_ITEM", default=10)
ORDER_CANCEL_WINDOW_HOURS = env.int("ORDER_CANCEL_WINDOW_HOURS", default=24)
RETURN_WINDOW_DAYS = env.int("RETURN_WINDOW_DAYS", default=7)
LOW_STOCK_THRESHOLD = env.int("LOW_STOCK_THRESHOLD", default=5)

# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
PAYMENT_GATEWAY = env("PAYMENT_GATEWAY")  # "razorpay" | "stripe" | "mock"
PAYMENT_KEY = env("PAYMENT_KEY", default="")
PAYMENT_SECRET = env("PAYMENT_SECRET", default="")
PAYMENT_WEBHOOK_SECRET = env("PAYMENT_WEBHOOK_SECRET", default="")
PAYMENT_CURRENCY = env("PAYMENT_CURRENCY", default="INR")

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@lumenstore.test")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "ecommerce": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB uploads
