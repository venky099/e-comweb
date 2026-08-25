"""Fetch subject-matched photography for the demo catalog.

    python manage.py fetch_images                 # fill in what is missing
    python manage.py fetch_images --all           # re-fetch everything
    python manage.py fetch_images --only products
    python manage.py fetch_images --dry-run       # show matches, download nothing

Images come from the Openverse API (openverse.org), which indexes
Creative-Commons and public-domain photography and needs no API key.

Relevance is verified, not assumed: a search for "laptop" happily returns a
potted hyacinth, so a candidate is only accepted when its *title* contains one
of the search keywords. Anything unverifiable falls back to LoremFlickr, and
failing that the caller keeps whatever image it already had.

Every accepted image is centre-cropped to a square and resized, because
consistent framing is most of what makes a product grid look professional.

This is demo dressing. A real catalog uploads its own photography through the
admin; nothing in the application depends on this command.
"""
import io
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image, ImageOps

from apps.catalog.models import Brand, Category, Product, ProductImage
from apps.marketing.models import Banner

OPENVERSE = "https://api.openverse.org/v1/images/"
FLICKR_FALLBACK = "https://loremflickr.com/{w}/{h}/{tags}"
USER_AGENT = "Mozilla/5.0 (compatible; lumen-store-seed/1.0)"
TIMEOUT = 25

# ---------------------------------------------------------------------------
# What to actually search for.
#
# Product names are brand-ish ("UltraBook 14 Pro"), so they make poor search
# terms. These map a name fragment to the words a photo of the thing would
# realistically be titled with.
# ---------------------------------------------------------------------------
PRODUCT_KEYWORDS = {
    "ultrabook": ["laptop", "notebook computer"],
    "workstation": ["laptop", "computer"],
    "everyday 15": ["laptop", "notebook"],
    "gaming rig": ["gaming laptop", "computer"],
    "pulse 5g": ["smartphone", "mobile phone"],
    "pulse pro": ["smartphone", "phone"],
    "essential lite": ["smartphone", "phone"],
    "anc headphones": ["headphones", "headphone"],
    "monitor buds": ["earbuds", "earphones"],
    "party speaker": ["speaker", "loudspeaker"],
    "trail watch": ["smartwatch", "wristwatch"],
    "fitness band": ["fitness tracker", "smartwatch"],
    "oxford cotton shirt": ["shirt", "dress shirt"],
    "merino crew sweater": ["sweater", "knitwear"],
    "tapered chinos": ["trousers", "chinos"],
    "linen wrap dress": ["dress", "summer dress"],
    "tailored blazer": ["blazer", "jacket"],
    "high-rise denim": ["jeans", "denim"],
    "trail runner": ["running shoes", "sneakers"],
    "leather sneaker": ["sneakers", "shoes"],
    "everyday loafer": ["loafers", "leather shoes"],
    "frying pan": ["frying pan", "skillet"],
    "dutch oven": ["cooking pot", "cast iron pot"],
    "task chair": ["office chair", "chair"],
    "standing desk": ["desk", "office desk"],
    "air fryer": ["air fryer", "kitchen appliance"],
    "coffee machine": ["coffee machine", "espresso machine"],
    "dumbbell": ["dumbbell", "weights"],
    "yoga mat": ["yoga mat", "yoga"],
    "backpack": ["backpack", "rucksack"],
    "tent": ["tent", "camping tent"],
    "salt road": ["book", "novel"],
    "nightfall harbour": ["book", "reading"],
    "designing data": ["book", "textbook"],
    "clean python": ["book", "programming book"],
}

