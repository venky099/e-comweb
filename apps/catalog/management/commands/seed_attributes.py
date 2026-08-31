"""Seed the fashion attributes section 12 asks shoppers to filter by.

    python manage.py seed_attributes

Fabric, pattern, occasion, gender, work type and sleeve length, plus a size
guide per garment family. Nothing here is privileged -- an administrator can
add, rename or remove any of it without touching code, which is the point of
storing attributes as data.

Idempotent: re-running updates the definitions and leaves any values an
administrator has added alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

import random

from apps.catalog.models import (
    Attribute,
    AttributeValue,
    Category,
    Product,
    ProductAttribute,
    SizeGuide,
)

ATTRIBUTES = [
    ("Fabric", "fabric", 1, [
        "Silk", "Cotton", "Georgette", "Chiffon", "Linen", "Rayon",
        "Velvet", "Organza", "Denim", "Wool", "Satin", "Crepe",
    ]),
    ("Pattern", "pattern", 2, [
        "Solid", "Printed", "Embroidered", "Floral", "Striped",
        "Checked", "Zari Work", "Colour Block",
    ]),
    ("Occasion", "occasion", 3, [
        "Casual", "Formal", "Wedding", "Festive", "Party", "Office", "Beach",
    ]),
    ("Gender", "gender", 4, ["Women", "Men", "Unisex", "Girls", "Boys"]),
    ("Work", "work", 5, [
        "Handwork", "Machine Work", "Mirror Work", "Sequin", "Stone Work", "Plain",
    ]),
    ("Sleeve", "sleeve", 6, [
        "Sleeveless", "Short Sleeve", "Three Quarter", "Full Sleeve",
    ]),
]

# name, unit, columns, rows
SIZE_GUIDES = [
    (
        "Women's apparel",
        "cm",
        ["Size", "Bust", "Waist", "Hip", "Length"],
        [
            ["XS", "81", "63", "86", "96"],
            ["S", "86", "68", "91", "97"],
            ["M", "91", "73", "96", "98"],
            ["L", "97", "79", "102", "99"],
            ["XL", "102", "84", "107", "100"],
            ["XXL", "107", "89", "112", "101"],
            ["XXXL", "112", "94", "117", "102"],
        ],
    ),
    (
        "Men's apparel",
        "cm",
        ["Size", "Chest", "Waist", "Shoulder", "Length"],
        [
            ["XS", "86", "71", "41", "68"],
            ["S", "91", "76", "43", "70"],
            ["M", "97", "81", "44", "72"],
            ["L", "102", "86", "46", "74"],
            ["XL", "107", "91", "48", "76"],
            ["XXL", "112", "97", "49", "78"],
            ["XXXL", "117", "102", "51", "80"],
        ],
    ),
]


class Command(BaseCommand):
    help = "Seed fashion attributes, their values and size guides."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-assign",
            action="store_true",
            help="Define the attributes without tagging any products.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created_attrs = created_values = 0

        for name, code, order, values in ATTRIBUTES:
            attribute, made = Attribute.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "kind": Attribute.Kind.CHOICE,
                    "is_filterable": True,
                    "show_on_product": True,
                    "sort_order": order,
                    "is_active": True,
                },
            )
            created_attrs += int(made)

            for index, value in enumerate(values):
                _row, value_made = AttributeValue.objects.update_or_create(
                    attribute=attribute,
                    slug=slugify(value)[:64],
                    defaults={"value": value, "sort_order": index},
                )
                created_values += int(value_made)

        created_guides = 0
        for name, unit, columns, rows in SIZE_GUIDES:
            _guide, made = SizeGuide.objects.update_or_create(
                name=name,
                defaults={
                    "unit": unit,
                    "columns": columns,
                    "rows": rows,
                    "is_active": True,
                    "note": "Measurements are body measurements, not garment measurements.",
                },
            )
            created_guides += int(made)

        tagged = 0
        if not options["no_assign"]:
            tagged = self.assign_to_products()

        self.stdout.write(
            self.style.SUCCESS(
                f"Attributes {Attribute.objects.count()} ({created_attrs} new), "
                f"values {AttributeValue.objects.count()} ({created_values} new), "
                f"size guides {SizeGuide.objects.count()} ({created_guides} new), "
                f"products tagged {tagged}."
            )
        )
        if not Category.objects.exists():
            self.stdout.write(
                "  No categories yet -- run seed_data to populate the catalogue."
            )

    def assign_to_products(self):
        """Give every product a plausible set of attribute values.

        Seeded from the product's own name so a re-run produces the same
        tagging -- a demo catalogue that reshuffles itself on every seed makes
        screenshots and bug reports impossible to compare.
        """
        attributes = {a.code: a for a in Attribute.objects.prefetch_related("values")}
        if not attributes:
            return 0

        tagged = 0
        for product in Product.objects.all().iterator():
            rng = random.Random(product.name)
            for code, attribute in attributes.items():
                values = list(attribute.values.all())
                if not values:
                    continue
                choice = rng.choice(values)
                _row, made = ProductAttribute.objects.get_or_create(
                    product=product, attribute=attribute, value=choice
                )
                tagged += int(made)
        return tagged
