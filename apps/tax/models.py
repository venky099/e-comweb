"""Configurable tax rules.

MST section 27 is explicit: "Do not hard-code Indian GST into the entire
system." So nothing here knows what GST is. India's behaviour -- CGST + SGST
within a state, IGST across state lines -- falls out of three ordinary rows
whose ``applies_when`` differs, and any other country is configured the same
way.

Admins configure country, state, tax name, percentage, product category and
effective date, which is exactly the list the spec asks for.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class TaxRule(TimeStampedModel):
    """One named tax, applying to a place, a category and a period.

    Resolution picks the most specific match: a rule naming a state beats one
    naming only the country, and a rule naming a category beats one that
    applies to everything.
    """

    class AppliesWhen(models.TextChoices):
        ANY = "any", _("Always")
        INTRA_STATE = "intra", _("Only within the seller's state")
        INTER_STATE = "inter", _("Only outside the seller's state")

    name = models.CharField(
        max_length=32, help_text=_("Shown on the invoice, e.g. CGST, VAT, GST.")
    )
    country = models.ForeignKey(
        "geo.Country", on_delete=models.CASCADE, related_name="tax_rules"
    )
    state = models.ForeignKey(
        "geo.State",
        on_delete=models.CASCADE,
        related_name="tax_rules",
        blank=True,
        null=True,
        help_text=_("Leave empty to apply to the whole country."),
    )
    category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.CASCADE,
        related_name="tax_rules",
        blank=True,
        null=True,
        help_text=_("Leave empty to apply to every product."),
    )
    percent = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    applies_when = models.CharField(
        max_length=8,
        choices=AppliesWhen.choices,
        default=AppliesWhen.ANY,
        help_text=_(
            "India splits GST by destination: CGST and SGST within the "
            "seller's state, IGST outside it."
        ),
    )
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(
        blank=True, null=True, help_text=_("Leave empty while the rule is current.")
    )
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["country__name", "name"]
        indexes = [
            models.Index(fields=["country", "is_active", "effective_from"]),
        ]

    def __str__(self):
        where = self.state.name if self.state_id else self.country.name
        return f"{self.name} {self.percent}% ({where})"

    @property
    def specificity(self):
        """How closely this rule targets a sale. Higher wins.

        A rule naming both a state and a category is the most specific thing
        an administrator can express, so it must beat a country-wide default.
        """
        return (2 if self.state_id else 0) + (1 if self.category_id else 0)


class OrderTaxLine(TimeStampedModel):
    """What tax an order actually paid, broken down by name.

    Stored rather than recomputed. Rules change, rates change, and an invoice
    must still show what was charged on the day -- the same reason an order
    stores its exchange rate.
    """

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="tax_lines"
    )
    name = models.CharField(max_length=32)
    percent = models.DecimalField(max_digits=6, decimal_places=3)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text=_("In the order's base currency.")
    )
    rule = models.ForeignKey(
        TaxRule,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        help_text=_("The rule this came from, if it still exists."),
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} {self.percent}% = {self.amount}"