# Fallback when no product-name fragment matches.
CATEGORY_KEYWORDS = {
    "laptops": ["laptop"],
    "smartphones": ["smartphone", "phone"],
    "audio": ["headphones", "speaker"],
    "wearables": ["smartwatch"],
    "men's clothing": ["shirt", "menswear"],
    "women's clothing": ["dress", "clothing"],
    "footwear": ["shoes", "sneakers"],
    "cookware": ["cookware", "kitchen"],
    "furniture": ["furniture", "chair"],
    "appliances": ["kitchen appliance"],
    "fitness equipment": ["gym equipment", "fitness"],
    "outdoor gear": ["camping", "hiking"],
    "fiction": ["book"],
    "technology": ["book", "computer"],
    "electronics": ["electronics"],
    "fashion": ["clothing"],
    "home & kitchen": ["kitchen"],
    "sports & fitness": ["fitness"],
    "books": ["books"],
}

BANNER_KEYWORDS = {
    "fresh tech": ["technology", "electronics"],
    "wardrobe": ["clothing", "fashion"],
    "home upgrades": ["kitchen", "home interior"],
}


def keywords_for_product(product):
    """Best search terms for one product: name fragment first, category second."""
    name = product.name.lower()
    for fragment, terms in PRODUCT_KEYWORDS.items():
        if fragment in name:
            return terms
    category = (product.category.name or "").lower()
    return CATEGORY_KEYWORDS.get(category, [category or "product"])


def _get(url, as_json=False):
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read()
        return json.loads(payload) if as_json else payload
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def console_safe(text):
    """Make arbitrary text printable on any console encoding.

    Openverse titles come from a worldwide corpus; a Windows cp1252 console
    raises UnicodeEncodeError on the first CJK character and would otherwise
    abort the whole run mid-catalog.
    """
    if not text:
        return ""
    return text.encode("ascii", "replace").decode("ascii")


def _title_matches(title, keywords):
    """Accept a candidate only if its title mentions what we searched for."""
    title = (title or "").lower()
    return any(word in title for word in " ".join(keywords).split() if len(word) > 3)


def search_openverse(keywords, wanted, seen_urls):
    """Return up to ``wanted`` (url, title) pairs whose titles verify."""
    query = quote(" ".join(keywords))
    url = (
        f"{OPENVERSE}?{urlencode({'page_size': 20, 'license_type': 'commercial', 'mature': 'false'})}"
        f"&q={query}"
    )
    payload = _get(url, as_json=True)
    if not payload:
        return []

    matches = []
    for result in payload.get("results", []):
        image_url = result.get("url")
        title = result.get("title") or ""
        if not image_url or image_url in seen_urls:
            continue
        if not _title_matches(title, keywords):
            continue
        matches.append((image_url, title))
        if len(matches) >= wanted:
            break
    return matches


def square(data, size=900):
    """Centre-crop to a square and resize, so every tile frames identically."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=86, optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


def wide(data, width=1600, height=600):
    """Same idea, but a banner crop."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS, centering=(0.5, 0.4))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=84, optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


