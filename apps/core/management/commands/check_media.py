"""Diagnose missing or broken images.

    python manage.py check_media

Answers the question "why can't I see the product images?" without anyone
having to guess. Missing images have several distinct causes that look
identical in a browser, and each needs a different fix:

* no image rows in the database          -> seed_data was never run
* rows exist but the files are gone      -> media/ is gitignored, so it is
                                            never delivered by git pull
* files exist but DEBUG is off           -> the dev server will not serve them
* everything present                     -> a browser cache or a template bug

Read-only. It never changes anything, it just reports and prescribes.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.catalog.models import Brand, Category, Product, ProductImage
from apps.marketing.models import Banner


def file_present(field):
    if not field:
        return False
    try:
        return field.storage.exists(field.name)
    except Exception:
        return False


class Command(BaseCommand):
    help = "Report why product images may not be displaying."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Media diagnostic"))
        problems = []

        # ---- configuration -------------------------------------------
        media_root = Path(settings.MEDIA_ROOT)
        self.stdout.write("\nConfiguration")
        self.stdout.write(f"  DEBUG              {settings.DEBUG}")
        self.stdout.write(f"  MEDIA_URL          {settings.MEDIA_URL}")
        self.stdout.write(f"  MEDIA_ROOT         {media_root}")
        self.stdout.write(f"  directory exists   {media_root.exists()}")

        if not settings.DEBUG:
            problems.append(
                "DEBUG is off, so the dev server does not serve /media/. "
                "Run with config.settings.dev, or serve media through your web server."
            )
        if not media_root.exists():
            problems.append(
                f"{media_root} does not exist -- no images have been generated yet."
            )

        on_disk = (
            len([p for p in media_root.rglob("*") if p.is_file()])
            if media_root.exists()
            else 0
        )
        self.stdout.write(f"  files on disk      {on_disk}")

        # ---- products ------------------------------------------------
        products = Product.objects.prefetch_related("images")
        total = products.count()
        no_rows, broken_files, healthy = [], [], 0

        for product in products:
            images = list(product.images.all())
            if not images:
                no_rows.append(product.name)
            elif not all(file_present(i.image) for i in images):
                broken_files.append(product.name)
            else:
                healthy += 1

        self.stdout.write("\nProducts")
        self.stdout.write(f"  total                     {total}")
        self.stdout.write(self.style.SUCCESS(f"  with working images       {healthy}"))
        if no_rows:
            self.stdout.write(self.style.WARNING(f"  with no image records     {len(no_rows)}"))
            for name in no_rows[:5]:
                self.stdout.write(f"      {name[:56]}")
            if len(no_rows) > 5:
                self.stdout.write(f"      ... and {len(no_rows) - 5} more")
        if broken_files:
            self.stdout.write(self.style.ERROR(f"  records but missing files {len(broken_files)}"))
            for name in broken_files[:5]:
                self.stdout.write(f"      {name[:56]}")
            if len(broken_files) > 5:
                self.stdout.write(f"      ... and {len(broken_files) - 5} more")

        # ---- everything else that carries an image -------------------
        self.stdout.write("\nOther images")
        incomplete, notes = [], []
        for label, queryset, attribute in (
            ("categories", Category.objects.all(), "image"),
            ("brands", Brand.objects.all(), "logo"),
            ("banners", Banner.objects.all(), "image"),
        ):
            rows = list(queryset)
            present = sum(1 for row in rows if file_present(getattr(row, attribute)))
            style = self.style.SUCCESS if present == len(rows) else self.style.WARNING
            self.stdout.write(style(f"  {label:<12} {present}/{len(rows)} with files"))
            if rows and present < len(rows):
                # Banners render a bare placeholder box without an image, so a
                # gap there is a real defect. Categories and brands fall back
                # to an icon and a lettermark respectively, which look
                # deliberate -- worth reporting, not worth alarming about.
                (incomplete if label == "banners" else notes).append(
                    f"{len(rows) - present} {label}"
                )

        # ---- verdict -------------------------------------------------
        if total == 0:
            problems.append("No products at all -- run: python manage.py seed_data")
        elif no_rows:
            problems.append(
                f"{len(no_rows)} product(s) have no images -- run: python manage.py seed_data"
            )
        if broken_files:
            problems.append(
                f"{len(broken_files)} product(s) reference files that are not on disk. "
                "media/ is gitignored, so git pull never delivers it. "
                "Run: python manage.py seed_data"
            )
        if incomplete:
            problems.append(
                "Missing images for " + ", ".join(incomplete)
                + " -- run: python manage.py seed_data"
            )
        if notes:
            self.stdout.write(
                "\n  Note: " + ", ".join(notes) + " have no image and fall back to "
                "an icon or lettermark. That is by design, not a fault."
            )

        self.stdout.write("")
        if problems:
            self.stdout.write(self.style.ERROR("Problems found:"))
            for index, problem in enumerate(problems, 1):
                self.stdout.write(f"  {index}. {problem}")
            self.stdout.write(
                "\n  For real photography instead of generated tiles:"
                "\n    python manage.py fetch_images"
            )
        else:
            self.stdout.write(self.style.SUCCESS("Everything checks out."))
            sample = ProductImage.objects.first()
            if sample:
                self.stdout.write(f"  Sample image URL: {sample.image.url}")
            self.stdout.write(
                "  If images still do not appear, it is a browser cache -- "
                "hard-refresh with Ctrl+F5."
            )
