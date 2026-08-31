"""Price rendering.

Every displayed price passes through here, so conversion and formatting
happen in exactly one place:

    {% load money %}
    {% price product.price %}        ->  $58.00
    {{ product.price|to_money }}     ->  same, as a filter
"""
from django import template

from apps.geo import services

register = template.Library()


def _currency(context):
    current = context.get("LOCALE")
    if current is not None:
        return current
    request = context.get("request")
    return getattr(request, "locale", None)


@register.simple_tag(takes_context=True)
def price(context, amount):
    """Convert a base-currency amount and format it for display."""
    if amount is None:
        return ""
    current = _currency(context)
    if current is None:
        # No locale (a management command rendering a template, say): show the
        # stored amount in the base currency rather than failing.
        return services.display_currency().format(amount)
    return current.display(amount)


@register.simple_tag(takes_context=True)
def price_value(context, amount):
    """The converted number without a symbol, for inputs and data attributes."""
    if amount is None:
        return ""
    current = _currency(context)
    if current is None:
        return amount
    return current.money(amount)


@register.filter(name="to_money")
def to_money(amount, currency=None):
    """Format an amount already expressed in ``currency`` (or the base one)."""
    if amount is None:
        return ""
    target = currency or services.display_currency()
    return target.format(amount)