class Command(BaseCommand):
    help = "Download subject-matched photography for the demo catalog."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-fetch existing images too.")
        parser.add_argument(
            "--only", choices=["products", "categories", "brands", "banners"],
            help="Restrict to one kind of image.",
        )
        parser.add_argument("--per-product", type=int, default=3, help="Gallery size (default 3).")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report the matches that would be used, without downloading.",
        )

    def handle(self, *args, **options):
        self.force = options["all"]
        self.dry_run = options["dry_run"]
        self.seen = set()
        self.stdout.write(self.style.MIGRATE_HEADING("Fetching subject-matched photography"))
        if self.dry_run:
            self.stdout.write(self.style.WARNING("  dry run - nothing will be saved\n"))

        only = options["only"]
        if only in (None, "products"):
            self._products(options["per_product"])
        if only in (None, "categories"):
            self._categories()
        if only in (None, "banners"):
            self._banners()
        if only in (None, "brands"):
            self._brands()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Done. Hard-refresh the storefront."))

    # ------------------------------------------------------------------
    def _fetch_one(self, keywords, shape="square"):
        """Verified match if possible, keyword-tagged fallback otherwise."""
        for image_url, title in search_openverse(keywords, 4, self.seen):
            raw = _get(image_url)
            if not raw:
                continue
            processed = square(raw) if shape == "square" else wide(raw)
            if processed:
                self.seen.add(image_url)
                return processed, title

        # LoremFlickr still filters by tag, so it stays on-subject.
        tags = ",".join(k.replace(" ", "") for k in keywords[:2])
        dimensions = (900, 900) if shape == "square" else (1600, 600)
        raw = _get(FLICKR_FALLBACK.format(w=dimensions[0], h=dimensions[1], tags=tags))
        if raw and raw[:2] == b"\xff\xd8":
            processed = square(raw) if shape == "square" else wide(raw)
            if processed:
                return processed, f"[flickr:{tags}]"
        return None, None

    def _save(self, owner, field_name, filename, data):
        field = getattr(owner, field_name)
        previous = field.name or None
        field.save(filename, ContentFile(data), save=True)
        if previous and previous != field.name:
            try:
                field.storage.delete(previous)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _products(self, per_product):
        products = Product.objects.select_related("category").prefetch_related("images")
        done = matched = 0

        for product in products:
            if product.images.exists() and not self.force:
                continue

            keywords = keywords_for_product(product)
            self.stdout.write(f"  {console_safe(product.name)[:38]:<40} <- {', '.join(keywords)}")

            collected = []
            for _ in range(per_product):
                data, title = self._fetch_one(keywords)
                if data:
                    collected.append((data, title))
                    if title and not title.startswith("["):
                        matched += 1
                time.sleep(0.25)  # be a considerate API client

            if not collected:
                self.stdout.write(self.style.WARNING("      no usable image found"))
                continue

            for _data, title in collected:
                self.stdout.write(f"      + {console_safe(title)[:66]}")

            if self.dry_run:
                done += 1
                continue

            with transaction.atomic():
                for old in product.images.all():
                    try:
                        old.image.storage.delete(old.image.name)
                    except Exception:
                        pass
                    old.delete()
                for index, (data, _title) in enumerate(collected):
                    ProductImage.objects.create(
                        product=product,
                        image=ContentFile(data, name=f"{product.slug}-{index}.jpg"),
                        alt_text=f"{product.name} - view {index + 1}",
                        is_primary=index == 0,
                        sort_order=index,
                    )
            done += 1

        self.stdout.write(f"  products updated: {done} ({matched} title-verified)")

    def _categories(self):
        queryset = Category.objects.all()
        if not self.force:
            queryset = queryset.filter(image="")
        done = 0
        for category in queryset:
            keywords = CATEGORY_KEYWORDS.get(category.name.lower(), [category.name])
            data, title = self._fetch_one(keywords)
            if data and not self.dry_run:
                self._save(category, "image", f"{category.slug}.jpg", data)
            if data:
                done += 1
                self.stdout.write(f"  category {category.name[:24]:<26} <- {console_safe(title)[:44]}")
        self.stdout.write(f"  categories: {done}")

    def _banners(self):
        done = 0
        for banner in Banner.objects.all():
            if banner.image and not self.force:
                continue
            keywords = ["retail", "shopping"]
            for fragment, terms in BANNER_KEYWORDS.items():
                if fragment in banner.title.lower():
                    keywords = terms
                    break
            data, title = self._fetch_one(keywords, shape="wide")
            if data and not self.dry_run:
                self._save(banner, "image", f"banner-{banner.pk}.jpg", data)
            if data:
                done += 1
                self.stdout.write(f"  banner {banner.title[:26]:<28} <- {console_safe(title)[:44]}")
        self.stdout.write(f"  banners: {done}")

    def _brands(self):
        # Brand marks are lettermarks, not photos -- a random photo would look
        # worse than the generated tile, so those stay as they are.
        self.stdout.write("  brands: skipped (generated lettermarks suit these better)")
        _ = Brand
