"""Product search.

Uses PostgreSQL full-text search (ranked, stemmed) when the project runs on
Postgres, and falls back to ``Q``-object matching on other engines so the
storefront keeps working in a SQLite dev environment.
"""
from django.db import connection
from django.db.models import Case, F, IntegerField, Q, Value, When


def _is_postgres():
    return connection.vendor == "postgresql"


_trigram_support = {}


def _has_trigram():
    """Is the pg_trgm extension installed on the current database?

    Migration 0002 creates it, but creating an extension needs privileges a
    managed database may withhold, and an older database may predate that
    migration. Asking is cheap and cached; guessing costs a 500 on every
    search. Without it we simply rank by full-text alone.
    """
    if not _is_postgres():
        return False
    alias = connection.alias
    if alias not in _trigram_support:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", ["pg_trgm"])
                _trigram_support[alias] = cursor.fetchone() is not None
        except Exception:
            _trigram_support[alias] = False
    return _trigram_support[alias]


def _fallback_search(queryset, term):
    """Portable search with a hand-rolled relevance ranking.

    Exact name matches rank above prefix matches, which rank above substring
    hits in the description -- roughly what ts_rank would give us.
    """
    lookup = (
        Q(name__icontains=term)
        | Q(short_description__icontains=term)
        | Q(description__icontains=term)
        | Q(tags__icontains=term)
        | Q(sku__icontains=term)
        | Q(brand__name__icontains=term)
        | Q(category__name__icontains=term)
    )
    return (
        queryset.filter(lookup)
        .annotate(
            search_rank=Case(
                When(name__iexact=term, then=Value(100)),
                When(name__istartswith=term, then=Value(80)),
                When(name__icontains=term, then=Value(60)),
                When(brand__name__icontains=term, then=Value(40)),
                When(category__name__icontains=term, then=Value(30)),
                When(tags__icontains=term, then=Value(20)),
                default=Value(10),
                output_field=IntegerField(),
            )
        )
        .distinct()
    )


def _postgres_search(queryset, term):
    from django.contrib.postgres.search import (
        SearchQuery,
        SearchRank,
        SearchVector,
        TrigramSimilarity,
    )

    vector = (
        SearchVector("name", weight="A")
        + SearchVector("brand__name", weight="B")
        + SearchVector("tags", weight="B")
        + SearchVector("short_description", weight="C")
        + SearchVector("description", weight="D")
    )
    query = SearchQuery(term, search_type="websearch")

    # Trigram similarity catches misspellings, but only where pg_trgm is
    # installed. Rank by full-text alone when it is not, rather than emitting
    # SQL the database cannot execute.
    rank = SearchRank(vector, query)
    if _has_trigram():
        rank = rank + TrigramSimilarity("name", term)

    return (
        queryset.annotate(search_rank=rank)
        .filter(Q(search_rank__gt=0.05) | Q(sku__iexact=term))
        .distinct()
    )


def search_products(queryset, term):
    """Filter ``queryset`` by ``term`` and annotate ``search_rank``.

    An empty term returns the queryset untouched (with a zero rank so callers
    can order by the same field either way).
    """
    term = (term or "").strip()
    if not term:
        return queryset.annotate(search_rank=Value(0, output_field=IntegerField()))

    if _is_postgres():
        try:
            return _postgres_search(queryset, term)
        except Exception:
            # pg_trgm may not be installed; degrade rather than 500.
            pass
    return _fallback_search(queryset, term)


def autocomplete_suggestions(limit=8, term=""):
    """Lightweight suggestions for the header search box (HTMX endpoint)."""
    from .models import Product

    term = (term or "").strip()
    if len(term) < 2:
        return []

    products = (
        Product.objects.published()
        .filter(Q(name__icontains=term) | Q(brand__name__icontains=term))
        .select_related("brand")
        .prefetch_related("images")
        .order_by(F("sold_count").desc())[:limit]
    )
    return list(products)
