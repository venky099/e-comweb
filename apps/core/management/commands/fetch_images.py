"""Replace generated placeholder tiles with real photography.

    python manage.py fetch_images                 # products missing real photos
    python manage.py fetch_images --all           # re-fetch everything
    python manage.py fetch_images --only banners

Images come from Lorem Picsum (https://picsum.photos), which serves
Unsplash-licensed photography and needs no API key. Each product is fetched
with a stable seed derived from its SKU, so the same product always gets the
same picture and re-running is idempotent.

This is demo dressing. A real catalog uploads its own photography through the
admin -- nothing in the app depends on this command.
"""
import hashlib
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Brand, Category, Product, ProductImage
from apps.marketing.models import Banner

SOURCE = "https://picsum.photos/seed/{seed}/{width}/{height}"
USER_AGENT = "Mozilla/5.0 (compatible; lumen-store-seed/1.0)"
TIMEOUT = 25


def _seed(*parts):
    """Stable slug so a given subject always resolves to the same photo."""
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _download(seed, width, height, retries=3):
    """Fetch one JPEG. Returns bytes, or None if the source will not cooperate."""
    url = SOURCE.format(seed=seed, width=width, height=height)
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=TIMEOUT) as response:
                data = response.read()
            if data[:2] == b"\xff\xd8":  # JPEG magic number
                return data
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        time.sleep(1.5 * (attempt + 1))  # back off before trying again
    return None


class Command(BaseCommand):
    help = "Download real photography for products, categories, brands and banners."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-fetch even where an image already exists.",
        )
        parser.add_argument(
            "--only",
            choices=["products", "categories", "brands", "banners"],
            help="Restrict to one kind of image.",
        )
        parser.add_argument(
            "--per-product",
            type=int,
            default=3,
            help="Gallery images per product (default 3).",
        )

    def handle(self, *args, **options):
        self.force = options["all"]
        self.stdout.write(self.style.MIGRATE_HEADING("Fetching photography"))

        # Fail early and clearly rather than half-way through the catalog.
        if _download(_seed("connectivity-probe"), 80, 80) is None:
            raise CommandError(
                "Could not reach picsum.photos. Check your connection, or add "
                "images through the admin instead."
            )

        only = options["only"]
        if only in (None, "categories"):
            self._categories()
        if only in (None, "brands"):
            self._brands()
        if only in (None, "banners"):
            self._banners()
        if only in (None, "products"):
            self._products(options["per_product"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Done. Hard-refresh the storefront to see them."))

    # ------------------------------------------------------------------
    def _save(self, field_owner, field_name, filename, data):
        """Attach downloaded bytes, removing whatever file it replaces."""
        field = getattr(field_owner, field_name)
        previous = field.name or None
        field.save(filename, ContentFile(data), save=True)
        if previous and previous != field.name:
            try:
                field.storage.delete(previous)
            except Exception:
                pass  # a missing old file is not worth failing over

    def _categories(self):
        queryset = Category.objects.all()
        if not self.force:
            queryset = queryset.filter(image="")
        done = 0
        for category in queryset:
            data = _download(_seed("category", category.slug), 600, 600)
            if data:
                self._save(category, "image", f"{category.slug}.jpg", data)
                done += 1
        self.stdout.write(f"  categories: {done}")

    def _brands(self):
        queryset = Brand.objects.all()
        if not self.force:
            queryset = queryset.filter(logo="")
        done = 0
        for brand in queryset:
            data = _download(_seed("brand", brand.slug), 400, 400)
            if data:
                self._save(brand, "logo", f"{brand.slug}.jpg", data)
                done += 1
        self.stdout.write(f"  brands: {done}")

    def _banners(self):
        done = 0
        for banner in Banner.objects.all():
            if banner.image and not self.force:
                continue
            # Wide crop; the headline is HTML rendered over this, so the photo
            # must stay clean and text-free.
            data = _download(_seed("banner", banner.title), 1600, 600)
            if data:
                self._save(banner, "image", f"banner-{banner.pk}.jpg", data)
                done += 1
        self.stdout.write(f"  banners: {done}")

    def _products(self, per_product):
        products = Product.objects.all().prefetch_related("images")
        total, failures = 0, 0

        for product in products:
            existing = list(product.images.all())
            if existing and not self.force:
                continue

            images = []
            for index in range(per_product):
                data = _download(_seed("product", product.sku, index), 900, 900)
                if data is None:
                    failures += 1
                    continue
                images.append(data)

            if not images:
                continue

            with transaction.atomic():
                for old in existing:
                    try:
                        old.image.storage.delete(old.image.name)
                    except Exception:
                        pass
                    old.delete()

                for index, data in enumerate(images):
                    ProductImage.objects.create(
                        product=product,
                        image=ContentFile(data, name=f"{product.slug}-{index}.jpg"),
                        alt_text=f"{product.name} - view {index + 1}",
                        is_primary=index == 0,
                        sort_order=index,
                    )
            total += 1
            self.stdout.write(f"    {product.name[:44]:<46} {len(images)} image(s)")

        self.stdout.write(f"  products: {total}")
        if failures:
            self.stdout.write(
                self.style.WARNING(f"  {failures} image(s) failed; re-run to fill the gaps.")
            )
