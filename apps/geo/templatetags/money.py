"""Price rendering.

Three different things get called "a price", and they must not be rendered
the same way:

    {% price product.price %}                 live catalogue price -- converts
                                              into the visitor's currency
    {{ order.total|money_in:order.currency }} what was actually charged -- shown
                                              in the order's own currency, never
                                              reconverted (spec section 60)
    {% base_price report.gross %}             company accounting -- always the
                                              base currency, whatever the staff
                                              member happens to be browsing in

Getting the second one wrong is the bug the spec warns about directly: "do
not simply convert the invoice later."
"""
from django import template

from apps.geo import services
from apps.geo.models import Currency

register = template.Library()


def _locale(context):
    current = context.get("LOCALE")
    if current is not None:
        return current
    request = context.get("request")
    return getattr(request, "locale", None)


def _format(currency, amount, places=None):
    if places is None:
        return currency.format(amount)
    quantized = currency.quantize(amount)
    return (
        f"{currency.symbol}{quantized:,.{places}f}"
        if currency.symbol_is_prefix
        else f"{quantized:,.{places}f} {currency.symbol}"
    )


@register.simple_tag(takes_context=True)
def price(context, amount, places=None):
    """A live price, converted into the currency the visitor is browsing in."""
    if amount is None or amount == "":
        return ""
    current = _locale(context)
    if current is None:
        # No middleware (a management command rendering a template, say).
        return _format(services.display_currency(), amount, places)
    return _format(current.currency, current.money(amount), places)


@register.simple_tag(takes_context=True)
def price_value(context, amount):
    """The converted number alone, for inputs, data attributes and JSON."""
    if amount is None or amount == "":
        return ""
    current = _locale(context)
    if current is None:
        return amount
    return current.money(amount)


@register.simple_tag
def base_price(amount, places=None):
    """An amount in the base currency, never converted.

    For staff reporting: revenue figures are company accounting and should
    not move because whoever opened the dashboard is browsing in dollars.
    """
    if amount is None or amount == "":
        return ""
    return _format(services.display_currency(), amount, places)


@register.filter(name="money_in")
def money_in(amount, currency_code):
    """Format an amount that is already expressed in ``currency_code``.

    Used for orders, payments and invoices, which store the currency they
    were charged in. No conversion happens -- the number is already right,
    and re-deriving it from today's rate is exactly what section 60 forbids.
    """
    if amount is None or amount == "":
        return ""
    currency = None
    if currency_code:
        code = getattr(currency_code, "code", currency_code)
        currency = Currency.objects.filter(code=str(code).upper()).first()
    return (currency or services.display_currency()).format(amount)


@register.filter(name="to_money")
def to_money(amount, currency=None):
    """Format an amount in ``currency``, defaulting to the base currency."""
    if amount is None or amount == "":
        return ""
    return (currency or services.display_currency()).format(amount)
