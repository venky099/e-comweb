"""Quoting delivery.

``quote()`` answers the question checkout asks: given this cart and this
address, what can the customer choose, what does each option cost, and how
long will it take?

Prices come back in the base currency. Converting here would mean the total
gets converted twice -- once for shipping, once for everything else -- and the
two roundings would not agree.
"""
from decimal import Decimal

from django.conf import settings

from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone

ZERO = Decimal("0.00")


class ShippingError(Exception):
    """Raised when a destination cannot be served at all."""


class ShippingOption:
    """One delivery choice a customer can pick."""

    __slots__ = ("method", "price", "rate", "is_free")

    def __init__(self, method, price, rate, is_free=False):
        self.method = method
        self.price = price
        self.rate = rate
        self.is_free = is_free

    @property
    def code(self):
        return self.method.code

    @property
    def name(self):
        return self.method.name

    @property
    def estimate(self):
        return self.method.estimate

    def __repr__(self):
        return f"<ShippingOption {self.method.code} {self.price}>"


def cart_weight_grams(items):
    """Total shipping weight of a cart or order, in grams."""
    total = 0
    for item in items:
        variant = item.variant
        weight = getattr(variant, "shipping_weight_grams", None)
        if weight is None:
            weight = getattr(variant, "weight_grams", None) or getattr(
                getattr(variant, "product", None), "weight_grams", 0
            ) or 0
        total += int(weight) * int(item.quantity)
    return total


def zone_for(country):
    """The active zone covering a country, or None."""
    if country is None:
        return None
    return (
        ShippingZone.objects.filter(is_active=True, countries=country)
        .order_by("sort_order", "name")
        .first()
    )


def quote(items, country, order_value):
    """Delivery options for these items to this country.

    Returns an empty list when nothing serves the destination -- the caller
    decides whether that is an error (checkout) or simply nothing to show
    (a cart page before an address is known).
    """
    items = list(items)
    if not items or country is None:
        return []
    if not country.shipping_enabled:
        return []

    zone = zone_for(country)
    if zone is None:
        return []

    weight = cart_weight_grams(items)
    value = Decimal(order_value)

    rates = (
        ShippingRate.objects.filter(zone=zone, is_active=True, method__is_active=True)
        .select_related("method")
        .order_by("method__sort_order", "method__name", "min_weight_grams")
    )

    options = {}
    for rate in rates:
        if not rate.covers(weight, value):
            continue
        # First matching band per method wins; rows are ordered narrowest
        # first, so a specific band beats a catch-all.
        if rate.method_id in options:
            continue
        charge = rate.charge_for(value)
        options[rate.method_id] = ShippingOption(
            method=rate.method,
            price=charge,
            rate=rate,
            is_free=charge <= ZERO,
        )

    return sorted(options.values(), key=lambda o: (o.price, o.method.sort_order))


def default_option(options):
    """What to preselect at checkout: the cheapest, then the fastest."""
    if not options:
        return None
    return sorted(options, key=lambda o: (o.price, o.method.min_days))[0]


def option_by_code(options, code):
    """Find a chosen option, or None if it is no longer on offer."""
    for option in options:
        if option.code == code:
            return option
    return None


def legacy_flat_charge(order_value):
    """The pre-zones behaviour, for destinations no rate table covers.

    Keeps a single-country shop working exactly as it did before shipping
    zones existed, rather than quoting zero and shipping at a loss.
    """
    threshold = Decimal(getattr(settings, "FREE_DELIVERY_THRESHOLD", 0) or 0)
    charge = Decimal(getattr(settings, "DELIVERY_CHARGE", 0) or 0)
    if threshold and Decimal(order_value) >= threshold:
        return ZERO
    return charge


def has_any_rates():
    """Whether a rate table has been configured at all."""
    return ShippingRate.objects.filter(is_active=True).exists()
