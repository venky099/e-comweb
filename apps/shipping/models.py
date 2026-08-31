"""Shipping zones, methods and rate tables.

MST sections 28 to 31. The shape follows the spec's own example: countries are
grouped into zones, each zone offers methods (Standard, Express,
International Priority), and a rate table says what each combination costs for
a given parcel weight and order value.

    Zone 1  India            Standard   0-500g    Rs.80,  free over Rs.999
    Zone 5  USA / Canada     Express    0-500g    $25

All money here is in the base currency, like every other stored price.
Conversion happens once, at display and at checkout.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class ShippingZone(TimeStampedModel):
    """A group of countries that share rates."""

    name = models.CharField(max_length=64, unique=True)
    countries = models.ManyToManyField(
        "geo.Country", related_name="shipping_zones", blank=True
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ShippingMethod(TimeStampedModel):
    """How a parcel travels, and how long it is expected to take."""

    name = models.CharField(max_length=64)
    code = models.SlugField(max_length=32, unique=True)
    carrier = models.CharField(max_length=64, blank=True)
    min_days = models.PositiveSmallIntegerField(
        default=3, help_text=_("Fastest realistic delivery, in days.")
    )
    max_days = models.PositiveSmallIntegerField(
        default=7, help_text=_("Slowest realistic delivery, in days.")
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.max_days < self.min_days:
            raise ValidationError(
                {"max_days": _("The slowest estimate cannot be faster than the fastest.")}
            )

    @property
    def estimate(self):
        if self.min_days == self.max_days:
            return _("%(days)d days") % {"days": self.max_days}
        return _("%(min)d-%(max)d days") % {"min": self.min_days, "max": self.max_days}


class ShippingRate(TimeStampedModel):
    """What one zone/method combination costs for a band of parcels.

    Bands are half-open -- ``min <= value < max`` -- so adjacent rows do not
    both match at a boundary. An empty maximum means "and everything above".
    """

    zone = models.ForeignKey(
        ShippingZone, on_delete=models.CASCADE, related_name="rates"
    )
    method = models.ForeignKey(
        ShippingMethod, on_delete=models.CASCADE, related_name="rates"
    )
    min_weight_grams = models.PositiveIntegerField(default=0)
    max_weight_grams = models.PositiveIntegerField(
        blank=True, null=True, help_text=_("Leave empty for no upper limit.")
    )
    min_order_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text=_("In the base currency."),
    )
    max_order_value = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True,
        help_text=_("Leave empty for no upper limit."),
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)],
        help_text=_("In the base currency."),
    )
    free_over = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True,
        help_text=_("Order value at or above which this method ships free."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["zone", "method", "min_weight_grams", "min_order_value"]
        indexes = [models.Index(fields=["zone", "method", "is_active"])]

    def __str__(self):
        return f"{self.zone} / {self.method}: {self.price}"

    def clean(self):
        if self.max_weight_grams is not None and self.max_weight_grams <= self.min_weight_grams:
            raise ValidationError(
                {"max_weight_grams": _("The upper weight must exceed the lower one.")}
            )
        if self.max_order_value is not None and self.max_order_value <= self.min_order_value:
            raise ValidationError(
                {"max_order_value": _("The upper order value must exceed the lower one.")}
            )

    def covers(self, weight_grams, order_value):
        """Does this rate apply to a parcel of this weight and value?"""
        if weight_grams < self.min_weight_grams:
            return False
        if self.max_weight_grams is not None and weight_grams >= self.max_weight_grams:
            return False
        if Decimal(order_value) < self.min_order_value:
            return False
        if self.max_order_value is not None and Decimal(order_value) >= self.max_order_value:
            return False
        return True

    def charge_for(self, order_value):
        """The price, after any free-shipping threshold."""
        if self.free_over is not None and Decimal(order_value) >= self.free_over:
            return ZERO
        return self.price
