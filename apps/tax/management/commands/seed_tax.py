"""Seed tax rules for the countries the storefront serves.

    python manage.py seed_tax

Nothing here is special-cased in code: India's CGST/SGST/IGST behaviour is
three ordinary rows that differ only in ``applies_when``, and every other
country is a single row. That is the point of section 27 -- "do not hard-code
Indian GST into the entire system".

Rates are indicative starting points. Confirm them with a tax advisor before
selling anywhere; the spec says as much for international sales.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.geo.models import Country
from apps.tax.models import TaxRule

# iso2, name, percent, applies_when
RULES = [
    ("IN", "CGST", "9.000", TaxRule.AppliesWhen.INTRA_STATE),
    ("IN", "SGST", "9.000", TaxRule.AppliesWhen.INTRA_STATE),
    ("IN", "IGST", "18.000", TaxRule.AppliesWhen.INTER_STATE),
    ("GB", "VAT", "20.000", TaxRule.AppliesWhen.ANY),
    ("DE", "VAT", "19.000", TaxRule.AppliesWhen.ANY),
    ("FR", "VAT", "20.000", TaxRule.AppliesWhen.ANY),
    ("AE", "VAT", "5.000", TaxRule.AppliesWhen.ANY),
    ("SG", "GST", "9.000", TaxRule.AppliesWhen.ANY),
    ("MY", "SST", "10.000", TaxRule.AppliesWhen.ANY),
    ("SA", "VAT", "15.000", TaxRule.AppliesWhen.ANY),
    ("AU", "GST", "10.000", TaxRule.AppliesWhen.ANY),
    ("CA", "GST", "5.000", TaxRule.AppliesWhen.ANY),
    # The United States charges sales tax per state, not nationally, so no
    # country-wide rule is seeded. Add per-state rules where you have nexus.
]


class Command(BaseCommand):
    help = "Seed indicative tax rules for the seeded countries."

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.now().date()
        created = updated = skipped = 0

        for iso2, name, percent, applies_when in RULES:
            country = Country.objects.filter(iso2=iso2).first()
            if country is None:
                skipped += 1
                continue
            _rule, was_created = TaxRule.objects.update_or_create(
                country=country,
                name=name,
                state=None,
                category=None,
                applies_when=applies_when,
                defaults={
                    "percent": Decimal(percent),
                    "effective_from": today,
                    "is_active": True,
                    "note": "Indicative seed rate -- confirm with a tax advisor.",
                },
            )
            created += int(was_created)
            updated += int(not was_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Tax rules: {created} created, {updated} updated, {skipped} skipped "
                f"(country not seeded)."
            )
        )
        self.stdout.write(
            "  India splits by destination against TAX_ORIGIN_STATE; "
            "the United States has none seeded -- add per-state rules where you have nexus."
        )
