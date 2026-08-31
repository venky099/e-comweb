"""Seed shipping zones, methods and rates.

    python manage.py seed_shipping

Follows the spec's own worked examples: the six zones named in section 28,
the three methods in section 30, and the India and USA rate bands in section
29 -- Rs.80 under Rs.1,000 and free above it, $15 under $100 and free above.

Rates are in the base currency, so the dollar figures from the spec are
seeded at their rupee equivalents.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.geo.models import Country
from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone

# name, sort, [country iso2]
ZONES = [
    ("Zone 1 - India", 1, ["IN"]),
    ("Zone 2 - UAE", 2, ["AE"]),
    ("Zone 3 - Singapore / Malaysia", 3, ["SG", "MY"]),
    ("Zone 4 - UK / Europe", 4, ["GB", "DE", "FR"]),
    ("Zone 5 - USA / Canada", 5, ["US", "CA"]),
    ("Zone 6 - Australia", 6, ["AU"]),
]

# name, code, carrier, min days, max days, sort
METHODS = [
    ("Standard Delivery", "standard", "", 7, 12, 1),
    ("Express Delivery", "express", "", 3, 5, 2),
    ("International Priority", "priority", "", 2, 4, 3),
]

# zone name, method code, max weight (g or None), price, free over
# Prices are base currency (INR). The spec's dollar figures are converted at
# the seeded indicative rate.
RATES = [
    # India -- section 29's worked example, exactly.
    ("Zone 1 - India", "standard", 1000, "80.00", "999.00"),
    ("Zone 1 - India", "standard", None, "140.00", "999.00"),
    ("Zone 1 - India", "express", 1000, "180.00", None),
    ("Zone 1 - India", "express", None, "260.00", None),

    ("Zone 2 - UAE", "standard", 1000, "1300.00", "17000.00"),
    ("Zone 2 - UAE", "standard", None, "2100.00", "17000.00"),
    ("Zone 2 - UAE", "priority", None, "3400.00", None),

    ("Zone 3 - Singapore / Malaysia", "standard", 1000, "1200.00", "17000.00"),
    ("Zone 3 - Singapore / Malaysia", "standard", None, "1900.00", "17000.00"),
    ("Zone 3 - Singapore / Malaysia", "priority", None, "3200.00", None),

    ("Zone 4 - UK / Europe", "standard", 1000, "1500.00", "8600.00"),
    ("Zone 4 - UK / Europe", "standard", None, "2400.00", "8600.00"),
    ("Zone 4 - UK / Europe", "express", None, "3900.00", None),

    # USA -- section 29: $15 under $100, free above. $100 is about Rs.8,600.
    ("Zone 5 - USA / Canada", "standard", 1000, "1300.00", "8600.00"),
    ("Zone 5 - USA / Canada", "standard", None, "2200.00", "8600.00"),
    ("Zone 5 - USA / Canada", "express", None, "2200.00", None),

    ("Zone 6 - Australia", "standard", 1000, "1600.00", "8600.00"),
    ("Zone 6 - Australia", "standard", None, "2500.00", "8600.00"),
    ("Zone 6 - Australia", "priority", None, "4000.00", None),
]


class Command(BaseCommand):
    help = "Seed shipping zones, methods and rate bands."

    @transaction.atomic
    def handle(self, *args, **options):
        zones = {}
        for name, sort_order, iso_codes in ZONES:
            zone, _created = ShippingZone.objects.update_or_create(
                name=name, defaults={"sort_order": sort_order, "is_active": True}
            )
            countries = Country.objects.filter(iso2__in=iso_codes)
            zone.countries.set(countries)
            zones[name] = zone

        methods = {}
        for name, code, carrier, min_days, max_days, sort_order in METHODS:
            method, _created = ShippingMethod.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "carrier": carrier,
                    "min_days": min_days,
                    "max_days": max_days,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )
            methods[code] = method

        made = 0
        for zone_name, method_code, max_weight, price, free_over in RATES:
            zone = zones.get(zone_name)
            method = methods.get(method_code)
            if zone is None or method is None:
                continue
            # Bands stack from zero: the first row for a method covers up to
            # its maximum, the next starts where that one ended.
            previous = (
                ShippingRate.objects.filter(zone=zone, method=method)
                .order_by("-min_weight_grams")
                .first()
            )
            lower = 0
            if previous is not None and previous.max_weight_grams:
                lower = previous.max_weight_grams
            _rate, created = ShippingRate.objects.update_or_create(
                zone=zone,
                method=method,
                min_weight_grams=lower,
                defaults={
                    "max_weight_grams": max_weight,
                    "price": Decimal(price),
                    "free_over": Decimal(free_over) if free_over else None,
                    "is_active": True,
                },
            )
            made += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Zones {ShippingZone.objects.count()}, "
                f"methods {ShippingMethod.objects.count()}, "
                f"rates {ShippingRate.objects.count()} ({made} new)."
            )
        )
