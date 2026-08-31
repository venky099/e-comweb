"""Bulk product import from a CSV file (MST spec section 38).

    python manage.py import_products catalogue.csv            # dry run
    python manage.py import_products catalogue.csv --commit   # write
    python manage.py import_products --template > example.csv

Defaults to a dry run. An import that writes by default is one somebody runs
against production while meaning to check the file first.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog import importer


class Command(BaseCommand):
    help = "Import products from a CSV file. Dry run unless --commit is given."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", help="Path to the CSV file.")
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually write. Without it the file is only validated.",
        )
        parser.add_argument(
            "--template",
            action="store_true",
            help="Print a sample CSV with the expected columns.",
        )

    def handle(self, *args, **options):
        if options["template"]:
            self.stdout.write(importer.template_csv())
            return

        path = options["path"]
        if not path:
            raise CommandError("Give a CSV path, or --template to see the format.")

        source = Path(path)
        if not source.exists():
            raise CommandError(f"No such file: {source}")

        with source.open("rb") as handle:
            report = (
                importer.commit(handle) if options["commit"] else importer.preview(handle)
            )

        if report.errors:
            self.stdout.write(self.style.ERROR(f"\n{len(report.errors)} problem(s):"))
            for row_number, message in report.errors[:25]:
                where = f"row {row_number}" if row_number else "file"
                self.stdout.write(f"  {where}: {message}")
            if len(report.errors) > 25:
                self.stdout.write(f"  ... and {len(report.errors) - 25} more")
            self.stdout.write(
                self.style.WARNING("\nNothing was imported. Fix the file and try again.")
            )
            return

        self.stdout.write(self.style.SUCCESS(f"\n{report.summary()}"))
        if report.dry_run:
            self.stdout.write("  Dry run -- nothing was written. Add --commit to apply.")
