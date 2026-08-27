"""Install pg_trgm, which the Postgres search path needs.

``TrigramSimilarity`` compiles to ``SIMILARITY(...)``, a function that only
exists once the pg_trgm extension is created. Without this migration a fresh
PostgreSQL database raises

    ProgrammingError: function similarity(character varying, unknown)
    does not exist

on the first search. ``TrigramExtension`` is a no-op on every other backend,
so this migration is harmless on SQLite.

Creating an extension needs privileges the application role may not have. If
this migration fails with "permission denied to create extension", ask a
superuser to run once, against the project database:

    CREATE EXTENSION IF NOT EXISTS pg_trgm;

Search stays up either way -- apps/catalog/search.py checks for the extension
at query time and drops the trigram term when it is absent.
"""
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        TrigramExtension(),
    ]
