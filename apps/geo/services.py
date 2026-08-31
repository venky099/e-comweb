"""Currency conversion and the rules around it.

Everything that turns a stored base-currency price into something a customer
sees goes through here, so there is one place where rounding is decided.

The order of operations matters and is fixed deliberately:

    compute the whole total in the base currency, THEN convert once.

Converting each line and summing the results produces a total that disagrees
with the sum of its own lines by a rounding unit -- the classic multi-currency
bug, which surfaces as customer complaints rather than exceptions.
"""
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.geo.models import Country, Currency, ExchangeRate

RATE_CACHE_SECONDS = 300
ONE = Decimal("1")


class CurrencyError(Exception):
    """Raised when a currency or rate that must exist does not."""


def base_currency(required=True):
    """The currency product prices are stored in.

    Raises when none is configured, because converting without a base is
    guesswork. Pass ``required=False`` on display paths, which must degrade
    rather than take the site down -- see ``fallback_currency``.
    """
    currency = Currency.objects.filter(is_base=True).first()
    if currency is None and required:
        raise CurrencyError(
            "No base currency is configured. Run: python manage.py seed_geo"
        )
    return currency


def fallback_currency():
    """An unsaved Currency mirroring the single-currency settings.

    A database with no geo rows -- a fresh clone, a test that does not care
    about currency -- must still render prices. This reproduces the behaviour
    the project had before multi-currency existed: one currency, no
    conversion.
    """
    return Currency(
        code=getattr(settings, "DEFAULT_CURRENCY", "INR"),
        name=getattr(settings, "DEFAULT_CURRENCY", "INR"),
        symbol=getattr(settings, "CURRENCY_SYMBOL", "₹"),
        symbol_is_prefix=True,
        decimal_places=2,
        is_base=True,
        is_active=True,
    )


def display_currency():
    """The base currency, or the settings-derived stand-in when unconfigured."""
    return base_currency(required=False) or fallback_currency()


def active_currencies():
    return Currency.objects.filter(is_active=True)


def active_countries():
    return Country.objects.filter(is_active=True).select_related("currency")


def get_currency(code, fallback_to_base=True):
    """Look up a currency by code, optionally falling back to the base one."""
    if code:
        currency = Currency.objects.filter(code=str(code).upper(), is_active=True).first()
        if currency is not None:
            return currency
    if fallback_to_base:
        return base_currency()
    return None


def get_country(iso2, fallback=True):
    if iso2:
        country = Country.objects.filter(iso2=str(iso2).upper(), is_active=True).first()
        if country is not None:
            return country
    return active_countries().first() if fallback else None


def rate_for(currency, on_date=None):
    """The rate from the base currency to ``currency``.

    Returns ``Decimal("1")`` for the base currency itself. Raises rather than
    guessing when no rate has ever been recorded -- silently charging at 1:1
    would sell everything at a fraction of its price.
    """
    base = base_currency()
    if currency.pk == base.pk:
        return ONE

    moment = on_date or timezone.now()
    is_now = on_date is None
    cache_key = ExchangeRate.cache_key(base.code, currency.code)

    if is_now:
        cached = cache.get(cache_key)
        if cached is not None:
            return Decimal(cached)

    row = (
        ExchangeRate.objects.filter(
            base=base, quote=currency, effective_from__lte=moment
        )
        .order_by("-effective_from")
        .first()
    )
    if row is None:
        raise CurrencyError(
            f"No exchange rate recorded for {base.code} to {currency.code}."
        )

    if is_now:
        cache.set(cache_key, str(row.rate), RATE_CACHE_SECONDS)
    return row.rate


def convert(amount, currency, rate=None):
    """Convert a base-currency amount, rounded the way ``currency`` displays.

    Pass ``rate`` explicitly when reproducing a historical figure -- an order
    stores the rate it was charged at, and reading it back must not pick up
    today's rate instead.
    """
    if amount is None:
        return None
    if rate is None:
        rate = rate_for(currency)
    return currency.quantize(Decimal(amount) * Decimal(rate))


def set_rate(currency, rate, source=ExchangeRate.Source.MANUAL, note=""):
    """Record a new rate, effective now."""
    base = base_currency()
    if currency.pk == base.pk:
        raise CurrencyError("The base currency does not have a rate against itself.")
    return ExchangeRate.objects.create(
        base=base,
        quote=currency,
        rate=Decimal(rate),
        source=source,
        effective_from=timezone.now(),
        note=note,
    )
