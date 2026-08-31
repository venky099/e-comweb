"""Bulk product upload (MST spec section 38).

A catalogue import is the one operation where a silent partial success is
worst: half a season loaded, nobody sure which half. So this always reports
every row, and defaults to a dry run.

    report = preview(file)        parse and validate, write nothing
    report = commit(file)         write, inside one transaction

Both return the same ``ImportReport``, so the confirmation screen shows
exactly what the real run will do. A file with any invalid row is refused
outright rather than imported partially -- fixing a spreadsheet and
re-uploading is easy; working out which of 400 rows landed is not.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.catalog.models import (
    Attribute,
    AttributeValue,
    Brand,
    Category,
    Product,
    ProductAttribute,
    ProductVariant,
)
from apps.inventory.models import Inventory

REQUIRED_COLUMNS = ["sku", "name", "category", "price"]

OPTIONAL_COLUMNS = [
    "brand",
    "short_description",
    "description",
    "compare_at_price",
    "size",
    "color",
    "color_hex",
    "stock",
    "weight_grams",
    "barcode",
    "tags",
    "is_active",
    "attributes",
]

ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

TRUTHY = {"1", "true", "yes", "y", "active"}


class RowError(Exception):
    """A problem with one row, reported rather than raised out."""


class ImportReport:
    """What an import did, or would do."""

    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.errors = []          # (row number, message)
        self.created_products = 0
        self.updated_products = 0
        self.created_variants = 0
        self.updated_variants = 0
        self.rows_read = 0
        self.seen_products = set()

    @property
    def ok(self):
        return not self.errors

    @property
    def total_written(self):
        return (
            self.created_products
            + self.updated_products
            + self.created_variants
            + self.updated_variants
        )

    def add_error(self, row_number, message):
        self.errors.append((row_number, str(message)))

    def summary(self):
        if self.errors:
            return _("%(count)d row(s) could not be imported.") % {
                "count": len(self.errors)
            }
        verb = _("would create") if self.dry_run else _("created")
        return _(
            "%(rows)d rows read. %(verb)s %(np)d products and %(nv)d variants; "
            "updated %(up)d products and %(uv)d variants."
        ) % {
            "rows": self.rows_read,
            "verb": verb,
            "np": self.created_products,
            "nv": self.created_variants,
            "up": self.updated_products,
            "uv": self.updated_variants,
        }


def _decimal(value, field, allow_blank=False):
    text = (value or "").strip()
    if not text:
        if allow_blank:
            return None
        raise RowError(_("%(field)s is required.") % {"field": field})
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise RowError(
            _("%(field)s is not a number: %(value)s") % {"field": field, "value": text}
        )


def _int(value, field, default=0):
    text = (value or "").strip()
    if not text:
        return default
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        raise RowError(
            _("%(field)s is not a whole number: %(value)s")
            % {"field": field, "value": text}
        )


def read_rows(uploaded):
    """Decode an uploaded CSV into dicts, with its header normalised."""
    raw = uploaded.read() if hasattr(uploaded, "read") else uploaded
    if isinstance(raw, bytes):
        # Spreadsheets exported from Excel are frequently UTF-8 with a BOM,
        # which would otherwise make the first column name unmatchable.
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise RowError(_("The file's text encoding could not be read."))
    else:
        text = raw

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise RowError(_("The file has no header row."))

    reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise RowError(
            _("Missing required column(s): %(names)s") % {"names": ", ".join(missing)}
        )
    return list(reader)


def parse_attributes(text):
    """``fabric=Silk;occasion=Festive`` into ``[(code, value), ...]``."""
    pairs = []
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise RowError(
                _("Attributes must look like fabric=Silk;occasion=Festive.")
            )
        code, value = chunk.split("=", 1)
        code, value = code.strip().lower(), value.strip()
        if code and value:
            pairs.append((code, value))
    return pairs


def _apply_row(row, report, write):
    """Validate one row, and write it when ``write`` is true."""
    sku = (row.get("sku") or "").strip()
    name = (row.get("name") or "").strip()
    if not sku:
        raise RowError(_("sku is required."))
    if not name:
        raise RowError(_("name is required."))

    price = _decimal(row.get("price"), "price")
    if price < 0:
        raise RowError(_("price cannot be negative."))
    compare_at = _decimal(row.get("compare_at_price"), "compare_at_price", allow_blank=True)
    if compare_at is not None and compare_at < price:
        raise RowError(_("compare_at_price is below price, so the discount would be negative."))

    category_name = (row.get("category") or "").strip()
    if not category_name:
        raise RowError(_("category is required."))

    stock = _int(row.get("stock"), "stock", default=0)
    weight = _int(row.get("weight_grams"), "weight_grams", default=0)
    attributes = parse_attributes(row.get("attributes"))

    # Validate attribute references before writing anything: a typo in a
    # fabric name should fail the row, not silently drop the tag.
    resolved = []
    for code, value in attributes:
        attribute = Attribute.objects.filter(code=code).first()
        if attribute is None:
            raise RowError(_("Unknown attribute '%(code)s'.") % {"code": code})
        option = AttributeValue.objects.filter(
            attribute=attribute, slug=slugify(value)[:64]
        ).first()
        if option is None:
            raise RowError(
                _("'%(value)s' is not a value of %(name)s.")
                % {"value": value, "name": attribute.name}
            )
        resolved.append((attribute, option))

    if not write:
        # Count what would happen without touching anything. Counting is done
        # against a set of SKUs already seen in this file so that the second
        # size of a new product is not reported as a second new product.
        if sku not in report.seen_products:
            report.seen_products.add(sku)
            if Product.objects.filter(sku=sku).exists():
                report.updated_products += 1
            else:
                report.created_products += 1

        existing = ProductVariant.objects.filter(
            product__sku=sku,
            size=(row.get("size") or "").strip(),
            color=(row.get("color") or "").strip(),
        ).exists()
        if existing:
            report.updated_variants += 1
        else:
            report.created_variants += 1
        return

    category, _made = Category.objects.get_or_create(
        slug=slugify(category_name)[:50], defaults={"name": category_name}
    )
    brand = None
    brand_name = (row.get("brand") or "").strip()
    if brand_name:
        brand, _made = Brand.objects.get_or_create(
            slug=slugify(brand_name)[:50], defaults={"name": brand_name}
        )

    active_text = (row.get("is_active") or "true").strip().lower()
    product, created = Product.objects.update_or_create(
        sku=sku,
        defaults={
            "name": name,
            "category": category,
            "brand": brand,
            "short_description": (row.get("short_description") or "").strip(),
            "description": (row.get("description") or "").strip(),
            "price": price,
            "compare_at_price": compare_at,
            "weight_grams": weight,
            "tags": (row.get("tags") or "").strip(),
            "is_active": active_text in TRUTHY,
        },
    )
    # Count a product once per file, not once per variant row: two size rows
    # of one new product are one creation, not a creation and an update. The
    # dry run counts the same way, so the preview matches what happens.
    if sku not in report.seen_products:
        report.seen_products.add(sku)
        report.created_products += int(created)
        report.updated_products += int(not created)

    for attribute, option in resolved:
        ProductAttribute.objects.get_or_create(
            product=product, attribute=attribute, value=option
        )

    size = (row.get("size") or "").strip()
    color = (row.get("color") or "").strip()
    variant, variant_created = ProductVariant.objects.update_or_create(
        product=product,
        size=size,
        color=color,
        defaults={
            "sku": f"{sku}-{slugify(size + color) or 'default'}"[:64],
            "color_hex": (row.get("color_hex") or "").strip(),
            "barcode": (row.get("barcode") or "").strip(),
            "weight_grams": weight or None,
            "is_active": True,
        },
    )
    report.created_variants += int(variant_created)
    report.updated_variants += int(not variant_created)

    inventory = Inventory.objects.filter(variant=variant).first()
    if inventory is not None and stock:
        inventory.quantity_available = stock
        inventory.save(update_fields=["quantity_available", "updated_at"])


def _run(uploaded, write):
    report = ImportReport(dry_run=not write)
    try:
        rows = read_rows(uploaded)
    except RowError as exc:
        report.add_error(0, exc)
        return report

    report.rows_read = len(rows)
    if not rows:
        report.add_error(0, _("The file has a header but no rows."))
        return report

    # One row per *variant*: a product's SKU repeats once per size and
    # colour, which is the normal shape of a clothing catalogue. What must
    # not repeat is the same size and colour of the same product.
    seen_variants = set()
    for index, row in enumerate(rows, start=2):  # row 1 is the header
        key = (
            (row.get("sku") or "").strip(),
            (row.get("size") or "").strip().lower(),
            (row.get("color") or "").strip().lower(),
        )
        if key[0] and key in seen_variants:
            report.add_error(
                index,
                _("This file already has %(sku)s in size '%(size)s' and colour '%(color)s'.")
                % {"sku": key[0], "size": key[1], "color": key[2]},
            )
            continue
        seen_variants.add(key)
        try:
            _apply_row(row, report, write)
        except RowError as exc:
            report.add_error(index, exc)
        except Exception as exc:  # a bad row must not abort the whole report
            report.add_error(index, f"{type(exc).__name__}: {exc}")

    return report


def preview(uploaded):
    """Validate a file and report what importing it would do."""
    return _run(uploaded, write=False)


def commit(uploaded):
    """Import a file, all or nothing.

    Wrapped in a transaction and rolled back if any row failed, so a
    spreadsheet with one bad line leaves the catalogue exactly as it was.
    """
    with transaction.atomic():
        report = _run(uploaded, write=True)
        if not report.ok:
            transaction.set_rollback(True)
    return report


def template_csv():
    """A header row plus one filled example, for the download link."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(ALL_COLUMNS)
    writer.writerow(
        [
            "MS-SAREE-1001",
            "Designer Silk Saree",
            "Sarees",
            "5000.00",
            "Meridian",
            "Handwoven silk saree with zari border",
            "A full description of the product.",
            "6000.00",
            "Free Size",
            "Red",
            "#b91c1c",
            "25",
            "700",
            "8901234567890",
            "saree,silk,festive",
            "true",
            "fabric=Silk;occasion=Festive;gender=Women",
        ]
    )
    return buffer.getvalue()
