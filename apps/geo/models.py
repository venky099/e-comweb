"""Countries, currencies and exchange rates.

The spec (MST section 8) calls multi-currency "one of the most important
requirements", and section 60 fixes the architecture:

    base currency -> exchange rate engine -> customer currency

with the order permanently storing the rate it was charged at. That last part
is the whole point. Product prices live in exactly one currency forever; every
other currency is derived at display time, and frozen onto the order at
checkout. Nothing ever re-converts a historical invoice.
"""
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Currency(TimeStampedModel):
    """A currency the storefront can display prices in.

    ``is_base`` marks the one currency product prices are stored in. Exactly
    one row may carry it; ``save()`` enforces that rather than trusting
    whoever is editing the admin.
    """

    class Rounding(models.TextChoices):
        NONE = "none", _("Exact (no rounding)")
        NEAREST = "nearest", _("Nearest whole unit")
        UP = "up", _("Always up (charm pricing)")

    code = models.CharField(
        max_length=3, unique=True, help_text=_("ISO 4217, e.g. INR, USD, GBP")
    )
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, help_text=_("e.g. ₹, $, £"))
    symbol_is_prefix = models.BooleanField(
        default=True, help_text=_("Shown before the amount rather than after.")
    )
    decimal_places = models.PositiveSmallIntegerField(default=2)
    rounding = models.CharField(
        max_length=10, choices=Rounding.choices, default=Rounding.NONE
    )
    is_base = models.BooleanField(
        default=False,
        help_text=_("The currency product prices are stored in. Only one."),
    )
    is_active = models.BooleanField(
        default=True, help_text=_("Offered to customers in the currency selector.")
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name_plural = _("currencies")

    def __str__(self):
        return f"{self.code} ({self.symbol})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_base:
            Currency.objects.exclude(pk=self.pk).filter(is_base=True).update(
                is_base=False
            )

    def quantize(self, amount):
        """Round ``amount`` the way this currency is displayed and charged."""
        amount = Decimal(amount)
        if self.rounding == self.Rounding.NEAREST:
            return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if self.rounding == self.Rounding.UP:
            return amount.quantize(Decimal("1"), rounding=ROUND_CEILING)
        exponent = Decimal(1).scaleb(-self.decimal_places)
        return amount.quantize(exponent, rounding=ROUND_HALF_UP)

    def format(self, amount):
        """Render an amount for display, symbol placed per this currency."""
        quantized = self.quantize(amount)
        places = 0 if self.rounding != self.Rounding.NONE else self.decimal_places
        text = f"{quantized:,.{places}f}"
        return f"{self.symbol}{text}" if self.symbol_is_prefix else f"{text} {self.symbol}"


class Country(TimeStampedModel):
    """A country the storefront serves.

    Per spec section 9, the selected country determines currency, available
    payment methods, shipping options and charges, tax rules and delivery
    availability. This model is the anchor all of those hang off.
    """

    iso2 = models.CharField(
        max_length=2, unique=True, help_text=_("ISO 3166-1 alpha-2, e.g. IN, US, AE")
    )
    iso3 = models.CharField(max_length=3, blank=True)
    name = models.CharField(max_length=100, db_index=True)
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="countries",
        help_text=_("Currency customers in this country see by default."),
    )
    dial_code = models.CharField(max_length=8, blank=True, help_text=_("e.g. +91"))
    is_active = models.BooleanField(
        default=True, help_text=_("Selectable in the country selector.")
    )
    shipping_enabled = models.BooleanField(
        default=True, help_text=_("Orders may be delivered here.")
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = _("countries")
        indexes = [models.Index(fields=["is_active", "shipping_enabled"])]

    def __str__(self):
        return self.name


class State(TimeStampedModel):
    """A state, province or region.

    Needed for tax: India charges CGST+SGST within a state and IGST across
    state lines, so the destination state is part of the tax key.
    """

    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="states"
    )
    name = models.CharField(max_length=100, db_index=True)
    code = models.CharField(max_length=8, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "name"], name="geo_state_unique_per_country"
            )
        ]

    def __str__(self):
        return f"{self.name}, {self.country.name}"


class ExchangeRate(TimeStampedModel):
    """One rate, from the base currency to another, at a point in time.

    Rates are append-only history rather than a single mutable number: an
    order stores the rate it used, and being able to see what the rate was on
    a given day is what makes a disputed invoice checkable.
    """

    class Source(models.TextChoices):
        AUTOMATIC = "automatic", _("Exchange-rate API")
        MANUAL = "manual", _("Set by an administrator")

    base = models.ForeignKey(
        Currency, on_delete=models.CASCADE, related_name="rates_from"
    )
    quote = models.ForeignKey(
        Currency, on_delete=models.CASCADE, related_name="rates_to"
    )
    rate = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        validators=[MinValueValidator(Decimal("0.00000001"))],
        help_text=_("Units of the quote currency for one unit of the base."),
    )
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.AUTOMATIC
    )
    effective_from = models.DateTimeField(db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [models.Index(fields=["base", "quote", "-effective_from"])]

    def __str__(self):
        return f"1 {self.base.code} = {self.rate} {self.quote.code}"

    @staticmethod
    def cache_key(base_code, quote_code):
        return f"geo:rate:{base_code}:{quote_code}"

    def save(self, *args, **kwargs):
        """Recording a rate must take effect immediately.

        Invalidating here rather than in the callers means every path -- the
        admin, a management command, a fixture, a service function -- is
        covered. Relying on callers to remember left the cache serving a
        superseded rate for up to five minutes.
        """
        super().save(*args, **kwargs)
        cache.delete(self.cache_key(self.base.code, self.quote.code))

    def delete(self, *args, **kwargs):
        key = self.cache_key(self.base.code, self.quote.code)
        result = super().delete(*args, **kwargs)
        cache.delete(key)
        return result
