"""Working out what tax an order owes.

One entry point, ``compute()``, used by checkout and by the cart summary so
the figure a customer sees before paying is the figure they are charged.

Rounding discipline, same as currency conversion: accumulate each tax at full
precision across the whole order, then round once per tax name. Rounding each
line and summing produces a total that disagrees with the sum of its own
lines, which on an invoice looks like an arithmetic error.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.tax.models import TaxRule

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class TaxLine:
    """One named tax and what it came to."""

    __slots__ = ("name", "percent", "amount", "rule")

    def __init__(self, name, percent, amount, rule=None):
        self.name = name
        self.percent = percent
        self.amount = amount
        self.rule = rule

    def __repr__(self):
        return f"<TaxLine {self.name} {self.percent}% = {self.amount}>"


class TaxResult:
    """The tax on an order: named lines, and their total."""

    __slots__ = ("lines", "total")

    def __init__(self, lines, total):
        self.lines = lines
        self.total = total

    def __bool__(self):
        return self.total > ZERO

    @property
    def names(self):
        return [line.name for line in self.lines]


EMPTY = TaxResult([], ZERO)


def origin_state_name():
    """The state the seller ships from, for India's intra/inter GST split."""
    return getattr(settings, "TAX_ORIGIN_STATE", "") or ""


def prices_include_tax():
    """Section 59: tax-inclusive or tax-exclusive pricing, site-wide."""
    return bool(getattr(settings, "PRICES_INCLUDE_TAX", False))


def applicable_rules(country, state=None, on_date=None):
    """Every active rule for a destination, most specific first."""
    if country is None:
        return []

    today = on_date or timezone.now().date()
    # A current rule has no end date; a superseded one ended in the past.
    still_current = Q(effective_to__isnull=True) | Q(effective_to__gte=today)
    rules = TaxRule.objects.filter(
        country=country, is_active=True, effective_from__lte=today
    ).filter(still_current)

    origin = origin_state_name()
    state_name = getattr(state, "name", state) or ""
    # Without a destination state we cannot tell intra from inter, so only
    # unconditional rules apply. Guessing would mean charging the wrong tax.
    is_intra = bool(origin) and bool(state_name) and origin == state_name
    is_inter = bool(origin) and bool(state_name) and origin != state_name

    keep = []
    for rule in rules.select_related("state", "category", "country"):
        if rule.state_id and getattr(rule.state, "name", None) != state_name:
            continue
        if rule.applies_when == TaxRule.AppliesWhen.INTRA_STATE and not is_intra:
            continue
        if rule.applies_when == TaxRule.AppliesWhen.INTER_STATE and not is_inter:
            continue
        keep.append(rule)

    keep.sort(key=lambda r: r.specificity, reverse=True)
    return keep


def rules_for_category(rules, category):
    """The rules that apply to one product's category.

    Category-specific rules win outright: if a category has its own rate,
    the country-wide default must not also be charged on top of it.
    """
    if not rules:
        return []
    category_ids = set()
    node = category
    while node is not None:
        category_ids.add(node.pk)
        node = getattr(node, "parent", None)

    specific = [r for r in rules if r.category_id in category_ids]
    if specific:
        return specific
    return [r for r in rules if r.category_id is None]


def compute(items, country, state=None, on_date=None):
    """Tax for ``items`` delivered to ``country``/``state``.

    ``items`` is any iterable of objects exposing ``line_total`` and a
    ``variant.product`` -- cart items and order items both qualify.

    Falls back to the product's own ``tax_rate_percent`` when no rule covers
    the destination, so a catalogue that predates the rules engine keeps
    behaving as it did.
    """
    items = list(items)
    if not items:
        return EMPTY

    rules = applicable_rules(country, state, on_date)
    totals = {}   # name -> [percent, running amount, rule]

    for item in items:
        product = item.variant.product
        matched = rules_for_category(rules, product.category)

        if not matched:
            percent = Decimal(getattr(product, "tax_rate_percent", 0) or 0)
            if percent <= 0:
                continue
            bucket = totals.setdefault("Tax", [percent, Decimal("0"), None])
            bucket[1] += Decimal(item.line_total) * percent / Decimal("100")
            continue

        for rule in matched:
            bucket = totals.setdefault(rule.name, [rule.percent, Decimal("0"), rule])
            bucket[1] += Decimal(item.line_total) * rule.percent / Decimal("100")

    lines = [
        TaxLine(name=name, percent=percent, amount=money(amount), rule=rule)
        for name, (percent, amount, rule) in sorted(totals.items())
        if money(amount) > ZERO
    ]
    return TaxResult(lines, money(sum((line.amount for line in lines), ZERO)))


def save_lines(order, result):
    """Persist a computed breakdown against an order."""
    from apps.tax.models import OrderTaxLine

    OrderTaxLine.objects.filter(order=order).delete()
    OrderTaxLine.objects.bulk_create(
        [
            OrderTaxLine(
                order=order,
                name=line.name,
                percent=line.percent,
                amount=line.amount,
                rule=line.rule,
            )
            for line in result.lines
        ]
    )
