"""Load the countries and currencies the spec names.

    python manage.py seed_geo

Section 8 of the MST spec lists ten country/currency pairs. Those are seeded
here with indicative rates so the storefront works out of the box; real rates
come from `python manage.py update_rates` or the admin.

Idempotent: run it as often as you like. Existing rows are updated, never
duplicated, and rates are only seeded for currencies that have none, so a
re-run never overwrites a rate an administrator set by hand.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.geo.models import Country, Currency, ExchangeRate, State

# code, name, symbol, prefix, decimals, indicative units per 1 INR
CURRENCIES = [
    ("INR", "Indian Rupee", "₹", True, 2, None),
    ("USD", "US Dollar", "$", True, 2, "0.01160"),
    ("GBP", "Pound Sterling", "£", True, 2, "0.00900"),
    ("EUR", "Euro", "€", True, 2, "0.01070"),
    ("AED", "UAE Dirham", "د.إ", True, 2, "0.04260"),
    ("SGD", "Singapore Dollar", "S$", True, 2, "0.01560"),
    ("MYR", "Malaysian Ringgit", "RM", True, 2, "0.05200"),
    ("SAR", "Saudi Riyal", "ر.س", True, 2, "0.04350"),
    ("AUD", "Australian Dollar", "A$", True, 2, "0.01780"),
    ("CAD", "Canadian Dollar", "C$", True, 2, "0.01620"),
]

# iso2, iso3, name, currency, dial code
COUNTRIES = [
    ("IN", "IND", "India", "INR", "+91"),
    ("US", "USA", "United States", "USD", "+1"),
    ("GB", "GBR", "United Kingdom", "GBP", "+44"),
    ("DE", "DEU", "Germany", "EUR", "+49"),
    ("FR", "FRA", "France", "EUR", "+33"),
    ("AE", "ARE", "United Arab Emirates", "AED", "+971"),
    ("SG", "SGP", "Singapore", "SGD", "+65"),
    ("MY", "MYS", "Malaysia", "MYR", "+60"),
    ("SA", "SAU", "Saudi Arabia", "SAR", "+966"),
    ("AU", "AUS", "Australia", "AUD", "+61"),
    ("CA", "CAN", "Canada", "CAD", "+1"),
]

# Indian states and union territories matter for GST: within-state sales are
# CGST + SGST, across state lines they are IGST.
INDIAN_STATES = [
    ("Andhra Pradesh", "AP"), ("Assam", "AS"), ("Bihar", "BR"),
    ("Chhattisgarh", "CG"), ("Delhi", "DL"), ("Goa", "GA"),
    ("Gujarat", "GJ"), ("Haryana", "HR"), ("Himachal Pradesh", "HP"),
    ("Jharkhand", "JH"), ("Karnataka", "KA"), ("Kerala", "KL"),
    ("Madhya Pradesh", "MP"), ("Maharashtra", "MH"), ("Odisha", "OD"),
    ("Punjab", "PB"), ("Rajasthan", "RJ"), ("Tamil Nadu", "TN"),
    ("Telangana", "TS"), ("Uttar Pradesh", "UP"), ("Uttarakhand", "UK"),
    ("West Bengal", "WB"),
]

US_STATES = [
    ("California", "CA"), ("New York", "NY"), ("Texas", "TX"),
    ("Florida", "FL"), ("Illinois", "IL"), ("Washington", "WA"),
]


class Command(BaseCommand):
    help = "Seed countries, currencies and indicative exchange rates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-rates",
            action="store_true",
            help="Record fresh indicative rates even where rates already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        currencies = {}
        created_currencies = 0

        for index, (code, name, symbol, prefix, places, _rate) in enumerate(CURRENCIES):
            currency, created = Currency.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "symbol": symbol,
                    "symbol_is_prefix": prefix,
                    "decimal_places": places,
                    "is_base": code == "INR",
                    "is_active": True,
                    "sort_order": index,
                },
            )
            currencies[code] = currency
            created_currencies += int(created)

        base = currencies["INR"]

        rates_added = 0
        for code, _n, _s, _p, _d, rate in CURRENCIES:
            if rate is None:
                continue
            quote = currencies[code]
            exists = ExchangeRate.objects.filter(base=base, quote=quote).exists()
            if exists and not options["reset_rates"]:
                continue
            ExchangeRate.objects.create(
                base=base,
                quote=quote,
                rate=Decimal(rate),
                source=ExchangeRate.Source.MANUAL,
                effective_from=now,
                note="Indicative seed rate -- replace with a live feed.",
            )
            rates_added += 1

        created_countries = 0
        for index, (iso2, iso3, name, code, dial) in enumerate(COUNTRIES):
            _country, created = Country.objects.update_or_create(
                iso2=iso2,
                defaults={
                    "iso3": iso3,
                    "name": name,
                    "currency": currencies[code],
                    "dial_code": dial,
                    "is_active": True,
                    "shipping_enabled": True,
                    "sort_order": index,
                },
            )
            created_countries += int(created)

        states_added = 0
        for iso2, rows in (("IN", INDIAN_STATES), ("US", US_STATES)):
            country = Country.objects.get(iso2=iso2)
            for name, code in rows:
                _state, created = State.objects.update_or_create(
                    country=country, name=name, defaults={"code": code}
                )
                states_added += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Currencies {Currency.objects.count()} ({created_currencies} new), "
                f"countries {Country.objects.count()} ({created_countries} new), "
                f"states {State.objects.count()} ({states_added} new), "
                f"rates recorded {rates_added}."
            )
        )
        self.stdout.write(f"  Base currency: {base.code} -- product prices are stored in it.")
