"""Resolving which country and currency a visitor is shopping in.

Spec section 10: the system may detect a country from browser locale or IP,
but "customer must be able to manually change it" and "do not force IP
location". So an explicit choice always wins and is remembered; detection only
ever supplies the first guess.

Precedence, highest first:

    1. session   -- the visitor picked it from the selector
    2. country   -- currency defaults to the selected country's currency
    3. detection -- Accept-Language header, then any IP-country header a
                    proxy or CDN has set
    4. defaults  -- the first active country, and the base currency
"""
from decimal import Decimal

from django.utils.functional import SimpleLazyObject
from django.utils.translation import get_language

from apps.geo import services

ONE = Decimal("1")

SESSION_COUNTRY = "geo_country"
SESSION_CURRENCY = "geo_currency"

# Headers a CDN or reverse proxy sets when it resolves the client's country.
# None of these are trusted for anything but a first guess.
IP_COUNTRY_HEADERS = (
    "HTTP_CF_IPCOUNTRY",           # Cloudflare
    "HTTP_CLOUDFRONT_VIEWER_COUNTRY",  # AWS CloudFront
    "HTTP_X_APPENGINE_COUNTRY",
)


class Locale:
    """The country, currency and language for one request."""

    __slots__ = ("country", "currency", "rate", "language", "detected")

    def __init__(self, country, currency, rate, language, detected=False):
        self.country = country
        self.currency = currency
        self.rate = rate
        self.language = language
        self.detected = detected

    @property
    def is_base_currency(self):
        return self.rate == 1

    def money(self, amount):
        """Convert a base-currency amount into this request's currency."""
        return services.convert(amount, self.currency, rate=self.rate)

    def display(self, amount):
        """Convert and format, e.g. ``$58.00``."""
        return self.currency.format(self.money(amount))


def country_from_headers(request):
    """A best guess at the visitor's country. Never authoritative."""
    for header in IP_COUNTRY_HEADERS:
        code = request.META.get(header, "").strip()
        if code and code.upper() not in {"XX", "T1"}:
            country = services.get_country(code, fallback=False)
            if country is not None:
                return country

    # Accept-Language carries a region for most browsers: "en-GB", "ta-IN".
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    for chunk in header.split(","):
        tag = chunk.split(";")[0].strip()
        if "-" in tag:
            country = services.get_country(tag.split("-")[-1], fallback=False)
            if country is not None:
                return country
    return None


def resolve(request):
    """Build the ``Locale`` for this request.

    Degrades rather than raising: a database with no geo rows yields the
    single-currency behaviour the project had before multi-currency, so a
    fresh clone and the existing test suite both still render.
    """
    if services.base_currency(required=False) is None:
        currency = services.fallback_currency()
        return Locale(
            country=None,
            currency=currency,
            rate=ONE,
            language=get_language(),
            detected=False,
        )

    session = getattr(request, "session", None)
    stored_country = session.get(SESSION_COUNTRY) if session else None
    stored_currency = session.get(SESSION_CURRENCY) if session else None

    detected = False
    country = services.get_country(stored_country, fallback=False)
    if country is None:
        country = country_from_headers(request)
        detected = country is not None
    if country is None:
        country = services.get_country(None)

    # An explicitly chosen currency outlives a change of country; otherwise the
    # country decides, which is what a visitor expects when they switch to the
    # United States and see dollars.
    currency = services.get_currency(stored_currency, fallback_to_base=False)
    if currency is None and country is not None:
        currency = country.currency
    if currency is None:
        currency = services.base_currency()

    try:
        rate = services.rate_for(currency)
    except services.CurrencyError:
        # A currency with no rate must not take the site down. Fall back to
        # the base currency, which always has an implicit rate of 1.
        currency = services.base_currency()
        rate = services.rate_for(currency)

    return Locale(
        country=country,
        currency=currency,
        rate=rate,
        language=get_language(),
        detected=detected,
    )


class LocaleMiddleware:
    """Attaches ``request.locale`` to every request.

    Resolution is lazy: a request that never renders a price never touches the
    database for one.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Same pattern as request.user: resolved on first access, then cached
        # for the rest of the request.
        request.locale = SimpleLazyObject(lambda: resolve(request))
        return self.get_response(request)
